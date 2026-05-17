import asyncio
import json
import logging
import os
import queue as _queue
import threading
import time
import uuid
from contextlib import asynccontextmanager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Fix #1：load_dotenv 必須在所有 chat_bot 模組 import 前呼叫，
# 否則 GlobalConfig 的 os.environ.get() 讀不到 .env 的值
from dotenv import load_dotenv
load_dotenv()

from fastapi import Depends, FastAPI, HTTPException, Request, Security, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.security.api_key import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from langchain_core.messages import HumanMessage, AIMessage
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import TextSendMessage
from pydantic import BaseModel

from chat_bot.global_config import GlobalConfig
from chat_bot.llm_processor import LLMProcessor

# ── 時辰對照表 ────────────────────────────────────────────────
TIME_TABLE = [
    {"zhi": "子", "range": "23:00-00:59", "hour": 0},
    {"zhi": "丑", "range": "01:00-02:59", "hour": 2},
    {"zhi": "寅", "range": "03:00-04:59", "hour": 4},
    {"zhi": "卯", "range": "05:00-06:59", "hour": 6},
    {"zhi": "辰", "range": "07:00-08:59", "hour": 8},
    {"zhi": "巳", "range": "09:00-10:59", "hour": 10},
    {"zhi": "午", "range": "11:00-12:59", "hour": 12},
    {"zhi": "未", "range": "13:00-14:59", "hour": 14},
    {"zhi": "申", "range": "15:00-16:59", "hour": 16},
    {"zhi": "酉", "range": "17:00-18:59", "hour": 18},
    {"zhi": "戌", "range": "19:00-20:59", "hour": 20},
    {"zhi": "亥", "range": "21:00-22:59", "hour": 22},
]

# ── 應用程式狀態 ──────────────────────────────────────────────
_pipeline = None
_llm = None  # Bug #5 Fix: singleton LLM reused across streaming requests (avoid rebuilding per request)
_line_bot_api: "LineBotApi | None" = None   # Fix #9：lazy singleton
_line_handler: "WebhookHandler | None" = None  # Fix #9：lazy singleton
SESSION_STATS: dict = {}
HOROSCOPE: dict = {}
CHART_TABLE: dict = {}
BIRTH_INFO: dict = {}
AGENT_THREAD: dict = {}
NON_AGENT_THREAD: dict = {}    # Bug #1 Fix: non-agent 模式的 LangGraph thread_id，重置時換新 UUID
_SESSION_LAST_SEEN: dict = {}  # Fix #5: session 最後活躍時間戳
USER_PROFILES: dict = {}       # 用戶 playbook 快取（key: session_id, value: md 字串）
# Per-session asyncio.Lock — prevents concurrent requests for the same session_id from
# running the pipeline simultaneously and causing TOCTOU races on HOROSCOPE/SESSION_STATS.
# NOTE: asyncio.Lock must be created lazily inside the running event loop.
# We use a plain dict protected by an asyncio.Lock for the dict itself.
# _SESSION_LOCKS_META_LOCK is initialised once inside the lifespan (event-loop context).
_SESSION_LOCKS: dict = {}
_SESSION_LOCKS_META_LOCK: "asyncio.Lock | None" = None  # set in lifespan


async def _get_session_lock(session_id: str) -> asyncio.Lock:
    """Return (or create) the asyncio.Lock for this session_id (coroutine-safe)."""
    global _SESSION_LOCKS_META_LOCK
    if _SESSION_LOCKS_META_LOCK is None:
        # Should not happen after lifespan runs, but guard anyway.
        _SESSION_LOCKS_META_LOCK = asyncio.Lock()
    async with _SESSION_LOCKS_META_LOCK:
        if session_id not in _SESSION_LOCKS:
            _SESSION_LOCKS[session_id] = asyncio.Lock()
        return _SESSION_LOCKS[session_id]

# 每個 session 最多保留的訊息輪數（fix #9：防止無限成長）
_MAX_HISTORY = 20
# Fix #5：session 閒置超過此秒數就清除（預設 2 小時）
_SESSION_TTL_SECS = 7200


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pipeline, _llm, _SESSION_LOCKS_META_LOCK
    from chat_bot.utils.utils import build_llm
    from chat_bot.auth.auth import init_db, sync_whitelist
    # Initialise the meta-lock inside the event loop so all asyncio.Lock objects
    # created later are bound to the same loop.
    _SESSION_LOCKS_META_LOCK = asyncio.Lock()
    init_db()
    sync_whitelist()
    _pipeline = LLMProcessor().set_pipeline()
    _llm = build_llm(GlobalConfig.MODEL)  # Bug #5 Fix: build once, reuse in _stream_worker
    yield


app = FastAPI(title="紫微斗數 API", lifespan=lifespan)
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# ── API Key 驗證 ──────────────────────────────────────────────
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def verify_api_key(api_key: str = Security(_api_key_header)):
    """若 GlobalConfig.API_KEY 有設定，則強制驗證；否則放行。"""
    if not GlobalConfig.API_KEY:
        return
    if api_key != GlobalConfig.API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )


# ── Request / Response schemas ────────────────────────────────
class ChatRequest(BaseModel):
    message: str
    session_id: str = "web_default"


class ResetRequest(BaseModel):
    session_id: str = "web_default"


class RegisterRequest(BaseModel):
    username: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


# ── 工具函式 ──────────────────────────────────────────────────
def _extract_last_message(output: dict) -> str:
    """Fix #10：output["messages"] 為空時回傳預設提示，而非 IndexError。"""
    msgs = output.get("messages", [])
    if not msgs:
        return "⚠️ 系統未回應，請稍後再試。"
    last = msgs[-1]
    return last["content"] if isinstance(last, dict) else last.content


def _trim_history(session_id: str) -> None:
    """超過上限時，保留最新的 _MAX_HISTORY 條訊息。"""
    msgs = SESSION_STATS.get(session_id, [])
    if len(msgs) > _MAX_HISTORY:
        SESSION_STATS[session_id] = msgs[-_MAX_HISTORY:]


def _pipeline_messages(session_id: str) -> list:
    """Bug #10 Fix: 當命盤已存在時，只傳最近 12 則訊息給 pipeline。
    命盤初期收集資料的訊息（出生資料問答）對後續對話無用，且命盤已透過 horoscope 傳遞，
    只保留近期對話即可，減少 context 浪費。"""
    msgs = SESSION_STATS.get(session_id, [])
    if HOROSCOPE.get(session_id):
        # 命盤已生成：只取最近 12 則，節省 context
        return msgs[-12:] if len(msgs) > 12 else msgs
    return msgs


def _touch_session(session_id: str) -> None:
    """Fix #5：更新 session 活躍時間。"""
    _SESSION_LAST_SEEN[session_id] = time.time()


def _evict_stale_sessions() -> None:
    """Fix #5：清除超過 TTL 的閒置 sessions，防止記憶體無限成長。"""
    now = time.time()
    # Bug R4#1 Fix: 先 list() 快照，避免另一個 coroutine 在迭代過程中修改 dict 造成 RuntimeError
    stale = [sid for sid, ts in list(_SESSION_LAST_SEEN.items()) if now - ts > _SESSION_TTL_SECS]
    for sid in stale:
        SESSION_STATS.pop(sid, None)
        HOROSCOPE.pop(sid, None)
        CHART_TABLE.pop(sid, None)
        BIRTH_INFO.pop(sid, None)
        AGENT_THREAD.pop(sid, None)
        NON_AGENT_THREAD.pop(sid, None)
        _SESSION_LAST_SEEN.pop(sid, None)
        USER_PROFILES.pop(sid, None)
        # Fix: 同步清除 asyncio.Lock，避免 _SESSION_LOCKS 無限增長（記憶體洩漏）
        _SESSION_LOCKS.pop(sid, None)


def _playbook_conv(session_id: str, n: int = 5) -> str:
    """回傳最近 n 則用戶訊息，作為 update_playbook 的對話上下文。"""
    msgs = SESSION_STATS.get(session_id, [])
    user_texts = [m.content for m in msgs if isinstance(m, HumanMessage)][-n:]
    return "\n".join(f"用戶: {t}" for t in user_texts)


# ── 行銷首頁 ──────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    line_id = GlobalConfig.LINE_BOT_ID or "@ziwei_ai"
    qr_url = f"https://qr-official.line.me/gs/M_{line_id.lstrip('@')}_BW.png"
    return templates.TemplateResponse(
        request,
        "index.html",
        {"request": request, "line_id": line_id, "qr_url": qr_url, "time_table": TIME_TABLE},
    )


# ── 網頁聊天介面 ───────────────────────────────────────────────
@app.get("/chat", response_class=HTMLResponse)
def chat_page(request: Request):
    return templates.TemplateResponse(
        request,
        "chat.html",
        {"request": request, "time_table": TIME_TABLE},
    )


# ── 帳戶驗證 API ──────────────────────────────────────────────
@app.post("/api/auth/register")
async def api_register(body: RegisterRequest):
    if not GlobalConfig.ALLOW_REGISTER:
        raise HTTPException(status_code=403, detail="目前不開放自行註冊")
    from chat_bot.auth.auth import create_user, create_token
    username = body.username.strip()
    password = body.password
    if len(username) < 3 or len(username) > 20:
        raise HTTPException(status_code=400, detail="帳號長度需為 3-20 字元")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="密碼至少需要 6 個字元")
    user = create_user(username, password)
    if user is None:
        raise HTTPException(status_code=409, detail="帳號已被使用")
    token = create_token(user["username"], user["session_id"])
    return {"token": token, "username": user["username"], "session_id": user["session_id"]}


@app.post("/api/auth/login")
async def api_login(body: LoginRequest):
    from chat_bot.auth.auth import authenticate_user, create_token
    user = authenticate_user(body.username.strip(), body.password)
    if user is None:
        raise HTTPException(status_code=401, detail="帳號或密碼錯誤")
    token = create_token(user["username"], user["session_id"])
    return {"token": token, "username": user["username"], "session_id": user["session_id"]}


@app.get("/api/auth/me")
async def api_me(request: Request):
    from chat_bot.auth.auth import verify_token
    auth_header = request.headers.get("Authorization", "")
    token = auth_header.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="未登入")
    user = verify_token(token)
    if user is None:
        raise HTTPException(status_code=401, detail="Token 已過期或無效")
    return {"username": user["username"], "session_id": user["session_id"]}


# ── 網頁聊天 API ───────────────────────────────────────────────
@app.post("/api/chat", dependencies=[Depends(verify_api_key)])
async def api_chat(body: ChatRequest):
    """
    網頁版聊天 API。需在 Header 帶 `X-API-Key`（若伺服器有設定 API_KEY）。
    Request body: { "message": "...", "session_id": "..." }
    Response:     { "reply": "..." }
    Bug #2 Fix: 改為 async def，pipeline.invoke 用 run_in_threadpool 避免阻塞 event loop
    """
    # Fix #2：pipeline 尚未就緒時提早返回
    if _pipeline is None:
        return JSONResponse(status_code=503, content={"reply": "⚠️ 服務啟動中，請稍後再試。"})

    t0 = time.time()
    try:
        msg = body.message.strip()
        # Bug #10 Fix: strip whitespace before fallback so "   " doesn't become a phantom session
        session_id = (body.session_id or "").strip() or "web_default"
        logger.info(f"[api/chat] session={session_id} msg={msg[:40]!r}")

        if not msg:
            return {"reply": "請輸入訊息。"}

        # Bug #3 Fix: 精確匹配，避免包含 "/reset" 的一般訊息（如「什麼是 /reset？」）誤觸重置
        is_reset = msg.strip() == "/reset"

        _evict_stale_sessions()
        _touch_session(session_id)

        from chat_bot.utils.user_playbook import load_playbook, update_playbook
        user_profile = USER_PROFILES.get(session_id) or load_playbook(session_id)
        USER_PROFILES[session_id] = user_profile

        # Fix: 取得 per-session lock，防止同一 session 並發請求造成 race condition
        session_lock = await _get_session_lock(session_id)
        async with session_lock:
            # 記憶體沒有命盤時從 DB 補載
            if not HOROSCOPE.get(session_id) and not is_reset and not GlobalConfig.USE_AGENT_SKILL:
                from chat_bot.auth.auth import load_horoscope
                _h, _ct, _bi = await run_in_threadpool(load_horoscope, session_id)
                if _h:
                    HOROSCOPE[session_id] = _h
                    CHART_TABLE[session_id] = _ct or ""
                    if _bi:
                        BIRTH_INFO[session_id] = _bi

            if GlobalConfig.USE_AGENT_SKILL:
                if is_reset or session_id not in AGENT_THREAD:
                    AGENT_THREAD[session_id] = str(uuid.uuid4())
                    if is_reset:
                        return {"reply": "✅ 對話記憶已重置！" if HOROSCOPE.get(session_id) else "✅ 對話已重置！請重新告訴我你的出生年月日、時辰和性別。"}

                config = {"configurable": {"thread_id": AGENT_THREAD[session_id]}}
                output = await run_in_threadpool(
                    _pipeline.invoke, {"messages": [HumanMessage(content=msg)]}, config
                )
                reply = _extract_last_message(output)

            else:
                if is_reset:
                    NON_AGENT_THREAD[session_id] = str(uuid.uuid4())
                    SESSION_STATS[session_id] = []
                    HOROSCOPE.pop(session_id, None)
                    CHART_TABLE.pop(session_id, None)
                    BIRTH_INFO.pop(session_id, None)
                    return {"reply": "✅ 對話與命盤已重置！請重新告訴我你的出生年月日、時辰和性別，我來幫你排盤。"}
                elif session_id not in HOROSCOPE:
                    NON_AGENT_THREAD[session_id] = str(uuid.uuid4())
                    HOROSCOPE[session_id] = ""
                    SESSION_STATS[session_id] = []

                thread_id = NON_AGENT_THREAD.setdefault(session_id, str(uuid.uuid4()))
                config = {"configurable": {"thread_id": thread_id, "session_id": session_id}}

                SESSION_STATS[session_id].append(HumanMessage(content=msg))
                _trim_history(session_id)

                output = await run_in_threadpool(
                    _pipeline.invoke,
                    {"messages": _pipeline_messages(session_id), "horoscope": HOROSCOPE[session_id], "user_profile": user_profile},
                    config,
                )
                if not HOROSCOPE[session_id]:
                    HOROSCOPE[session_id] = output.get("horoscope", "")
                    CHART_TABLE[session_id] = output.get("chart_table", "")
                    _bi = output.get("birth_info") or {}
                    if _bi:
                        BIRTH_INFO[session_id] = _bi
                    if HOROSCOPE[session_id]:
                        from chat_bot.auth.auth import save_horoscope
                        threading.Thread(
                            target=save_horoscope,
                            args=(session_id, HOROSCOPE[session_id], CHART_TABLE.get(session_id, ""), BIRTH_INFO.get(session_id)),
                            daemon=True,
                        ).start()

                reply = _extract_last_message(output)
                SESSION_STATS[session_id].append(AIMessage(content=reply))

        # 背景更新 user playbook（不阻塞回應）
        if not is_reset and _llm is not None:
            _llm_ref = _llm
            _sid = session_id
            _conv = _playbook_conv(session_id)
            import threading
            threading.Thread(
                target=update_playbook,
                args=(_sid, _conv, _llm_ref),
                daemon=True,
            ).start()
            USER_PROFILES.pop(session_id, None)  # 下次請求時從磁碟重新載入最新資料

        logger.info(f"[api/chat] done in {time.time()-t0:.1f}s")
        return {"reply": reply}

    except Exception as e:
        logger.exception("[api/chat] error")
        if "RESOURCE_EXHAUSTED" in str(e) or "429" in str(e):
            return JSONResponse(status_code=429, content={"reply": "⚠️ AI 服務目前請求過多，請稍後再試。"})
        return JSONResponse(status_code=500, content={"reply": "⚠️ 發生錯誤，請稍後再試。"})


# ── 重置 API ──────────────────────────────────────────────────
@app.post("/api/reset", dependencies=[Depends(verify_api_key)])
async def api_reset(body: ResetRequest):
    """清除指定 session 的對話記憶與命盤，讓使用者可以重新排盤。"""
    session_id = (body.session_id or "").strip() or "web_default"
    SESSION_STATS.pop(session_id, None)
    HOROSCOPE.pop(session_id, None)
    CHART_TABLE.pop(session_id, None)
    BIRTH_INFO.pop(session_id, None)
    AGENT_THREAD.pop(session_id, None)
    NON_AGENT_THREAD.pop(session_id, None)
    _SESSION_LAST_SEEN.pop(session_id, None)
    USER_PROFILES.pop(session_id, None)
    _SESSION_LOCKS.pop(session_id, None)
    return {"status": "ok"}


@app.post("/api/clear-chart", dependencies=[Depends(verify_api_key)])
async def api_clear_chart(body: ResetRequest):
    """只清除命盤資料（HOROSCOPE / CHART_TABLE / BIRTH_INFO + 磁碟檔案），保留對話記憶。
    用於「排新命盤」：讓使用者為不同人排盤，不需清除對話歷史。
    關鍵：必須刪除磁碟上的 horoscope 檔案，否則下次請求會從磁碟重載舊命盤。
    同時重置 NON_AGENT_THREAD，讓 LangGraph state 回到初始（is_fortune=False）。"""
    session_id = (body.session_id or "").strip() or "web_default"
    HOROSCOPE.pop(session_id, None)
    CHART_TABLE.pop(session_id, None)
    BIRTH_INFO.pop(session_id, None)
    SESSION_STATS.pop(session_id, None)
    USER_PROFILES.pop(session_id, None)
    # Reset conversation thread so LangGraph starts fresh (birth_info / is_fortune reset)
    NON_AGENT_THREAD.pop(session_id, None)
    # Delete horoscope file — without this, the next request would reload the old chart from disk
    from chat_bot.auth.auth import delete_horoscope
    await run_in_threadpool(delete_horoscope, session_id)
    return {"status": "ok"}


# ── 命盤表格 API ──────────────────────────────────────────────
@app.get("/api/chart", dependencies=[Depends(verify_api_key)])
async def api_chart(session_id: str = "web_default"):
    """回傳指定 session 的命盤 Markdown 表格與出生資料（先查記憶體，再查 DB）。"""
    session_id = (session_id or "").strip() or "web_default"
    chart = CHART_TABLE.get(session_id, "")
    birth_info = BIRTH_INFO.get(session_id)
    if not chart:
        from chat_bot.auth.auth import load_horoscope
        _, ct, bi = await run_in_threadpool(load_horoscope, session_id)
        chart = ct or ""
        if bi and not birth_info:
            birth_info = bi
            BIRTH_INFO[session_id] = bi
    return {"chart": chart, "birth_info": birth_info}


# ── 對話歷史 API ──────────────────────────────────────────────
@app.get("/api/history")
async def api_history(session_id: str = "web_default", limit: int = 30, offset: int = 0):
    """回傳指定 session 的聊天歷史（最多 limit 筆，依時間升序）。"""
    session_id = (session_id or "").strip() or "web_default"
    limit = max(1, min(limit, 100))
    from chat_bot.auth.auth import load_chat_history
    messages = await run_in_threadpool(load_chat_history, session_id, limit, offset)
    return {"messages": messages, "has_more": len(messages) == limit}


@app.delete("/api/history")
async def api_history_delete(session_id: str = "web_default"):
    """清除指定 session 的聊天歷史。"""
    session_id = (session_id or "").strip() or "web_default"
    from chat_bot.auth.auth import clear_chat_history
    await run_in_threadpool(clear_chat_history, session_id)
    return {"status": "ok"}


# ── 串流聊天 API ───────────────────────────────────────────────
@app.post("/api/chat/stream", dependencies=[Depends(verify_api_key)])
async def api_chat_stream(body: ChatRequest):
    """
    SSE 串流版聊天 API。
    - 已有命盤的 session：逐 token 串流回傳 LLM 回應
    - 尚未排盤的 session：一次性回傳（等待排盤完成）
    Response format: text/event-stream
      data: {"token": "..."}\n\n   — 每個 token
      data: {"done": true}\n\n     — 結束
    """
    if _pipeline is None:
        async def _not_ready():
            yield f'data: {json.dumps({"token": "⚠️ 服務啟動中，請稍後再試。"})}\n\n'
            yield f'data: {json.dumps({"done": True})}\n\n'
        return StreamingResponse(_not_ready(), media_type="text/event-stream")

    # Bug #10 Fix: strip whitespace before fallback so "   " doesn't become a phantom session
    session_id = (body.session_id or "").strip() or "web_default"
    msg = body.message.strip()

    if not msg:
        async def _empty():
            yield f'data: {json.dumps({"token": "請輸入訊息。"})}\n\n'
            yield f'data: {json.dumps({"done": True})}\n\n'
        return StreamingResponse(_empty(), media_type="text/event-stream")

    _evict_stale_sessions()
    _touch_session(session_id)
    # Bug #3 Fix: 精確匹配避免一般訊息含 "/reset" 誤觸重置
    is_reset = msg.strip() == "/reset"

    from chat_bot.utils.user_playbook import load_playbook, update_playbook
    user_profile = USER_PROFILES.get(session_id) or load_playbook(session_id)
    USER_PROFILES[session_id] = user_profile

    # Fix: 取得 per-session lock，防止同一 session 並發請求造成 race condition
    session_lock = await _get_session_lock(session_id)

    # ── 若尚未有命盤 or 需要重置，走一般 pipeline（非串流）─────────
    async with session_lock:
        # 記憶體沒有命盤時從 DB 補載（伺服器重啟後不需重新排盤）
        if not HOROSCOPE.get(session_id) and not is_reset:
            from chat_bot.auth.auth import load_horoscope
            _h, _ct, _bi = await run_in_threadpool(load_horoscope, session_id)
            if _h:
                HOROSCOPE[session_id] = _h
                CHART_TABLE[session_id] = _ct or ""
                if _bi:
                    BIRTH_INFO[session_id] = _bi

        horoscope_ready = (
            session_id in HOROSCOPE
            and bool(HOROSCOPE[session_id])
            and not is_reset
            and not GlobalConfig.USE_AGENT_SKILL
        )

        if not horoscope_ready:
            # ── 在 lock 內完成所有狀態設定，定義 _run closure ────────────
            _reset_msg = None

            if GlobalConfig.USE_AGENT_SKILL:
                if is_reset or session_id not in AGENT_THREAD:
                    AGENT_THREAD[session_id] = str(uuid.uuid4())
                    if is_reset:
                        _reset_msg = "✅ 對話已重置！請重新告訴我你的出生年月日、時辰和性別。"
                if not _reset_msg:
                    _ag_config = {"configurable": {"thread_id": AGENT_THREAD[session_id]}}
                    def _run():
                        output = _pipeline.invoke({"messages": [HumanMessage(content=msg)]}, _ag_config)
                        return _extract_last_message(output)
            else:
                if is_reset:
                    NON_AGENT_THREAD[session_id] = str(uuid.uuid4())
                    SESSION_STATS[session_id] = []
                    HOROSCOPE.pop(session_id, None)
                    CHART_TABLE.pop(session_id, None)
                    BIRTH_INFO.pop(session_id, None)
                    _reset_msg = "✅ 對話與命盤已重置！請重新告訴我你的出生年月日、時辰和性別，我來幫你排盤。"
                elif session_id not in HOROSCOPE:
                    NON_AGENT_THREAD[session_id] = str(uuid.uuid4())
                    HOROSCOPE[session_id] = ""
                    CHART_TABLE[session_id] = ""
                    SESSION_STATS[session_id] = []
                if not _reset_msg:
                    SESSION_STATS.setdefault(session_id, [])
                    SESSION_STATS[session_id].append(HumanMessage(content=msg))
                    _trim_history(session_id)
                    _tid = NON_AGENT_THREAD.setdefault(session_id, str(uuid.uuid4()))
                    _cfg = {"configurable": {"thread_id": _tid, "session_id": session_id}}
                    _msgs_snap = _pipeline_messages(session_id)
                    _horoscope_snap = HOROSCOPE[session_id]
                    def _run():
                        output = _pipeline.invoke(
                            {"messages": _msgs_snap, "horoscope": _horoscope_snap, "user_profile": user_profile},
                            config=_cfg,
                        )
                        if not HOROSCOPE[session_id]:
                            HOROSCOPE[session_id] = output.get("horoscope", "")
                            CHART_TABLE[session_id] = output.get("chart_table", "")
                            _bi = output.get("birth_info") or {}
                            if _bi:
                                BIRTH_INFO[session_id] = _bi
                            # 命盤生成後存入 DB，伺服器重啟後不需重新排盤
                            if HOROSCOPE[session_id]:
                                from chat_bot.auth.auth import save_horoscope
                                save_horoscope(session_id, HOROSCOPE[session_id], CHART_TABLE.get(session_id, ""), BIRTH_INFO.get(session_id))
                        r = _extract_last_message(output)
                        SESSION_STATS[session_id].append(AIMessage(content=r))
                        return r

            # ── 重置：立即回應 ─────────────────────────────────────────
            if _reset_msg is not None:
                async def _rst_gen():
                    yield f'data: {json.dumps({"token": _reset_msg})}\n\n'
                    yield f'data: {json.dumps({"done": True})}\n\n'
                return StreamingResponse(_rst_gen(), media_type="text/event-stream")

            # ── 非重置：return 會退出 async with，釋放 lock；
            #   generator 在 FastAPI 迭代時才真正執行（lock 已釋放）────────
            async def _pipeline_stream():
                # 立即送出 loading 訊息，瀏覽器馬上顯示
                yield f'data: {json.dumps({"token": "⏳ 正在排命盤中，大約需要兩到三分鐘，請稍後..."})}\n\n'
                try:
                    reply = await run_in_threadpool(_run)
                except Exception as exc:
                    if "RESOURCE_EXHAUSTED" in str(exc) or "429" in str(exc):
                        reply = "⚠️ AI 服務目前請求過多，請稍後再試。"
                    else:
                        reply = "⚠️ 發生錯誤，請稍後再試。"
                    logger.exception("[stream] pipeline error")
                if _llm is not None:
                    _lref = _llm
                    _conv_snap = _playbook_conv(session_id)
                    threading.Thread(
                        target=update_playbook,
                        args=(session_id, _conv_snap, _lref),
                        daemon=True,
                    ).start()
                    USER_PROFILES.pop(session_id, None)
                chart_md = CHART_TABLE.get(session_id, "")
                # clear 事件讓前端清除 loading 訊息，再顯示實際回應
                yield f'data: {json.dumps({"clear": True})}\n\n'
                yield f'data: {json.dumps({"token": reply})}\n\n'
                if chart_md:
                    yield f'data: {json.dumps({"chart": chart_md, "birth_info": BIRTH_INFO.get(session_id)})}\n\n'
                yield f'data: {json.dumps({"done": True})}\n\n'
                # 背景儲存排盤對話到 chat_history DB
                from chat_bot.auth.auth import save_chat_message as _save_msg
                threading.Thread(target=_save_msg, args=(session_id, "user", msg), daemon=True).start()
                threading.Thread(target=_save_msg, args=(session_id, "ai", reply), daemon=True).start()

            return StreamingResponse(_pipeline_stream(), media_type="text/event-stream")

        # ── 已有命盤，串流 chat 回應 ──────────────────────────────────
        from chat_bot.node.chat import chat_stream

        SESSION_STATS.setdefault(session_id, []).append(HumanMessage(content=msg))
        _trim_history(session_id)

        import datetime as _dt_now
        messages_snapshot = list(SESSION_STATS[session_id])
        horoscope_snapshot = HOROSCOPE[session_id]
        chart_table_snapshot = CHART_TABLE.get(session_id, "")
        current_year_snapshot = _dt_now.datetime.now().year
        user_profile_snapshot = user_profile

    # lock 釋放後才開串流，避免 lock 被長時間持有（串流期間可接受下一個請求排隊）
    token_q: _queue.Queue = _queue.Queue()
    # Fix: cancel_event 讓 _stream_worker 在用戶斷線後提早退出，避免繼續消耗 API quota
    cancel_event = threading.Event()

    def _stream_worker():
        try:
            # Bug R4#6 Fix: guard against _llm being None (e.g. lifespan startup failed)
            if _llm is None:
                token_q.put("⚠️ 服務尚未就緒，請稍後再試。")
                return
            for token in chat_stream(messages_snapshot, horoscope_snapshot, _llm, chart_table_snapshot, current_year_snapshot, user_profile_snapshot):  # Bug #5 Fix: reuse singleton
                if cancel_event.is_set():
                    break
                token_q.put(token)
        except Exception:
            logger.exception("[stream] chat_stream error")
            # Bug #8 Fix: 將錯誤訊息送入 queue，前端可顯示而非留空白 bubble
            if not cancel_event.is_set():
                token_q.put("⚠️ 回應時發生錯誤，請稍後再試。")
        finally:
            token_q.put(None)  # sentinel

    t = threading.Thread(target=_stream_worker, daemon=True)
    t.start()

    async def _token_generator():
        full_reply: list[str] = []
        loop = asyncio.get_running_loop()
        try:
            while True:
                token = await loop.run_in_executor(None, token_q.get)
                if token is None:
                    break
                full_reply.append(token)
                yield f'data: {json.dumps({"token": token})}\n\n'

            yield f'data: {json.dumps({"done": True})}\n\n'
        finally:
            # Fix: 通知 _stream_worker 停止（用戶斷線或串流正常結束）
            cancel_event.set()
            # Bug R4#2 Fix: 無論是否中途斷線都要清理 SESSION_STATS，
            # 避免 HumanMessage 孤立在歷史中破壞下一輪對話 context
            if full_reply:
                complete = "".join(full_reply)
                SESSION_STATS[session_id].append(AIMessage(content=complete))
                _trim_history(session_id)
                # 背景儲存訊息到 chat_history DB
                from chat_bot.auth.auth import save_chat_message as _save_msg
                threading.Thread(target=_save_msg, args=(session_id, "user", msg), daemon=True).start()
                threading.Thread(target=_save_msg, args=(session_id, "ai", complete), daemon=True).start()
                # 背景更新 playbook（串流完成後）
                if _llm is not None:
                    _lref = _llm
                    _conv_snap = _playbook_conv(session_id)
                    threading.Thread(
                        target=update_playbook,
                        args=(session_id, _conv_snap, _lref),
                        daemon=True,
                    ).start()
                    USER_PROFILES.pop(session_id, None)
            elif SESSION_STATS.get(session_id) and isinstance(
                SESSION_STATS[session_id][-1], HumanMessage
            ):
                # 沒有收到任何 token（斷線前 LLM 未回應）→ 移除懸空的 HumanMessage
                SESSION_STATS[session_id].pop()

    return StreamingResponse(_token_generator(), media_type="text/event-stream")


# ── 每日運勢 API ──────────────────────────────────────────────
_DAILY_FORTUNE_PROMPT = """你是資深紫微斗數命理師。以下是命主的命盤分析資料，請為他/她生成今天（{today}）的運勢分析。

命盤資料摘要：
{horoscope}

請按以下格式回答（繁體中文，整體精簡有力）：

## 今日整體運勢 {stars}

**今日概述：** （一到兩句點出今日主題）

| 面向 | 運勢 | 提示 |
|------|------|------|
| 💕 感情 | ★★★★☆ | 一句話 |
| 💼 事業 | ★★★☆☆ | 一句話 |
| 💰 財運 | ★★★★☆ | 一句話 |
| ❤️ 健康 | ★★★☆☆ | 一句話 |

**今日行動建議：** 具體可執行的一件事。

🎨 **幸運色：** XXX　　🔢 **幸運數字：** X"""


@app.get("/api/daily-fortune")
async def api_daily_fortune(session_id: str = "web_default"):
    """SSE 串流：回傳今日運勢（同一天同 session 只生成一次，之後從 DB 快取回傳）。"""
    import datetime as _dt
    session_id = (session_id or "").strip() or "web_default"
    today = _dt.date.today().isoformat()   # e.g. "2026-05-17"

    from chat_bot.auth.auth import get_daily_fortune, save_daily_fortune, load_horoscope

    async def _stream():
        # 先查快取
        cached = await run_in_threadpool(get_daily_fortune, session_id, today)
        if cached:
            yield f'data: {json.dumps({"token": cached})}\n\n'
            yield f'data: {json.dumps({"done": True, "cached": True})}\n\n'
            return

        # 確保有命盤資料
        horoscope = HOROSCOPE.get(session_id, "")
        if not horoscope:
            _, _, _bi = await run_in_threadpool(load_horoscope, session_id)
            horoscope_tuple = await run_in_threadpool(load_horoscope, session_id)
            horoscope = horoscope_tuple[0] or ""
        if not horoscope:
            yield f'data: {json.dumps({"token": "⚠️ 尚未建立命盤，請先輸入出生資料排盤。"})}\n\n'
            yield f'data: {json.dumps({"done": True})}\n\n'
            return

        if _llm is None:
            yield f'data: {json.dumps({"token": "⚠️ AI 服務尚未就緒，請稍後再試。"})}\n\n'
            yield f'data: {json.dumps({"done": True})}\n\n'
            return

        from langchain_core.prompts import PromptTemplate
        import random
        stars_options = ["★★★★☆", "★★★☆☆", "★★★★★", "★★★☆☆", "★★★★☆"]
        stars = random.choice(stars_options)
        prompt = PromptTemplate.from_template(_DAILY_FORTUNE_PROMPT)
        chain = prompt | _llm
        accumulated = []
        try:
            async for chunk in chain.astream({
                "today": today,
                "horoscope": horoscope[:2500],   # 節省 tokens
                "stars": stars,
            }):
                token = chunk.content if hasattr(chunk, "content") else str(chunk)
                if token:
                    accumulated.append(token)
                    yield f'data: {json.dumps({"token": token})}\n\n'
        except Exception as exc:
            logger.exception("[daily-fortune] generate error")
            yield f'data: {json.dumps({"token": f"⚠️ 生成失敗：{exc}"})}\n\n'
            yield f'data: {json.dumps({"done": True})}\n\n'
            return

        content = "".join(accumulated)
        threading.Thread(
            target=save_daily_fortune, args=(session_id, today, content), daemon=True
        ).start()
        yield f'data: {json.dumps({"done": True})}\n\n'

    return StreamingResponse(_stream(), media_type="text/event-stream")


# ── 收藏 API ───────────────────────────────────────────────────
class BookmarkRequest(BaseModel):
    session_id: str = "web_default"
    content: str


@app.post("/api/bookmarks")
async def api_bookmark_add(body: BookmarkRequest):
    session_id = (body.session_id or "").strip() or "web_default"
    from chat_bot.auth.auth import save_bookmark
    bm_id = await run_in_threadpool(save_bookmark, session_id, body.content)
    return {"id": bm_id}


@app.get("/api/bookmarks")
async def api_bookmark_list(session_id: str = "web_default"):
    session_id = (session_id or "").strip() or "web_default"
    from chat_bot.auth.auth import load_bookmarks
    items = await run_in_threadpool(load_bookmarks, session_id)
    return {"bookmarks": items}


@app.delete("/api/bookmarks/{bookmark_id}")
async def api_bookmark_delete(bookmark_id: int, session_id: str = "web_default"):
    session_id = (session_id or "").strip() or "web_default"
    from chat_bot.auth.auth import delete_bookmark
    ok = await run_in_threadpool(delete_bookmark, bookmark_id, session_id)
    return {"status": "ok" if ok else "not_found"}


# ── 感情合盤 API ──────────────────────────────────────────────
class CompatibilityRequest(BaseModel):
    person_a: dict  # {year, month, day, hour, is_male}
    person_b: dict
    session_id: str = "web_default"
    name_a: str = "甲方"
    name_b: str = "乙方"


@app.post("/api/compatibility")
async def api_compatibility(body: CompatibilityRequest):
    """
    輸入兩人出生資料，回傳合盤分析文字（SSE 串流）。
    使用 get_palace_information() 生成完整宮位分析，並自動儲存命盤至命盤庫。
    """
    from ziweidoushu.kernel import ZiweiChart
    from ziweidoushu.base import ZiWeiConfig
    from chat_bot.node.gen_ziwei import get_palace_information, _get_domain_df

    def _gen_full_chart(info: dict) -> tuple[str, str, str]:
        """Returns (chart_markdown, horoscope_text, header_str)"""
        cfg = ZiWeiConfig(
            int(info["year"]), int(info["month"]), int(info["day"]),
            int(info["hour"]), bool(info.get("is_male", True))
        )
        df = ZiweiChart(cfg).gen_chart()
        chart_md = df.to_markdown()
        horoscope = get_palace_information(df, _get_domain_df())
        gender = "男" if info.get("is_male", True) else "女"
        header = f"{info['year']}年{info['month']}月{info['day']}日 {info['hour']}時 {gender}"
        chart_text = f"【{header}】\n\n命盤：\n{chart_md}\n\n命盤分析：\n{horoscope}"
        return chart_md, horoscope, chart_text

    async def _stream():
        if _llm is None:
            yield f'data: {json.dumps({"token": "⚠️ AI 服務尚未就緒，請稍後再試。"})}\n\n'
            yield f'data: {json.dumps({"done": True})}\n\n'
            return

        # Generate chart A
        yield f'data: {json.dumps({"progress": f"⏳ 正在為【{body.name_a}】排命盤並分析（約 30-60 秒）…"})}\n\n'
        try:
            chart_md_a, horoscope_a, chart_text_a = await run_in_threadpool(_gen_full_chart, body.person_a)
        except Exception as exc:
            yield f'data: {json.dumps({"token": f"⚠️ 【{body.name_a}】命盤生成失敗：{exc}"})}\n\n'
            yield f'data: {json.dumps({"done": True})}\n\n'
            return

        # Generate chart B
        yield f'data: {json.dumps({"progress": f"⏳ 正在為【{body.name_b}】排命盤並分析（約 30-60 秒）…"})}\n\n'
        try:
            chart_md_b, horoscope_b, chart_text_b = await run_in_threadpool(_gen_full_chart, body.person_b)
        except Exception as exc:
            yield f'data: {json.dumps({"token": f"⚠️ 【{body.name_b}】命盤生成失敗：{exc}"})}\n\n'
            yield f'data: {json.dumps({"done": True})}\n\n'
            return

        # Auto-save both charts to profiles
        saved_names: list[str] = []
        session_id = (body.session_id or "").strip() or "web_default"
        try:
            from chat_bot.auth.auth import save_profile
            save_profile(session_id, body.name_a, horoscope_a, chart_md_a, body.person_a)
            save_profile(session_id, body.name_b, horoscope_b, chart_md_b, body.person_b)
            saved_names = [body.name_a, body.name_b]
        except Exception as exc:
            logger.warning("[compat] 儲存命盤失敗: %s", exc)

        yield f'data: {json.dumps({"progress": "✨ AI 合盤分析中，請稍候…"})}\n\n'
        yield f'data: {json.dumps({"clear": True})}\n\n'
        if saved_names:
            yield f'data: {json.dumps({"saved": saved_names})}\n\n'

        from langchain_core.prompts import PromptTemplate
        COMPAT_PROMPT = """你是紫微斗數命理師，請根據以下兩份完整命盤分析兩人的感情相性。

甲方（{name_a}）命盤分析：
{chart_a}

乙方（{name_b}）命盤分析：
{chart_b}

請從以下角度深入分析：
1. 兩人的個性特質與互補性
2. 感情發展的優勢與挑戰
3. 事業財務上的合作潛力
4. 整體相性評估與建議

請用繁體中文回答，條理清晰，分段說明，並直接使用對方代稱（{name_a}、{name_b}）。"""

        prompt = PromptTemplate.from_template(COMPAT_PROMPT)
        chain = prompt | _llm
        try:
            async for chunk in chain.astream({"chart_a": chart_text_a, "chart_b": chart_text_b,
                                               "name_a": body.name_a, "name_b": body.name_b}):
                token = chunk.content if hasattr(chunk, "content") else str(chunk)
                if token:
                    yield f'data: {json.dumps({"token": token})}\n\n'
        except Exception as exc:
            yield f'data: {json.dumps({"token": f"⚠️ 分析失敗：{exc}"})}\n\n'
        yield f'data: {json.dumps({"done": True})}\n\n'

    return StreamingResponse(_stream(), media_type="text/event-stream")


# ── LINE Bot Webhook ───────────────────────────────────────────
@app.post("/linebot")
async def linebot(request: Request):
    """LINE Bot webhook（使用 LINE 自身的 Signature 驗證，不需 API Key）。"""
    body = await request.body()
    body_text = body.decode("utf-8")
    try:
        json_data = json.loads(body_text)
        events = json_data.get("events", [])
        if not events:
            return "OK"

        # Fix #9：lazy singleton，避免每次請求重新建立 client 物件
        # Fix #10：若 LINE 金鑰未設定，提早返回避免後續 AttributeError
        global _line_bot_api, _line_handler
        if _line_bot_api is None or _line_handler is None:
            if not GlobalConfig.LINE_ACCESS_KEY or not GlobalConfig.LINE_SECRET:
                logger.warning("LINE_ACCESS_KEY 或 LINE_SECRET 未設定，略過 webhook 處理")
                return "OK"
            # 同時初始化兩個物件：若任一失敗都不保留半初始化狀態
            new_api = LineBotApi(GlobalConfig.LINE_ACCESS_KEY)
            new_handler = WebhookHandler(GlobalConfig.LINE_SECRET)
            _line_bot_api = new_api
            _line_handler = new_handler
        line_bot_api = _line_bot_api
        handler = _line_handler
        try:
            handler.handle(body_text, request.headers.get("X-Line-Signature", ""))
        except InvalidSignatureError:
            # LINE platform requires 400 on bad signature; returning OK would cause
            # LINE to keep retrying (infinite retry loop).
            logger.warning("[linebot] invalid signature — rejecting request")
            raise HTTPException(status_code=400, detail="Invalid signature")

        _evict_stale_sessions()  # Fix #5：每次請求順帶清理過期 sessions

        # Fix #6：逐一處理每個 event，不只取 events[0]
        for event in events:
            try:
                # Fix #5：guard 非文字訊息事件（follow/unfollow/postback 沒有 message key）
                if event.get("type") != "message":
                    continue
                if event.get("message", {}).get("type") != "text":
                    tk = event.get("replyToken", "")
                    if tk:
                        line_bot_api.reply_message(tk, TextSendMessage("我是算命機器人, 看不到文字以外的訊息喔!"))
                    continue

                # Bug R4#7 Fix: 用 .get() 避免 KeyError 讓 tk 保持 unbound 進而引發 NameError
                tk = event.get("replyToken", "")
                if not tk:
                    logger.warning("[linebot] event missing replyToken, skipping")
                    continue
                msg = event["message"]["text"]
                user_id = event["source"]["userId"]
                # Bug #3 Fix: 精確匹配避免一般訊息誤觸重置
                is_reset = msg.strip() == "/reset"

                if _pipeline is None:
                    line_bot_api.reply_message(tk, TextSendMessage("服務啟動中，請稍後再試。"))
                    continue

                _touch_session(user_id)

                from chat_bot.utils.user_playbook import load_playbook, update_playbook
                line_user_profile = USER_PROFILES.get(user_id) or load_playbook(user_id)
                USER_PROFILES[user_id] = line_user_profile

                if GlobalConfig.USE_AGENT_SKILL:
                    if is_reset or user_id not in AGENT_THREAD:
                        AGENT_THREAD[user_id] = str(uuid.uuid4())
                        if is_reset:
                            line_bot_api.reply_message(tk, TextSendMessage("已重置對話！請重新告訴我你的出生資料。"))
                            continue

                    config = {"configurable": {"thread_id": AGENT_THREAD[user_id]}}
                    output = await run_in_threadpool(
                        _pipeline.invoke, {"messages": [HumanMessage(content=msg)]}, config
                    )
                    reply = _extract_last_message(output)

                else:
                    # Bug #1 Fix: 重置時換新 thread_id，清除 MemorySaver 的舊 birth_info
                    if user_id not in HOROSCOPE or is_reset:
                        NON_AGENT_THREAD[user_id] = str(uuid.uuid4())
                        HOROSCOPE[user_id] = ""
                        SESSION_STATS[user_id] = []
                        if is_reset:
                            line_bot_api.reply_message(tk, TextSendMessage("已重置對話！請重新告訴我你的出生資料。"))
                            continue

                    thread_id = NON_AGENT_THREAD.setdefault(user_id, str(uuid.uuid4()))
                    config = {"configurable": {"thread_id": thread_id, "session_id": user_id}}
                    SESSION_STATS[user_id].append(HumanMessage(content=msg))
                    _trim_history(user_id)

                    output = await run_in_threadpool(
                        _pipeline.invoke,
                        {"messages": _pipeline_messages(user_id), "horoscope": HOROSCOPE[user_id], "user_profile": line_user_profile},
                        config,
                    )
                    if not HOROSCOPE[user_id]:
                        HOROSCOPE[user_id] = output.get("horoscope", "")

                    reply = _extract_last_message(output)
                    SESSION_STATS[user_id].append(AIMessage(content=reply))
                    logger.debug("[linebot] cur_state: %s", SESSION_STATS[user_id])

                logger.debug("[linebot] reply: %.100s", reply)
                line_bot_api.reply_message(tk, TextSendMessage(reply))

                # 背景更新 playbook
                if not is_reset and _llm is not None:
                    _lref = _llm
                    _conv_snap = _playbook_conv(user_id)
                    threading.Thread(
                        target=update_playbook,
                        args=(user_id, _conv_snap, _lref),
                        daemon=True,
                    ).start()
                    USER_PROFILES.pop(user_id, None)

            except Exception:
                # Bug #1 Fix: 內層例外也要回覆用戶，否則 LINE 用戶等不到回應
                # Bug R4#7 Fix: tk 現在保證已設定（上方已過濾空值並 continue）
                logger.exception("[linebot] error handling event")
                try:
                    if tk:
                        line_bot_api.reply_message(tk, TextSendMessage("⚠️ 處理訊息時發生錯誤，請稍後再試。"))
                except Exception:
                    pass

    except Exception:
        logger.exception("[linebot] outer error")

    return "OK"


# ── 多命盤管理 API ──────────────────────────────────────────────
class ProfileRequest(BaseModel):
    session_id: str = "web_default"
    profile_name: str


@app.get("/api/profiles")
async def api_profiles_list(session_id: str = "web_default"):
    session_id = (session_id or "").strip() or "web_default"
    from chat_bot.auth.auth import load_profiles
    items = await run_in_threadpool(load_profiles, session_id)
    return {"profiles": items}


@app.post("/api/profiles")
async def api_profiles_save(body: ProfileRequest):
    session_id = (body.session_id or "").strip() or "web_default"
    chart = CHART_TABLE.get(session_id, "")
    horoscope = HOROSCOPE.get(session_id, "")
    birth_info = BIRTH_INFO.get(session_id)
    if not horoscope:
        from chat_bot.auth.auth import load_horoscope
        _h, _ct, _bi = await run_in_threadpool(load_horoscope, session_id)
        horoscope = _h or ""
        chart = _ct or ""
        birth_info = _bi
    if not horoscope:
        raise HTTPException(status_code=400, detail="尚未建立命盤，請先排盤")
    from chat_bot.auth.auth import save_profile
    pid = await run_in_threadpool(save_profile, session_id, body.profile_name, horoscope, chart, birth_info)
    return {"id": pid}


@app.get("/api/profiles/{profile_id}")
async def api_profiles_get(profile_id: int, session_id: str = "web_default", activate: bool = False):
    session_id = (session_id or "").strip() or "web_default"
    from chat_bot.auth.auth import get_profile
    profile = await run_in_threadpool(get_profile, profile_id, session_id)
    if not profile:
        raise HTTPException(status_code=404, detail="命盤不存在")
    if activate and profile.get("horoscope"):
        # 將此命盤載入為當前 session 的工作命盤，讓 Q&A 使用該命盤分析
        HOROSCOPE[session_id] = profile["horoscope"]
        CHART_TABLE[session_id] = profile.get("chart_table", "")
        if profile.get("birth_info"):
            BIRTH_INFO[session_id] = profile["birth_info"]
        # 清除舊的對話 context，避免混用
        SESSION_STATS.pop(session_id, None)
        NON_AGENT_THREAD.pop(session_id, None)
    return profile


@app.delete("/api/profiles/{profile_id}")
async def api_profiles_delete(profile_id: int, session_id: str = "web_default"):
    session_id = (session_id or "").strip() or "web_default"
    from chat_bot.auth.auth import delete_profile
    await run_in_threadpool(delete_profile, profile_id, session_id)
    return {"status": "ok"}


# ── 運勢日曆 API ───────────────────────────────────────────────
_MONTHLY_FORTUNE_PROMPT = """你是資深紫微斗數命理師。請根據以下命主的命盤，為{year}年{month}月的每一天生成運勢評分。

命盤摘要：
{horoscope}

請以嚴格的 JSON 格式回傳，不要包含 markdown 代碼塊或其他文字：
{{"days":[{{"day":1,"score":4,"note":"一句概述"}},{{"day":2,"score":3,"note":"一句概述"}}...],"month_summary":"本月整體運勢（2-3句）"}}

score 為 1-5 整數（5最佳），共需包含 {days_count} 天。note 控制在 15 字以內。"""


@app.get("/api/fortune-calendar")
async def api_fortune_calendar(session_id: str = "web_default", year: int = None, month: int = None):
    import datetime as _dt
    import calendar as _cal
    import json as _json
    import re as _re
    session_id = (session_id or "").strip() or "web_default"
    now = _dt.date.today()
    if not year:
        year = now.year
    if not month:
        month = now.month
    year_month = f"{year:04d}-{month:02d}"
    days_count = _cal.monthrange(year, month)[1]

    from chat_bot.auth.auth import get_monthly_fortune, save_monthly_fortune, load_horoscope

    cached = await run_in_threadpool(get_monthly_fortune, session_id, year_month)
    if cached:
        try:
            return _json.loads(cached)
        except Exception:
            pass

    horoscope = HOROSCOPE.get(session_id, "")
    if not horoscope:
        _h, _ct, _bi = await run_in_threadpool(load_horoscope, session_id)
        horoscope = _h or ""
    if not horoscope:
        raise HTTPException(status_code=400, detail="尚未建立命盤，請先排盤")

    if _llm is None:
        raise HTTPException(status_code=503, detail="AI 服務未就緒")

    from langchain_core.prompts import PromptTemplate
    prompt = PromptTemplate.from_template(_MONTHLY_FORTUNE_PROMPT)
    chain = prompt | _llm
    try:
        result = await chain.ainvoke({
            "year": year, "month": month,
            "days_count": days_count,
            "horoscope": horoscope[:2000],
        })
        content = result.content if hasattr(result, "content") else str(result)
        match = _re.search(r'\{[\s\S]*\}', content)
        if not match:
            raise HTTPException(status_code=500, detail="AI 回傳格式錯誤")
        data = _json.loads(match.group())
        cache_str = _json.dumps(data, ensure_ascii=False)
        threading.Thread(target=save_monthly_fortune, args=(session_id, year_month, cache_str), daemon=True).start()
        return data
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[fortune-calendar] error")
        raise HTTPException(status_code=500, detail=str(exc))


# ── 個人設定 API ───────────────────────────────────────────────
class SettingsRequest(BaseModel):
    session_id: str = "web_default"
    response_style: str = "balanced"


@app.get("/api/settings")
async def api_settings_get(session_id: str = "web_default"):
    session_id = (session_id or "").strip() or "web_default"
    from chat_bot.auth.auth import get_user_settings
    return await run_in_threadpool(get_user_settings, session_id)


@app.post("/api/settings")
async def api_settings_save(body: SettingsRequest):
    session_id = (body.session_id or "").strip() or "web_default"
    from chat_bot.auth.auth import save_user_settings
    await run_in_threadpool(save_user_settings, session_id, body.response_style)
    return {"status": "ok"}


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


@app.post("/api/auth/change-password")
async def api_change_password(body: ChangePasswordRequest, request: Request):
    from chat_bot.auth.auth import verify_token, change_password
    auth_header = request.headers.get("Authorization", "")
    token = auth_header.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="未登入")
    user = verify_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Token 無效")
    if len(body.new_password) < 6:
        raise HTTPException(status_code=400, detail="新密碼至少需要 6 個字元")
    ok = await run_in_threadpool(change_password, user["session_id"], body.old_password, body.new_password)
    if not ok:
        raise HTTPException(status_code=400, detail="舊密碼不正確")
    return {"status": "ok"}


# ── 關鍵人生節點 API ──────────────────────────────────────────
_LIFE_EVENTS_PROMPT = """你是資深紫微斗數命理師。請根據以下命主的命盤，分析並找出3-5個最重要的人生關鍵節點（大限轉換點或重要流年）。

命盤分析：
{horoscope}

大限時間軸資料：
{timeline}

請以嚴格的 JSON 格式回傳，不要包含 markdown 代碼塊或其他文字：
{{"events":[{{"age_start":12,"age_end":21,"palace":"命宮","title":"學業衝刺","description":"此大限文昌化科入命，學業表現優異，宜積極進修","type":"good"}},...]}}

type 必須是 "good"、"caution" 或 "neutral" 其中之一。title 控制在 8 字以內，description 控制在 40 字以內。"""


@app.get("/api/life-events")
async def api_life_events(session_id: str = "web_default"):
    import json as _json
    import re as _re
    session_id = (session_id or "").strip() or "web_default"

    horoscope = HOROSCOPE.get(session_id, "")
    chart_table = CHART_TABLE.get(session_id, "")
    if not horoscope:
        from chat_bot.auth.auth import load_horoscope
        _h, _ct, _bi = await run_in_threadpool(load_horoscope, session_id)
        horoscope = _h or ""
        chart_table = _ct or ""
    if not horoscope:
        raise HTTPException(status_code=400, detail="尚未建立命盤，請先排盤")

    if _llm is None:
        raise HTTPException(status_code=503, detail="AI 服務未就緒")

    from langchain_core.prompts import PromptTemplate
    prompt = PromptTemplate.from_template(_LIFE_EVENTS_PROMPT)
    chain = prompt | _llm
    try:
        result = await chain.ainvoke({
            "horoscope": horoscope[:2000],
            "timeline": chart_table[:1500],
        })
        content = result.content if hasattr(result, "content") else str(result)
        match = _re.search(r'\{[\s\S]*\}', content)
        if not match:
            raise HTTPException(status_code=500, detail="AI 回傳格式錯誤")
        return _json.loads(match.group())
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[life-events] error")
        raise HTTPException(status_code=500, detail=str(exc))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=False)
