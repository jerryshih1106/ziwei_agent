import asyncio
import json
import logging
import os
import queue as _queue
import re
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
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
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
from chat_bot.session_store import SessionStore
from chat_bot.llm_client import astream_with_retry, is_transient_error
from chat_bot.background_tasks import bg as _bg

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

class _AppState:
    """Centralises pipeline / LLM readiness so startup failures surface clearly."""
    pipeline = None
    llm = None
    _init_error: "Exception | None" = None

    @classmethod
    def is_ready(cls) -> bool:
        return cls.pipeline is not None and cls.llm is not None

    @classmethod
    def require_pipeline(cls):
        if cls.pipeline is None:
            raise HTTPException(status_code=503, detail="服務啟動中，請稍後再試。")
        return cls.pipeline

    @classmethod
    def require_llm(cls):
        if cls.llm is None:
            raise HTTPException(status_code=503, detail="AI 服務尚未就緒，請稍後再試。")
        return cls.llm


_pipeline = None   # kept for backward compat; _AppState.pipeline is the source of truth
_llm = None        # kept for backward compat; _AppState.llm is the source of truth
_line_bot_api: "LineBotApi | None" = None   # Fix #9：lazy singleton
_line_handler: "WebhookHandler | None" = None  # Fix #9：lazy singleton

# SessionStore — all session-scoped state in one place (#1 + #2)
_store = SessionStore()

# ── Backward-compat aliases (existing code keeps working unchanged) ──
SESSION_STATS   = _store.messages
HOROSCOPE       = _store.horoscopes
CHART_TABLE     = _store.chart_tables
BIRTH_INFO      = _store.birth_infos
BAZI_DOC        = _store.bazi_docs
AGENT_THREAD    = _store.agent_threads
NON_AGENT_THREAD = _store.thread_ids
USER_PROFILES   = _store.user_profiles
_SESSION_LAST_SEEN = _store._last_seen


async def _get_session_lock(session_id: str) -> asyncio.Lock:
    """Return (or create) the asyncio.Lock for this session_id (coroutine-safe)."""
    return await _store.get_lock(session_id)

# 每個 session 最多保留的訊息輪數（fix #9：防止無限成長）
_MAX_HISTORY = 20
# Fix #5：session 閒置超過此秒數就清除（預設 2 小時）
_SESSION_TTL_SECS = 7200


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pipeline, _llm
    from chat_bot.utils.utils import build_llm
    from chat_bot.auth.auth import init_db, sync_whitelist
    _store.init_event_loop()
    init_db()
    sync_whitelist()
    try:
        _AppState.llm = build_llm(GlobalConfig.MODEL)
        _AppState.pipeline = LLMProcessor(llm=_AppState.llm).set_pipeline()
        # backward-compat module-level references
        _pipeline = _AppState.pipeline
        _llm = _AppState.llm
    except Exception as exc:
        _AppState._init_error = exc
        logger.exception("[lifespan] pipeline/LLM 初始化失敗: %s", exc)
    yield
    _bg.shutdown(wait=True)  # 優雅關閉：等待背景任務完成再退出
    from chat_bot.utils.utils import flush_langfuse
    flush_langfuse()


app = FastAPI(title="紫微斗數 API", lifespan=lifespan)
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# ── 安全 Headers middleware ───────────────────────────────────
from fastapi.middleware.cors import CORSMiddleware

@app.middleware("http")
async def _security_headers(request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-XSS-Protection", "1; mode=block")
    return response

# ── 登入速率限制（暴力破解防護）────────────────────────────────
from collections import defaultdict as _defaultdict

_login_attempts: dict = _defaultdict(list)
_LOGIN_MAX_ATTEMPTS = 10   # 10 次
_LOGIN_WINDOW_SECS  = 300  # 5 分鐘內


def _check_login_rate(ip: str) -> bool:
    """回傳 True 表示允許，False 表示已超過速率限制。"""
    now = time.time()
    bucket = _login_attempts[ip]
    _login_attempts[ip] = [t for t in bucket if now - t < _LOGIN_WINDOW_SECS]
    if len(_login_attempts[ip]) >= _LOGIN_MAX_ATTEMPTS:
        return False
    _login_attempts[ip].append(now)
    return True


# ── 啟動安全性檢查 ────────────────────────────────────────────
if not os.environ.get("JWT_SECRET"):
    logger.warning(
        "[security] JWT_SECRET 未設定！每次重啟將產生新的隨機 secret，"
        "導致所有使用者 Token 失效。請在 .env 設定固定的 JWT_SECRET。"
    )
if not GlobalConfig.API_KEY:
    logger.warning(
        "[security] API_KEY 未設定！部分 API 端點（chat/reset/pop-last 等）"
        "處於完全開放狀態，任何人皆可呼叫。建議在 .env 設定 API_KEY。"
    )

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
from pydantic import field_validator

class ChatRequest(BaseModel):
    message: str
    session_id: str = "web_default"

    @field_validator("message")
    @classmethod
    def message_length(cls, v: str) -> str:
        if len(v) > 2000:
            raise ValueError("訊息長度不得超過 2000 字元")
        return v


class ResetRequest(BaseModel):
    session_id: str = "web_default"


class RegisterRequest(BaseModel):
    username: str
    password: str

    @field_validator("username")
    @classmethod
    def username_chars(cls, v: str) -> str:
        import re as _re
        v = v.strip()
        if not _re.match(r"^[a-zA-Z0-9_\-一-鿿]{3,20}$", v):
            raise ValueError("帳號只能包含英數字、底線、橫線或中文，長度 3-20")
        return v


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
    _store.trim_messages(session_id, _MAX_HISTORY)


def _pipeline_messages(session_id: str) -> list:
    """當命盤已存在時，只傳最近 12 則訊息給 pipeline，減少 context 浪費。"""
    return _store.pipeline_messages(session_id, bool(HOROSCOPE.get(session_id)))


def _touch_session(session_id: str) -> None:
    _store.touch(session_id)


def _evict_stale_sessions() -> None:
    _store.evict_stale(_SESSION_TTL_SECS)


def _playbook_conv(session_id: str, n: int = 5) -> str:
    """回傳最近 n 則用戶訊息，作為 update_playbook 的對話上下文。"""
    msgs = SESSION_STATS.get(session_id, [])
    user_texts = [m.content for m in msgs if isinstance(m, HumanMessage)][-n:]
    return "\n".join(f"用戶: {t}" for t in user_texts)


def _auto_activate_bazi(session_id: str, birth_info: "dict | None") -> None:
    """排好紫微命盤後，用相同出生資料自動計算八字並存入 BAZI_DOC。"""
    if not birth_info or BAZI_DOC.get(session_id):
        return  # 若用戶已從八字 modal 手動設定，不覆蓋
    try:
        from chat_bot.utils.bazi import compute_bazi, wuxing_count, day_master, _STEM_ELEMENT
        year, month, day = birth_info.get("year"), birth_info.get("month"), birth_info.get("day")
        hour = birth_info.get("hour", 0)
        is_male = birth_info.get("is_male", 1)
        if not all([year, month, day]):
            return
        bazi = compute_bazi(int(year), int(month), int(day), int(hour), 0, 120)
        dm = day_master(bazi)
        dm_elem = _STEM_ELEMENT[bazi["day_pillar"]["stem_idx"]]
        wx = wuxing_count(bazi)
        wx_str = " ".join(f"{k}{v}個" for k, v in wx.items() if v > 0)
        cg = bazi.get("canggan", {})
        cang_str = " ".join(
            f"{'年月日時'[i]}[{'/'.join(cg.get(k, [])) or '—'}]"
            for i, k in enumerate(["year_pillar", "month_pillar", "day_pillar", "hour_pillar"])
        )
        gender = "男" if is_male else "女"
        doc = (
            f"（{gender}）\n"
            f"年柱：{bazi['year_pillar']['ganzhi']}　月柱：{bazi['month_pillar']['ganzhi']}　"
            f"日柱：{bazi['day_pillar']['ganzhi']}　時柱：{bazi['hour_pillar']['ganzhi']}\n"
            f"日主：{dm}（{dm_elem}）　時辰：{bazi['shichen']}\n"
            f"五行：{wx_str}\n"
            f"藏幹：{cang_str}"
        )
        BAZI_DOC[session_id] = doc
        logger.info("[auto_activate_bazi] session=%s bazi activated", session_id)
    except Exception:
        logger.warning("[auto_activate_bazi] failed for session=%s", session_id, exc_info=True)


def _auto_save_self_profile(session_id: str, horoscope: str, chart_table: str, birth_info: "dict | None") -> None:
    """新命盤排好後，若命盤庫中尚無「我」，自動以「我」為名存入命盤庫。"""
    from chat_bot.auth.auth import save_profile, load_profiles
    try:
        existing = load_profiles(session_id)
        if not any(p["profile_name"] == "我" for p in existing):
            save_profile(session_id, "我", horoscope, chart_table, birth_info)
            logger.info("[auto_save_self] session=%s saved as '我'", session_id)
    except Exception:
        logger.warning("[auto_save_self] failed for session=%s", session_id, exc_info=True)


def _restore_session_from_db(session_id: str, limit: int = 20) -> None:
    """
    伺服器重啟後的記憶體恢復：從 chat_history DB 將最近 N 則對話
    重新載入 SESSION_STATS，讓 LLM 取得上下文，避免重啟即失憶。
    只在 session_id 尚未存在於 SESSION_STATS 時執行。
    """
    if session_id in SESSION_STATS:
        return
    from chat_bot.auth.auth import load_chat_history
    try:
        rows = load_chat_history(session_id, limit=limit)
    except Exception:
        SESSION_STATS[session_id] = []
        return
    msgs = []
    for r in rows:
        if r["role"] == "user":
            msgs.append(HumanMessage(content=r["content"]))
        else:
            msgs.append(AIMessage(content=r["content"]))
    SESSION_STATS[session_id] = msgs
    if msgs:
        logger.info("[session_restore] %s: recovered %d messages from DB", session_id, len(msgs))


# ── 行銷首頁 ──────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    from chat_bot.auth.auth import count_users, count_charts
    line_id = GlobalConfig.LINE_BOT_ID or "@ziwei_ai"
    qr_url = f"https://qr-official.line.me/gs/M_{line_id.lstrip('@')}_BW.png"
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "request": request,
            "line_id": line_id,
            "qr_url": qr_url,
            "time_table": TIME_TABLE,
            "chart_count": count_charts(),
            "user_count": count_users(),
        },
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
async def api_register(body: RegisterRequest, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    if not _check_login_rate(f"reg:{client_ip}"):
        raise HTTPException(status_code=429, detail="註冊嘗試過於頻繁，請 5 分鐘後再試")
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
async def api_login(body: LoginRequest, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    if not _check_login_rate(client_ip):
        raise HTTPException(status_code=429, detail="登入嘗試過於頻繁，請 5 分鐘後再試")
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
    if not _AppState.is_ready():
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
                    _store.set_horoscope_atomic(session_id, _h, _ct or "", _bi)
                    # 伺服器重啟後同步恢復對話上下文
                    await run_in_threadpool(_restore_session_from_db, session_id)
                    _auto_activate_bazi(session_id, _bi)

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
                    _new_h = output.get("horoscope", "")
                    _new_ct = output.get("chart_table", "")
                    _new_bi = output.get("birth_info") or None
                    _store.set_horoscope_atomic(session_id, _new_h, _new_ct, _new_bi)
                    if HOROSCOPE[session_id]:
                        from chat_bot.auth.auth import save_horoscope
                        _h_snap = HOROSCOPE[session_id]
                        _ct_snap = CHART_TABLE.get(session_id, "")
                        _bi_snap = BIRTH_INFO.get(session_id)
                        def _save_new_chart(sid=session_id, h=_h_snap, ct=_ct_snap, bi=_bi_snap):
                            save_horoscope(sid, h, ct, bi)
                            _auto_save_self_profile(sid, h, ct, bi)
                            _auto_activate_bazi(sid, bi)
                        _bg.submit(_save_new_chart)

                reply = _extract_last_message(output)
                SESSION_STATS[session_id].append(AIMessage(content=reply))

        # 背景更新 user playbook（不阻塞回應）
        if not is_reset and _llm is not None:
            _bg.submit_named(f"playbook:{session_id}", update_playbook, session_id, _playbook_conv(session_id), _llm)
            USER_PROFILES.pop(session_id, None)  # 下次請求時從磁碟重新載入最新資料

        logger.info(f"[api/chat] done in {time.time()-t0:.1f}s")
        return {"reply": reply}

    except Exception as e:
        logger.exception("[api/chat] error")
        if is_transient_error(e):
            return JSONResponse(status_code=429, content={"reply": "⚠️ AI 服務目前請求過多，請稍後再試。"})
        return JSONResponse(status_code=500, content={"reply": "⚠️ 發生錯誤，請稍後再試。"})


# ── 重置 API ──────────────────────────────────────────────────
@app.post("/api/reset", dependencies=[Depends(verify_api_key)])
async def api_reset(body: ResetRequest):
    """清除指定 session 的對話記憶與命盤，讓使用者可以重新排盤。"""
    session_id = (body.session_id or "").strip() or "web_default"
    _store.clear_session(session_id)
    return {"status": "ok"}


@app.post("/api/clear-chart", dependencies=[Depends(verify_api_key)])
async def api_clear_chart(body: ResetRequest):
    """只清除命盤資料（HOROSCOPE / CHART_TABLE / BIRTH_INFO + 磁碟檔案），保留對話記憶。
    用於「排新命盤」：讓使用者為不同人排盤，不需清除對話歷史。
    關鍵：必須刪除磁碟上的 horoscope 檔案，否則下次請求會從磁碟重載舊命盤。
    同時重置 NON_AGENT_THREAD，讓 LangGraph state 回到初始（is_fortune=False）。"""
    session_id = (body.session_id or "").strip() or "web_default"
    _store.clear_chart(session_id)
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
async def api_history(session_id: str = "web_default", limit: int = 30, offset: int = 0, q: str = ""):
    """回傳指定 session 的聊天歷史（支援關鍵字搜尋 q）。"""
    session_id = (session_id or "").strip() or "web_default"
    limit = max(1, min(limit, 100))
    from chat_bot.auth.auth import load_chat_history
    messages = await run_in_threadpool(load_chat_history, session_id, limit, offset, q.strip())
    return {"messages": messages, "has_more": len(messages) == limit and not q}


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
    if not _AppState.is_ready():
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

    # ── API 用量速率限制（免費方案每日10次）──────────────────────
    if not is_reset:
        from chat_bot.auth.auth import check_and_increment_api_calls as _check_rate
        _allowed, _remaining = await run_in_threadpool(_check_rate, session_id)
        if not _allowed:
            async def _rate_limited():
                yield f'data: {json.dumps({"token": "⚠️ 今日免費使用次數已達上限（10次）。升級付費方案即可無限使用！", "rate_limited": True})}\n\n'
                yield f'data: {json.dumps({"done": True})}\n\n'
            return StreamingResponse(_rate_limited(), media_type="text/event-stream")

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
                # 伺服器重啟後同步恢復對話上下文
                await run_in_threadpool(_restore_session_from_db, session_id)
                _auto_activate_bazi(session_id, _bi)

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
                    BAZI_DOC.pop(session_id, None)
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
                            _store.set_horoscope_atomic(
                                session_id,
                                output.get("horoscope", ""),
                                output.get("chart_table", ""),
                                output.get("birth_info") or None,
                            )
                            # 命盤生成後存入 DB，伺服器重啟後不需重新排盤
                            if HOROSCOPE[session_id]:
                                from chat_bot.auth.auth import save_horoscope
                                save_horoscope(session_id, HOROSCOPE[session_id], CHART_TABLE.get(session_id, ""), BIRTH_INFO.get(session_id))
                                _auto_save_self_profile(session_id, HOROSCOPE[session_id], CHART_TABLE.get(session_id, ""), BIRTH_INFO.get(session_id))
                                _auto_activate_bazi(session_id, BIRTH_INFO.get(session_id))
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
                    if is_transient_error(exc):
                        reply = "⚠️ AI 服務目前請求過多，請稍後再試。"
                    else:
                        reply = "⚠️ 發生錯誤，請稍後再試。"
                    logger.exception("[stream] pipeline error")
                if _llm is not None:
                    _bg.submit_named(f"playbook:{session_id}", update_playbook, session_id, _playbook_conv(session_id), _llm)
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
                _bg.submit(_save_msg, session_id, "user", msg)
                _bg.submit(_save_msg, session_id, "ai", reply)

            return StreamingResponse(_pipeline_stream(), media_type="text/event-stream")

        # ── 已有命盤，串流 chat 回應 ──────────────────────────────────
        from chat_bot.node.chat import chat_stream

        # ── 偵測「幫別人存命盤」意圖（快速單次 LLM 呼叫）────────────
        _save_intent_data = None
        if _llm is not None:
            from chat_bot.node.save_profile_intent import detect_save_intent
            _save_intent_data = await run_in_threadpool(detect_save_intent, msg, _llm)

        if _save_intent_data is not None:
            # 意圖命中：走儲存流程（lock 內只做快速偵測，耗時排盤在 lock 外執行）
            _save_data_snap = _save_intent_data
            _save_session_snap = session_id

        else:
            SESSION_STATS.setdefault(session_id, []).append(HumanMessage(content=msg))
            _trim_history(session_id)

        import datetime as _dt_now
        messages_snapshot = list(SESSION_STATS.get(session_id, []))
        horoscope_snapshot = HOROSCOPE[session_id]
        chart_table_snapshot = CHART_TABLE.get(session_id, "")
        current_year_snapshot = _dt_now.datetime.now().year
        user_profile_snapshot = user_profile
        bazi_doc_snapshot = BAZI_DOC.get(session_id, "")

    # ── 儲存命盤流程（lock 已釋放，耗時排盤在這裡執行）─────────────
    if _save_intent_data is not None:
        label = (_save_data_snap.get("label") or "命盤").strip()

        async def _save_profile_stream():
            yield f'data: {json.dumps({"token": f"⏳ 正在幫 {label} 排命盤，請稍候…"})}\n\n'
            try:
                from chat_bot.node.save_profile_intent import execute_save_profile
                reply, profile_id, horoscope, chart_table, birth_info = await run_in_threadpool(
                    execute_save_profile, _save_data_snap, _save_session_snap
                )
            except Exception:
                logger.exception("[save_profile_stream] execute failed")
                reply, profile_id, horoscope, chart_table, birth_info = (
                    "⚠️ 排盤時發生錯誤，請稍後再試。", None, "", "", None
                )
            # 直接在後端更新 session，不依賴前端再打一次 API
            if profile_id and horoscope:
                HOROSCOPE[_save_session_snap] = horoscope
                CHART_TABLE[_save_session_snap] = chart_table
                if birth_info:
                    BIRTH_INFO[_save_session_snap] = birth_info
                SESSION_STATS.pop(_save_session_snap, None)
                NON_AGENT_THREAD.pop(_save_session_snap, None)
            yield f'data: {json.dumps({"clear": True})}\n\n'
            yield f'data: {json.dumps({"token": reply})}\n\n'
            # 送出 chart 事件讓前端更新命盤圖（與一般排盤後相同邏輯）
            if profile_id and chart_table:
                yield f'data: {json.dumps({"chart": chart_table, "birth_info": birth_info})}\n\n'
            yield f'data: {json.dumps({"done": True})}\n\n'

        return StreamingResponse(_save_profile_stream(), media_type="text/event-stream")

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
            for token in chat_stream(messages_snapshot, horoscope_snapshot, _llm, chart_table_snapshot, current_year_snapshot, user_profile_snapshot, bazi_doc_snapshot):  # Bug #5 Fix: reuse singleton
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
                _bg.submit(_save_msg, session_id, "user", msg)
                _bg.submit(_save_msg, session_id, "ai", complete)
                # 背景更新 playbook（串流完成後）
                if _llm is not None:
                    _bg.submit_named(f"playbook:{session_id}", update_playbook, session_id, _playbook_conv(session_id), _llm)
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
        _inputs = {"today": today, "horoscope": horoscope[:2500], "stars": stars}
        accumulated: list[str] = []
        async for token, err in astream_with_retry(chain, _inputs):
            if err == "__clear__":
                yield f'data: {json.dumps({"clear": True})}\n\n'
            elif err:
                logger.error("[daily-fortune] generate error: %s", err)
                yield f'data: {json.dumps({"token": err})}\n\n'
                yield f'data: {json.dumps({"done": True})}\n\n'
                return
            else:
                accumulated.append(token)
                yield f'data: {json.dumps({"token": token})}\n\n'

        content = "".join(accumulated)
        _bg.submit(save_daily_fortune, session_id, today, content)
        yield f'data: {json.dumps({"done": True})}\n\n'

    return StreamingResponse(_stream(), media_type="text/event-stream")


# ── 命盤精簡解說 API ───────────────────────────────────────────
_CHART_SUMMARY_PROMPT = """\
你是資深紫微斗數命理師。以下是命主的命盤分析資料。

請用350字左右提供一篇「命盤精簡解說」，幫命主快速掌握自己的命格全貌。
內容需涵蓋：
1. 整體命格特質與人生基調
2. 最突出的星曜組合及其影響
3. 事業與財運大方向
4. 感情與婚姻特質
5. 最重要的人生建議一句話

請以親切直接的口吻，用自然流暢的段落呈現，不要列點或表格。

命盤分析資料：
{horoscope}
{bazi_section}"""


@app.get("/api/chart-summary")
async def api_chart_summary(session_id: str = "web_default"):
    """SSE 串流：回傳命盤精簡整體解說。"""
    session_id = (session_id or "").strip() or "web_default"

    async def _stream():
        horoscope = HOROSCOPE.get(session_id, "")
        if not horoscope:
            from chat_bot.auth.auth import load_horoscope
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

        bazi_doc = BAZI_DOC.get(session_id, "")
        bazi_section = f"\n八字資料：\n{bazi_doc}\n" if bazi_doc else ""

        from langchain_core.prompts import PromptTemplate
        prompt = PromptTemplate.from_template(_CHART_SUMMARY_PROMPT)
        chain = prompt | _llm
        _inputs = {"horoscope": horoscope[:3000], "bazi_section": bazi_section}

        async for token, err in astream_with_retry(chain, _inputs):
            if err == "__clear__":
                yield f'data: {json.dumps({"clear": True})}\n\n'
            elif err:
                logger.error("[chart-summary] generate error: %s", err)
                yield f'data: {json.dumps({"token": err})}\n\n'
                yield f'data: {json.dumps({"done": True})}\n\n'
                return
            else:
                yield f'data: {json.dumps({"token": token})}\n\n'

        yield f'data: {json.dumps({"done": True})}\n\n'

    return StreamingResponse(_stream(), media_type="text/event-stream")


# ── 收藏 API ───────────────────────────────────────────────────
class BookmarkRequest(BaseModel):
    session_id: str = "web_default"
    content: str

    @field_validator("content")
    @classmethod
    def content_length(cls, v: str) -> str:
        if len(v) > 5000:
            raise ValueError("收藏內容長度不得超過 5000 字元")
        return v


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


# ── 重新生成（pop last messages）API ─────────────────────────
@app.post("/api/chat/pop-last", dependencies=[Depends(verify_api_key)])
async def api_chat_pop_last(body: ResetRequest):
    """移除 SESSION_STATS 中最後一對 AI+User 訊息，回傳最後的 user 訊息內容，供前端重新送出。"""
    session_id = (body.session_id or "").strip() or "web_default"
    session_lock = await _get_session_lock(session_id)
    async with session_lock:
        msgs = SESSION_STATS.get(session_id, [])
        last_user = None
        if msgs and isinstance(msgs[-1], AIMessage):
            msgs.pop()
        if msgs and isinstance(msgs[-1], HumanMessage):
            last_user = msgs[-1].content
            msgs.pop()
        SESSION_STATS[session_id] = msgs
    return {"last_user_msg": last_user}


# ── Push notification API ─────────────────────────────────────
class PushSubscribeRequest(BaseModel):
    session_id: str = "web_default"
    endpoint: str
    p256dh: str
    auth: str


@app.post("/api/push/subscribe")
async def api_push_subscribe(body: PushSubscribeRequest):
    session_id = (body.session_id or "").strip() or "web_default"
    from chat_bot.auth.auth import save_push_subscription
    await run_in_threadpool(save_push_subscription, session_id, body.endpoint, body.p256dh, body.auth)
    return {"status": "ok"}


@app.delete("/api/push/subscribe")
async def api_push_unsubscribe(session_id: str = "web_default", endpoint: str = ""):
    session_id = (session_id or "").strip() or "web_default"
    from chat_bot.auth.auth import delete_push_subscription
    await run_in_threadpool(delete_push_subscription, session_id, endpoint)
    return {"status": "ok"}


@app.get("/api/push/vapid-public-key")
async def api_push_vapid_key():
    return {"key": GlobalConfig.VAPID_PUBLIC_KEY}


@app.post("/api/push/send-daily", dependencies=[Depends(verify_api_key)])
async def api_push_send_daily():
    """觸發對所有訂閱用戶發送今日運勢推播（需 VAPID 金鑰）。"""
    if not GlobalConfig.VAPID_PUBLIC_KEY or not GlobalConfig.VAPID_PRIVATE_KEY:
        raise HTTPException(status_code=503, detail="VAPID 金鑰未設定")
    from chat_bot.auth.auth import load_all_push_subscriptions
    subs = await run_in_threadpool(load_all_push_subscriptions)
    if not subs:
        return {"sent": 0}

    import datetime as _dt
    today = _dt.date.today().strftime("%m/%d")

    def _send_all():
        try:
            from pywebpush import webpush, WebPushException
        except ImportError:
            logger.warning("[push] pywebpush 未安裝")
            return 0
        sent = 0
        for sub in subs:
            try:
                webpush(
                    subscription_info={
                        "endpoint": sub["endpoint"],
                        "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
                    },
                    data=json.dumps({"title": f"紫微AI {today} 今日運勢", "body": "點擊查看你的今日運勢，把握今天的好時機！", "url": "/chat"}, ensure_ascii=False),
                    vapid_private_key=GlobalConfig.VAPID_PRIVATE_KEY,
                    vapid_claims={"sub": f"mailto:{GlobalConfig.VAPID_CLAIMS_EMAIL}"},
                )
                sent += 1
            except Exception as e:
                logger.warning("[push] 推播失敗: %s", e)
        return sent

    sent = await run_in_threadpool(_send_all)
    return {"sent": sent}


# ── Message reactions API ─────────────────────────────────────
class ReactionRequest(BaseModel):
    session_id: str = "web_default"
    msg_id: str
    reaction: str  # 'like' | 'dislike' | ''


@app.post("/api/reactions")
async def api_reaction_save(body: ReactionRequest):
    if body.reaction not in ("like", "dislike", ""):
        raise HTTPException(status_code=400, detail="reaction 必須是 like、dislike 或空字串")
    session_id = (body.session_id or "").strip() or "web_default"
    from chat_bot.auth.auth import save_message_reaction
    await run_in_threadpool(save_message_reaction, session_id, body.msg_id, body.reaction)
    return {"status": "ok"}


# ── 感情合盤 API ──────────────────────────────────────────────
class PersonInfo(BaseModel):
    year: int
    month: int
    day: int
    hour: int
    is_male: bool = True

    @field_validator("year")
    @classmethod
    def year_range(cls, v: int) -> int:
        if not (1900 <= v <= 2100):
            raise ValueError("出生年份需在 1900–2100 之間")
        return v

    @field_validator("month")
    @classmethod
    def month_range(cls, v: int) -> int:
        if not (1 <= v <= 12):
            raise ValueError("月份需在 1–12 之間")
        return v

    @field_validator("day")
    @classmethod
    def day_range(cls, v: int) -> int:
        if not (1 <= v <= 31):
            raise ValueError("日期需在 1–31 之間")
        return v

    @field_validator("hour")
    @classmethod
    def hour_range(cls, v: int) -> int:
        if not (0 <= v <= 23):
            raise ValueError("時辰需在 0–23 之間")
        return v


class CompatibilityRequest(BaseModel):
    person_a: PersonInfo
    person_b: PersonInfo
    session_id: str = "web_default"
    name_a: str = "甲方"
    name_b: str = "乙方"
    horoscope_a: str = ""   # 若已有命盤分析，傳入可跳過排盤
    horoscope_b: str = ""

    @field_validator("name_a", "name_b")
    @classmethod
    def name_length(cls, v: str) -> str:
        if len(v.strip()) > 20:
            raise ValueError("名稱長度不得超過 20 字元")
        return v.strip()


@app.post("/api/compatibility")
async def api_compatibility(body: CompatibilityRequest):
    """
    輸入兩人出生資料，回傳合盤分析文字（SSE 串流）。
    使用 get_palace_information() 生成完整宮位分析，並自動儲存命盤至命盤庫。
    """
    from ziweidoushu.kernel import ZiweiChart
    from ziweidoushu.base import ZiWeiConfig
    from chat_bot.node.gen_ziwei import get_palace_information, _get_domain_df

    def _gen_full_chart(info: PersonInfo) -> tuple[str, str, str]:
        """Returns (chart_markdown, horoscope_text, header_str)"""
        cfg = ZiWeiConfig(info.year, info.month, info.day, info.hour, info.is_male)
        df = ZiweiChart(cfg).gen_chart()
        chart_md = df.to_markdown()
        horoscope = get_palace_information(df, _get_domain_df())
        gender = "男" if info.is_male else "女"
        header = f"{info.year}年{info.month}月{info.day}日 {info.hour}時 {gender}"
        chart_text = f"【{header}】\n\n命盤：\n{chart_md}\n\n命盤分析：\n{horoscope}"
        return chart_md, horoscope, chart_text

    async def _stream():
        if _llm is None:
            yield f'data: {json.dumps({"token": "⚠️ AI 服務尚未就緒，請稍後再試。"})}\n\n'
            yield f'data: {json.dumps({"done": True})}\n\n'
            return

        session_id = (body.session_id or "").strip() or "web_default"

        # 判斷哪一方需要排盤（命盤庫已有的直接使用）
        need_a = not body.horoscope_a.strip()
        need_b = not body.horoscope_b.strip()

        if need_a or need_b:
            who = "、".join(filter(None, [body.name_a if need_a else "", body.name_b if need_b else ""]))
            yield f'data: {json.dumps({"progress": f"⏳ 正在為【{who}】排命盤並分析（約 15-30 秒）…"})}\n\n'

        tasks = []
        if need_a:
            tasks.append(run_in_threadpool(_gen_full_chart, body.person_a))
        if need_b:
            tasks.append(run_in_threadpool(_gen_full_chart, body.person_b))

        results = await asyncio.gather(*tasks, return_exceptions=True) if tasks else []

        # 取出結果
        idx = 0
        if need_a:
            if isinstance(results[idx], Exception):
                logger.exception("[compat] 命盤A生成失敗", exc_info=results[idx])
                yield f'data: {json.dumps({"token": f"⚠️ 【{body.name_a}】命盤生成失敗，請確認出生資料是否正確。"})}\n\n'
                yield f'data: {json.dumps({"done": True})}\n\n'
                return
            chart_md_a, horoscope_a, chart_text_a = results[idx]
            idx += 1
        else:
            horoscope_a = body.horoscope_a
            chart_md_a = ""
            gender_a = "男" if body.person_a.is_male else "女"
            chart_text_a = f"【{body.person_a.year}年{body.person_a.month}月{body.person_a.day}日 {gender_a}】\n\n命盤分析：\n{horoscope_a}"

        if need_b:
            if isinstance(results[idx], Exception):
                logger.exception("[compat] 命盤B生成失敗", exc_info=results[idx])
                yield f'data: {json.dumps({"token": f"⚠️ 【{body.name_b}】命盤生成失敗，請確認出生資料是否正確。"})}\n\n'
                yield f'data: {json.dumps({"done": True})}\n\n'
                return
            chart_md_b, horoscope_b, chart_text_b = results[idx]
        else:
            horoscope_b = body.horoscope_b
            chart_md_b = ""
            gender_b = "男" if body.person_b.is_male else "女"
            chart_text_b = f"【{body.person_b.year}年{body.person_b.month}月{body.person_b.day}日 {gender_b}】\n\n命盤分析：\n{horoscope_b}"

        # 新排的才存入命盤庫
        saved_names: list[str] = []
        try:
            from chat_bot.auth.auth import save_profile
            if need_a:
                save_profile(session_id, body.name_a, horoscope_a, chart_md_a, body.person_a.model_dump())
                saved_names.append(body.name_a)
            if need_b:
                save_profile(session_id, body.name_b, horoscope_b, chart_md_b, body.person_b.model_dump())
                saved_names.append(body.name_b)
        except Exception as exc:
            logger.warning("[compat] 儲存命盤失敗: %s", exc)

        yield f'data: {json.dumps({"progress": "✨ AI 合盤分析中，請稍候…"})}\n\n'
        yield f'data: {json.dumps({"clear": True})}\n\n'
        if saved_names:
            yield f'data: {json.dumps({"saved": saved_names})}\n\n'

        from langchain_core.prompts import PromptTemplate
        COMPAT_PROMPT = """你是資深紫微斗數命理師，請根據以下兩份完整命盤，為兩人進行深度合盤分析。

甲方（{name_a}）命盤分析：
{chart_a}

乙方（{name_b}）命盤分析：
{chart_b}

請按以下結構輸出（Markdown 格式，繁體中文，直接以 {name_a}、{name_b} 稱呼）：

## 👤 個性特質與互補性

**{name_a}**：（核心特質 2-3 句）
**{name_b}**：（核心特質 2-3 句）
**互補分析**：（特質如何互補或碰撞）

## 💕 感情發展

**✅ 優勢**
（列出 2-3 個感情優勢，每條單獨一行，以「- 」開頭）

**⚠️ 挑戰與注意事項**
（列出 2-3 個需留意的點，每條單獨一行，以「- 」開頭）

## 💼 事業與財務合作潛力

（評估合作潛力，是否適合共同創業或財務規劃）

## ⭐ 整體相性評估

**相性指數**：X／10 分
**一句話總評**：（點評兩人關係核心）

**建議**：
- （建議一）
- （建議二）
- （建議三）

---
> **💡 合盤摘要**：（2-3 句精華，含相性指數與最關鍵特點，供後續對話參考）"""

        prompt = PromptTemplate.from_template(COMPAT_PROMPT)
        chain = prompt | _llm
        full_tokens: list[str] = []
        try:
            async for chunk in chain.astream({"chart_a": chart_text_a, "chart_b": chart_text_b,
                                               "name_a": body.name_a, "name_b": body.name_b}):
                token = chunk.content if hasattr(chunk, "content") else str(chunk)
                if token:
                    full_tokens.append(token)
                    yield f'data: {json.dumps({"token": token})}\n\n'
        except Exception:
            logger.exception("[compat] AI 分析失敗")
            yield f'data: {json.dumps({"token": "⚠️ 合盤分析失敗，請稍後再試。"})}\n\n'
            yield f'data: {json.dumps({"done": True})}\n\n'
            return

        # 抽取摘要段落並加入 SESSION_STATS 作為 AI 歷史訊息
        import re as _re
        full_text = "".join(full_tokens)
        _summary_match = _re.search(r'>\s*\*\*💡\s*合盤摘要\*\*[:：]?\s*(.*?)(?:\n\n|\Z)', full_text, _re.DOTALL)
        summary = _summary_match.group(1).strip() if _summary_match else full_text[:300].strip() + ("…" if len(full_text) > 300 else "")
        history_msg = f"[合盤分析：{body.name_a} × {body.name_b}]\n{summary}"
        SESSION_STATS.setdefault(session_id, []).append(AIMessage(content=history_msg))

        yield f'data: {json.dumps({"compat_complete": {"name_a": body.name_a, "name_b": body.name_b}})}\n\n'
        yield f'data: {json.dumps({"done": True})}\n\n'

    return StreamingResponse(_stream(), media_type="text/event-stream")


class CompatSaveRequest(BaseModel):
    session_id: str = "web_default"
    name_a: str = "甲方"
    name_b: str = "乙方"
    result: str


@app.post("/api/compat/save-to-history", dependencies=[Depends(verify_api_key)])
async def api_compat_save(body: CompatSaveRequest):
    """將合盤全文存入聊天記錄（SESSION_STATS + DB）。"""
    session_id = (body.session_id or "").strip() or "web_default"
    msg = f"## 💕 合盤分析：{body.name_a} × {body.name_b}\n\n{body.result}"
    SESSION_STATS.setdefault(session_id, []).append(AIMessage(content=msg))
    from chat_bot.auth.auth import save_chat_message
    await run_in_threadpool(save_chat_message, session_id, "ai", msg)
    return {"ok": True}


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
                    _bg.submit_named(f"playbook:{user_id}", update_playbook, user_id, _playbook_conv(user_id), _llm)
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

    @field_validator("profile_name")
    @classmethod
    def name_length(cls, v: str) -> str:
        v = v.strip()
        if not v or len(v) > 30:
            raise ValueError("命盤名稱長度需在 1-30 字元之間")
        return v


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


class MultiProfileRequest(BaseModel):
    session_id: str = "web_default"
    profile_ids: list[int]


@app.post("/api/profiles/activate-multi")
async def api_profiles_activate_multi(body: MultiProfileRequest):
    """同時啟用複數命盤，合併為當前 session 的對話上下文。"""
    from chat_bot.auth.auth import get_profile
    session_id = (body.session_id or "").strip() or "web_default"
    profiles = []
    for pid in body.profile_ids:
        p = await run_in_threadpool(get_profile, pid, session_id)
        if p and p.get("horoscope"):
            profiles.append(p)
    if not profiles:
        raise HTTPException(status_code=404, detail="找不到指定的命盤")

    if len(profiles) == 1:
        combined = profiles[0]["horoscope"]
        chart = profiles[0].get("chart_table", "")
        bi = profiles[0].get("birth_info")
    else:
        horoscope_parts = [
            f"【{p['profile_name']} 的命盤】\n{p['horoscope']}"
            for p in profiles
        ]
        combined = "\n\n---\n\n".join(horoscope_parts)
        chart_parts = [
            f"### {p['profile_name']} 的命盤\n\n{p['chart_table']}"
            for p in profiles
            if p.get("chart_table")
        ]
        chart = "\n\n---\n\n".join(chart_parts)
        bi = None

    HOROSCOPE[session_id] = combined
    CHART_TABLE[session_id] = chart
    if bi:
        BIRTH_INFO[session_id] = bi
    else:
        BIRTH_INFO.pop(session_id, None)
    SESSION_STATS.pop(session_id, None)
    NON_AGENT_THREAD.pop(session_id, None)
    return {
        "activated": [p["profile_name"] for p in profiles],
        "count": len(profiles),
        "chart_table": chart,
        "birth_info": bi,
    }


@app.delete("/api/profiles/{profile_id}")
async def api_profiles_delete(profile_id: int, session_id: str = "web_default"):
    session_id = (session_id or "").strip() or "web_default"
    from chat_bot.auth.auth import delete_profile
    await run_in_threadpool(delete_profile, profile_id, session_id)
    return {"status": "ok"}


# ── 每週運勢 API ──────────────────────────────────────────────
_WEEKLY_FORTUNE_PROMPT = """你是資深紫微斗數命理師。請根據命主命盤，為以下一週（{week_range}）生成每日運勢評分。

命盤摘要：
{horoscope}

請以嚴格的 JSON 格式回傳（不含 markdown 代碼塊）：
{{"days":[{{"date":"YYYY-MM-DD","weekday":"週一","score":82,"note":"一句概述（15字以內）"}},...],"week_summary":"本週整體運勢（2-3句）"}}

score 為 1-100 整數，各天差異要明顯，分佈自然。共需 7 天，日期依序為：{dates_list}"""


@app.get("/api/weekly-fortune")
async def api_weekly_fortune(session_id: str = "web_default", week_start: str = None):
    """回傳一週運勢 JSON（同週同 session 快取）。"""
    import datetime as _dt, json as _json, re as _re
    session_id = (session_id or "").strip() or "web_default"
    today = _dt.date.today()
    if week_start:
        try:
            ws = _dt.date.fromisoformat(week_start)
        except Exception:
            ws = today - _dt.timedelta(days=today.weekday())
    else:
        ws = today - _dt.timedelta(days=today.weekday())
    week_dates = [ws + _dt.timedelta(days=i) for i in range(7)]
    cache_key = f"week:{ws.isoformat()}"

    from chat_bot.auth.auth import get_monthly_fortune, save_monthly_fortune, load_horoscope

    cached = await run_in_threadpool(get_monthly_fortune, session_id, cache_key)
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

    weekday_names = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]
    dates_list = ", ".join(f"{d.isoformat()}({weekday_names[i]})" for i, d in enumerate(week_dates))
    week_range = f"{ws.isoformat()} 至 {week_dates[-1].isoformat()}"

    from langchain_core.prompts import PromptTemplate
    prompt = PromptTemplate.from_template(_WEEKLY_FORTUNE_PROMPT)
    chain = prompt | _llm
    try:
        result = await chain.ainvoke({"week_range": week_range, "horoscope": horoscope[:2000], "dates_list": dates_list})
        content = result.content if hasattr(result, "content") else str(result)
        match = _re.search(r'\{[\s\S]*\}', content)
        if not match:
            raise HTTPException(status_code=500, detail="AI 回傳格式錯誤")
        data = _json.loads(match.group())
        data["week_start"] = ws.isoformat()
        data["week_label"] = f"{ws.month}/{ws.day} – {week_dates[-1].month}/{week_dates[-1].day}"
        cache_str = _json.dumps(data, ensure_ascii=False)
        _bg.submit(save_monthly_fortune, session_id, cache_key, cache_str)
        return data
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[weekly-fortune] error")
        raise HTTPException(status_code=500, detail=str(exc))


# ── 運勢日曆 API ───────────────────────────────────────────────
_MONTHLY_FORTUNE_PROMPT = """你是資深紫微斗數命理師。請根據以下命主的命盤，為{year}年{month}月的每一天生成運勢評分。

命盤摘要：
{horoscope}

請以嚴格的 JSON 格式回傳，不要包含 markdown 代碼塊或其他文字：
{{"days":[{{"day":1,"score":82,"note":"一句概述"}},{{"day":2,"score":47,"note":"一句概述"}}...],"month_summary":"本月整體運勢（2-3句）"}}

score 為 1-100 整數（100最佳）。評分要求：
- 分數要真實反映命盤波動，不同日期間差異要明顯，避免大量相同分數
- 大吉（85-100）、吉（70-84）、小吉（55-69）、普通（40-54）、小注意（25-39）、注意（10-24）、凶（1-9）
- 一個月中各等級分布要自然，不要全部集中在某一區段
- 共需包含 {days_count} 天。note 控制在 15 字以內。"""


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
        _bg.submit(save_monthly_fortune, session_id, year_month, cache_str)
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


# ── 密碼重置 & 電子郵件 API ────────────────────────────────────

class ForgotPasswordRequest(BaseModel):
    email: str

    @field_validator("email")
    @classmethod
    def email_format(cls, v: str) -> str:
        import re as _re
        v = v.strip().lower()
        if not _re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", v):
            raise ValueError("請輸入有效的電子郵件地址")
        return v


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


class UpdateEmailRequest(BaseModel):
    email: str

    @field_validator("email")
    @classmethod
    def email_format(cls, v: str) -> str:
        import re as _re
        v = v.strip().lower()
        if not _re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", v):
            raise ValueError("請輸入有效的電子郵件地址")
        return v


@app.get("/reset-password", response_class=HTMLResponse)
async def reset_password_page(request: Request):
    return templates.TemplateResponse(request, "reset_password.html", {"request": request})


@app.post("/api/auth/forgot-password")
async def api_forgot_password(body: ForgotPasswordRequest, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    if not _check_login_rate(f"forgot:{client_ip}"):
        raise HTTPException(status_code=429, detail="請求過於頻繁，請 5 分鐘後再試")
    from chat_bot.auth.auth import create_password_reset_token
    result = await run_in_threadpool(create_password_reset_token, body.email)
    if result:
        raw_token, username = result
        import threading as _thr
        from chat_bot.email_service import send_password_reset_email
        _thr.Thread(
            target=send_password_reset_email,
            args=(body.email, username, raw_token),
            daemon=True,
        ).start()
    # Always return OK to prevent email enumeration
    return {"status": "ok", "message": "若此電子郵件地址已註冊，您將在幾分鐘內收到重置郵件"}


@app.post("/api/auth/reset-password")
async def api_reset_password(body: ResetPasswordRequest, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    if not _check_login_rate(f"reset:{client_ip}"):
        raise HTTPException(status_code=429, detail="請求過於頻繁，請稍後再試")
    if len(body.new_password) < 6:
        raise HTTPException(status_code=400, detail="新密碼至少需要 6 個字元")
    from chat_bot.auth.auth import verify_and_consume_reset_token
    ok = await run_in_threadpool(verify_and_consume_reset_token, body.token.strip(), body.new_password)
    if not ok:
        raise HTTPException(status_code=400, detail="重置連結無效或已過期，請重新申請")
    return {"status": "ok", "message": "密碼已成功重置，請使用新密碼登入"}


@app.post("/api/auth/update-email")
async def api_update_email(body: UpdateEmailRequest, request: Request):
    from chat_bot.auth.auth import verify_token, update_user_email, get_user_by_email
    auth_header = request.headers.get("Authorization", "")
    token = auth_header.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="未登入")
    user = verify_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Token 無效")
    # Check email not taken by another user
    existing = await run_in_threadpool(get_user_by_email, body.email)
    if existing and existing["session_id"] != user["session_id"]:
        raise HTTPException(status_code=409, detail="此電子郵件已被其他帳號使用")
    verify_token_val = await run_in_threadpool(update_user_email, user["session_id"], body.email)
    if not verify_token_val:
        raise HTTPException(status_code=500, detail="更新失敗，請稍後再試")
    import threading as _thr
    from chat_bot.email_service import send_email_verification
    from chat_bot.auth.auth import get_user_by_session as _get_u
    u_info = await run_in_threadpool(_get_u, user["session_id"])
    uname = u_info["username"] if u_info else user["username"]
    _thr.Thread(
        target=send_email_verification,
        args=(body.email, uname, verify_token_val),
        daemon=True,
    ).start()
    return {"status": "ok", "message": "驗證郵件已發送，請查收並點擊驗證連結"}


@app.get("/api/auth/verify-email")
async def api_verify_email(token: str, request: Request):
    from chat_bot.auth.auth import verify_email_token
    ok = await run_in_threadpool(verify_email_token, token.strip())
    if not ok:
        return HTMLResponse("<h3>驗證連結無效或已過期</h3><a href='/chat'>返回主頁</a>", status_code=400)
    return HTMLResponse("<h3>✅ 電子郵件驗證成功！</h3><a href='/chat'>返回主頁</a>")


@app.get("/api/auth/plan")
async def api_get_plan(request: Request):
    from chat_bot.auth.auth import verify_token, get_user_plan
    auth_header = request.headers.get("Authorization", "")
    token = auth_header.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="未登入")
    user = verify_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Token 無效")
    plan_info = await run_in_threadpool(get_user_plan, user["session_id"])
    return plan_info


# ── 綠界金流 API ───────────────────────────────────────────────

class CreateOrderRequest(BaseModel):
    plan: str

    @field_validator("plan")
    @classmethod
    def plan_valid(cls, v: str) -> str:
        if v not in ("standard", "pro"):
            raise ValueError("方案必須為 standard 或 pro")
        return v


@app.post("/api/payment/create-order")
async def api_create_order(body: CreateOrderRequest, request: Request):
    from chat_bot.auth.auth import verify_token, create_payment_order
    from chat_bot.payment.ecpay import create_order_params, is_configured, PLAN_ITEMS, CHECKOUT_URL
    auth_header = request.headers.get("Authorization", "")
    token = auth_header.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="未登入")
    user = verify_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Token 無效")
    if not is_configured():
        raise HTTPException(status_code=503, detail="金流服務尚未設定")

    # Generate unique order ID (max 20 chars: ZW + 10-digit timestamp + 8 random)
    import secrets as _sec
    order_id = f"ZW{int(time.time())}{_sec.token_hex(4).upper()}"[:20]
    amount = PLAN_ITEMS[body.plan]["amount"]

    base_url = str(request.base_url).rstrip("/")
    notify_url = f"{base_url}/api/payment/callback"
    return_url = f"{base_url}/api/payment/return"

    params = create_order_params(order_id, body.plan, notify_url, return_url)
    if not params:
        raise HTTPException(status_code=500, detail="建立訂單失敗")

    await run_in_threadpool(create_payment_order, order_id, user["session_id"], body.plan, amount)
    return {"checkout_url": CHECKOUT_URL, "params": params, "order_id": order_id}


@app.post("/api/payment/callback")
async def api_payment_callback(request: Request):
    """ECPay server-to-server callback (no JWT)."""
    from chat_bot.payment.ecpay import verify_callback
    from chat_bot.auth.auth import complete_payment_order, activate_plan, get_user_plan, _PLAN_DURATIONS
    form = await request.form()
    data = dict(form)
    if not verify_callback(data):
        return PlainTextResponse("0|CheckMacValue Error", status_code=400)
    rtn_code = data.get("RtnCode", "0")
    order_id = data.get("MerchantTradeNo", "")
    trade_no = data.get("TradeNo", "")
    if rtn_code != "1" or not order_id:
        return PlainTextResponse("1|OK")
    order = await run_in_threadpool(complete_payment_order, order_id, trade_no)
    if order:
        plan = order["plan"]
        session_id = order["session_id"]
        duration = _PLAN_DURATIONS.get(plan, 30)
        await run_in_threadpool(activate_plan, session_id, plan, duration)
        # Send receipt email in background if email available
        plan_info = await run_in_threadpool(get_user_plan, session_id)
        if plan_info.get("email") and plan_info.get("email_verified"):
            from chat_bot.auth.auth import get_user_by_session as _get_u
            from chat_bot.payment.ecpay import PLAN_ITEMS
            from chat_bot.email_service import send_payment_receipt
            import threading as _thr
            u_info = await run_in_threadpool(_get_u, session_id)
            if u_info:
                amount = PLAN_ITEMS.get(plan, {}).get("amount", 0)
                _thr.Thread(
                    target=send_payment_receipt,
                    args=(plan_info["email"], u_info["username"], plan, amount, plan_info["expires_at"] or ""),
                    daemon=True,
                ).start()
    return PlainTextResponse("1|OK")


@app.get("/api/payment/return")
async def api_payment_return(request: Request):
    rtn_code = request.query_params.get("RtnCode", "0")
    order_id = request.query_params.get("MerchantTradeNo", "")
    if rtn_code == "1":
        html = f"""<html><head><meta http-equiv="refresh" content="3;url=/chat?payment=success"></head>
<body style="font-family:sans-serif;text-align:center;padding:40px;background:#060e1c;color:#e8dcc8">
<h2 style="color:#c9a227">✅ 付款成功！</h2>
<p>訂單編號：{order_id}</p>
<p>正在跳轉回主頁…</p></body></html>"""
    else:
        html = f"""<html><head><meta http-equiv="refresh" content="3;url=/chat?payment=fail"></head>
<body style="font-family:sans-serif;text-align:center;padding:40px;background:#060e1c;color:#e8dcc8">
<h2 style="color:#ff7b7b">❌ 付款未完成</h2>
<p>若有疑問請聯絡客服</p>
<p>正在跳轉回主頁…</p></body></html>"""
    return HTMLResponse(html)


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
    except Exception:
        logger.exception("[life-events] error")
        raise HTTPException(status_code=500, detail="人生節點分析失敗，請稍後再試")


# ── 流年 / 流月分析 API ────────────────────────────────────────
_ANNUAL_FORTUNE_PROMPT = """你是資深紫微斗數命理師。請根據命主命盤，為{year}年做完整的流年分析。

命盤資料：
{horoscope}

請以嚴格的 JSON 格式回傳（不含 markdown 代碼塊）：
{{"year_summary":"流年整體分析（3-4句，繁體中文）","overall_score":75,"key_advice":"今年最重要建議（一句話）","months":[{{"month":1,"score":82,"note":"本月概述（15字以內）"}},...]}}

overall_score 與 score 均為 1-100 整數。共需包含 12 個月（month 1-12）。各月分數差異要明顯，分佈自然。"""

_MONTHLY_DETAIL_PROMPT = """你是資深紫微斗數命理師。請根據命主命盤，為{year}年{month}月做詳細的流月分析。

命盤資料：
{horoscope}

請按以下格式回答（繁體中文，內容詳盡）：

## {year}年{month}月流月分析 {stars}

**本月整體概述：** （2-3句說明本月主要氣場與機遇）

| 面向 | 運勢 | 本月重點 |
|------|------|---------|
| 💕 感情 | ★★★★☆ | 一句重點 |
| 💼 事業 | ★★★☆☆ | 一句重點 |
| 💰 財運 | ★★★★☆ | 一句重點 |
| ❤️ 健康 | ★★★☆☆ | 一句重點 |

**本月吉日：** 列出3-5個吉日（僅日期數字，例：3、9、17）

**注意事項：** 1-2件本月需特別留意的事

**行動建議：** 具體的月度建議（2-3條）"""


@app.get("/api/annual-fortune")
async def api_annual_fortune(session_id: str = "web_default", year: int = None):
    """回傳流年分析 JSON（同年同 session 快取）。"""
    import datetime as _dt, json as _json, re as _re
    session_id = (session_id or "").strip() or "web_default"
    if year is None:
        year = _dt.date.today().year
    if not (1900 <= year <= 2200):
        raise HTTPException(status_code=400, detail="年份需在 1900–2200 之間")

    cache_key = f"annual:{year}"
    from chat_bot.auth.auth import get_annual_fortune, save_annual_fortune, load_horoscope

    cached = await run_in_threadpool(get_annual_fortune, session_id, cache_key)
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
    prompt = PromptTemplate.from_template(_ANNUAL_FORTUNE_PROMPT)
    chain = prompt | _llm
    try:
        result = await chain.ainvoke({"year": year, "horoscope": horoscope[:2500]})
        content = result.content if hasattr(result, "content") else str(result)
        match = _re.search(r'\{[\s\S]*\}', content)
        if not match:
            raise HTTPException(status_code=500, detail="AI 回傳格式錯誤")
        data = _json.loads(match.group())
        data["year"] = year
        _bg.submit(save_annual_fortune, session_id, cache_key, _json.dumps(data, ensure_ascii=False))
        return data
    except HTTPException:
        raise
    except Exception:
        logger.exception("[annual-fortune] error")
        raise HTTPException(status_code=500, detail="流年分析失敗，請稍後再試")


@app.get("/api/monthly-detail")
async def api_monthly_detail(session_id: str = "web_default", year: int = None, month: int = None):
    """SSE 串流：流月詳細分析（同年月同 session 快取）。"""
    import datetime as _dt
    session_id = (session_id or "").strip() or "web_default"
    today = _dt.date.today()
    if year is None:
        year = today.year
    if month is None:
        month = today.month
    if not (1900 <= year <= 2200):
        raise HTTPException(status_code=400, detail="年份需在 1900–2200 之間")
    if not (1 <= month <= 12):
        raise HTTPException(status_code=400, detail="月份需在 1–12 之間")

    cache_key = f"monthly_detail:{year}-{month:02d}"
    from chat_bot.auth.auth import get_annual_fortune, save_annual_fortune, load_horoscope

    async def _stream():
        cached = await run_in_threadpool(get_annual_fortune, session_id, cache_key)
        if cached:
            yield f'data: {json.dumps({"token": cached})}\n\n'
            yield f'data: {json.dumps({"done": True, "cached": True})}\n\n'
            return

        horoscope = HOROSCOPE.get(session_id, "")
        if not horoscope:
            _h, _ct, _bi = await run_in_threadpool(load_horoscope, session_id)
            horoscope = _h or ""
        if not horoscope:
            yield f'data: {json.dumps({"token": "⚠️ 尚未建立命盤，請先輸入出生資料排盤。"})}\n\n'
            yield f'data: {json.dumps({"done": True})}\n\n'
            return
        if _llm is None:
            yield f'data: {json.dumps({"token": "⚠️ AI 服務尚未就緒，請稍後再試。"})}\n\n'
            yield f'data: {json.dumps({"done": True})}\n\n'
            return

        import random
        stars_options = ["★★★★☆", "★★★☆☆", "★★★★★", "★★★☆☆", "★★★★☆"]
        stars = random.choice(stars_options)
        from langchain_core.prompts import PromptTemplate
        prompt = PromptTemplate.from_template(_MONTHLY_DETAIL_PROMPT)
        chain = prompt | _llm
        _inputs = {"year": year, "month": month, "horoscope": horoscope[:2500], "stars": stars}
        accumulated: list[str] = []
        async for token, err in astream_with_retry(chain, _inputs):
            if err == "__clear__":
                yield f'data: {json.dumps({"clear": True})}\n\n'
            elif err:
                logger.error("[monthly-detail] generate error: %s", err)
                yield f'data: {json.dumps({"token": err})}\n\n'
                yield f'data: {json.dumps({"done": True})}\n\n'
                return
            else:
                accumulated.append(token)
                yield f'data: {json.dumps({"token": token})}\n\n'

        full_content = "".join(accumulated)
        _bg.submit(save_annual_fortune, session_id, cache_key, full_content)
        yield f'data: {json.dumps({"done": True})}\n\n'

    return StreamingResponse(_stream(), media_type="text/event-stream")


# ── 八字排盤 API ──────────────────────────────────────────────
_BAZI_KNOWLEDGE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "knowledge", "bazi.md"
)

# Names returned by compute_shenshas() that differ from their bazi.md entry names
_SHENSHA_ALIASES: dict[str, str] = {
    "六甲空亡": "空亡",    # bazi.md entry: 空亡煞 (contains "空亡" substring)
    "八專日":   "八專",    # bazi.md entry: 八專 (no 日 suffix)
    "六秀日":   "六秀",    # bazi.md entry: 六秀 (no 日 suffix)
    "德秀貴人": "天德貴人", # derived; falls back to 天德貴人 entry
    "陰差陽錯": "陰陽差錯", # bazi.md entry: 陰陽差錯
}

# Module-level RAG cache: (core_text, {name: entry_text})
_BAZI_RAG_CACHE: tuple[str, dict[str, str]] | None = None


def _build_bazi_rag() -> tuple[str, dict[str, str]]:
    """Parse bazi.md into (core_text, {shensha_name: entry_text}).

    core_text: 核心 + 定義 sections — always injected (small, foundational).
    index: each numbered 神煞 entry keyed by its name.
    Cached after first call.
    """
    global _BAZI_RAG_CACHE
    if _BAZI_RAG_CACHE is not None:
        return _BAZI_RAG_CACHE

    core_text = ""
    index: dict[str, str] = {}
    try:
        with open(_BAZI_KNOWLEDGE_FILE, encoding="utf-8") as f:
            raw = f.read()

        # Split 核心/定義 sections from 神煞要義
        m = re.search(r"###\s*神煞要義", raw)
        core_text = raw[: m.start()].strip() if m else raw.strip()
        shenshas_raw = raw[m.end() :] if m else ""

        if shenshas_raw:
            # Split into numbered entries; each starts with digits + Chinese/Western punct
            chunks = re.split(r"\n(?=\d+[，,、\. ])", shenshas_raw)
            for chunk in chunks:
                chunk = chunk.strip().rstrip(",，")
                if not chunk:
                    continue
                # Extract name: strip numeric prefix, capture pure CJK characters only
                # so punctuation like 、 in "天德貴人、天德合" doesn't contaminate the name
                nm = re.match(r"^\d+[，,、\. ]+([一-鿿]{2,8})", chunk)
                if not nm:
                    continue
                name = nm.group(1)
                # Cap each entry at 500 chars to stay within token budget
                entry = chunk if len(chunk) <= 500 else chunk[:500] + "…"
                index[name] = entry

        logger.info("BaZi RAG index built: core=%d chars, %d entries", len(core_text), len(index))
    except Exception:
        logger.warning("Failed to build BaZi RAG index", exc_info=True)

    _BAZI_RAG_CACHE = (core_text, index)
    return _BAZI_RAG_CACHE


def _load_bazi_knowledge_for_chart(bazi_data: dict) -> str:
    """Return bazi.md sections relevant to this chart's 神煞 — Ziwei-style RAG.

    Mirrors how Ziwei RAG works: extract which 神煞 are present in the chart
    (from compute_shenshas), look them up in the bazi.md index, inject only
    those entries.  Reduces tokens by ~90 % compared to injecting the full file.
    """
    core_text, index = _build_bazi_rag()

    # Collect all shensha names present across the four pillars
    names: set[str] = set()
    for pillar_list in bazi_data.get("shenshas", {}).values():
        names.update(pillar_list)

    if not names:
        return core_text

    matched: list[str] = []
    seen_ids: set[int] = set()   # deduplicate entries shared by multiple name lookups

    for name in sorted(names):
        lookup = _SHENSHA_ALIASES.get(name, name)

        # 1. Exact key match
        entry = index.get(lookup)

        # 2. Substring match: lookup ⊆ key  or  key ⊆ lookup  (e.g. 元辰 ↔ 元辰之歲)
        if entry is None:
            entry = next(
                (v for k, v in index.items() if lookup in k or k in lookup),
                None,
            )

        # 3. Entry-text match: lookup appears in the first 300 chars of any entry
        #    (catches cases like 天德合 found inside the 天德貴人 entry body)
        if entry is None:
            entry = next(
                (v for v in index.values() if lookup in v[:300]),
                None,
            )

        if entry is not None:
            eid = id(entry)
            if eid not in seen_ids:
                seen_ids.add(eid)
                matched.append(entry)
        else:
            logger.debug("BaZi RAG: no entry found for %r (lookup=%r)", name, lookup)

    if not matched:
        return core_text

    return core_text + "\n\n【命盤相關神煞知識】\n" + "\n\n".join(matched)

_BAZI_INTERPRET_PROMPT = """你是資深八字命理師（BaZi / Four Pillars）。以下是命主的八字命盤，請做精要的命盤解析。
{knowledge_doc}
【八字命盤】
年柱：{year_gz}　月柱：{month_gz}　日柱：{day_gz}　時柱：{hour_gz}

日主（命主）：{day_master}（{day_master_element}）
五行分佈：{wuxing}

出生時間：{birth_dt}（真太陽時：{solar_time}，校正 {correction} 分鐘）
時辰：{shichen}

請依以下架構分析（繁體中文，精簡有力）：

## 🔯 八字命盤 {day_master}日主

**日主特質：** （一句描述日主天干的個性）

**五行喜忌：**
- 旺相衰弱分析（一句）
- 喜用神建議（一句）

| 面向 | 分析 |
|------|------|
| 💼 事業財運 | 一句重點 |
| 💕 感情婚姻 | 一句重點 |
| ❤️ 健康養生 | 一句重點 |
| 🌟 人生建議 | 一句重點 |

**命盤亮點：** 2-3個最突出的命盤特徵；若上方神煞知識中有吉神（如貴人、文昌）或凶煞（如劫煞、桃花），各點出最重要的1-2個並說明對命主的影響。

**開運建議：** 1-2條具體可行的建議（顏色、方位、行業等）"""


class BaziRequest(BaseModel):
    year: int
    month: int
    day: int
    hour: int = 0
    minute: int = 0
    longitude: float = 121.5
    is_male: int = 1          # 1=男, 0=女
    name: str = "命主"
    session_id: str = "web_default"

    @field_validator("year")
    @classmethod
    def year_range(cls, v):
        if not (1900 <= v <= 2100):
            raise ValueError("出生年份需在 1900–2100 之間")
        return v

    @field_validator("month")
    @classmethod
    def month_range(cls, v):
        if not (1 <= v <= 12):
            raise ValueError("月份需在 1–12 之間")
        return v

    @field_validator("day")
    @classmethod
    def day_range(cls, v):
        if not (1 <= v <= 31):
            raise ValueError("日期需在 1–31 之間")
        return v

    @field_validator("hour")
    @classmethod
    def hour_range(cls, v):
        if not (0 <= v <= 23):
            raise ValueError("小時需在 0–23 之間")
        return v

    @field_validator("minute")
    @classmethod
    def minute_range(cls, v):
        if not (0 <= v <= 59):
            raise ValueError("分鐘需在 0–59 之間")
        return v

    @field_validator("longitude")
    @classmethod
    def lon_range(cls, v):
        if not (-180 <= v <= 180):
            raise ValueError("經度需在 -180–180 之間")
        return v

    @field_validator("name")
    @classmethod
    def name_len(cls, v):
        return (v or "命主").strip()[:20] or "命主"


@app.post("/api/bazi/chart")
async def api_bazi_chart(body: BaziRequest):
    """計算八字命盤（純算法，無 AI），立即回傳 JSON。"""
    from chat_bot.utils.bazi import compute_bazi, wuxing_count, day_master
    try:
        result = await run_in_threadpool(
            compute_bazi,
            body.year, body.month, body.day,
            body.hour, body.minute, body.longitude,
        )
        result["wuxing"] = wuxing_count(result)
        result["day_master"] = day_master(result)
        return result
    except Exception as exc:
        logger.exception("[bazi-chart] error")
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/bazi/activate")
async def api_bazi_activate(body: BaziRequest):
    """將八字命盤存入 session，之後的對話會自動帶入 bazi_doc 上下文。"""
    from chat_bot.utils.bazi import compute_bazi, wuxing_count, day_master, _STEM_ELEMENT
    try:
        bazi = await run_in_threadpool(
            compute_bazi,
            body.year, body.month, body.day,
            body.hour, body.minute, body.longitude,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    dm = day_master(bazi)
    dm_elem = _STEM_ELEMENT[bazi["day_pillar"]["stem_idx"]]
    wx = wuxing_count(bazi)
    wx_str = " ".join(f"{k}{v}個" for k, v in wx.items() if v > 0)
    cg = bazi.get("canggan", {})
    cang_str = " ".join(
        f"{'年月日時'[i]}[{'/'.join(cg.get(k, [])) or '—'}]"
        for i, k in enumerate(["year_pillar","month_pillar","day_pillar","hour_pillar"])
    )
    gender = "男" if body.is_male else "女"

    doc = (
        f"姓名：{body.name}（{gender}）\n"
        f"年柱：{bazi['year_pillar']['ganzhi']}　月柱：{bazi['month_pillar']['ganzhi']}　"
        f"日柱：{bazi['day_pillar']['ganzhi']}　時柱：{bazi['hour_pillar']['ganzhi']}\n"
        f"日主：{dm}（{dm_elem}）　時辰：{bazi['shichen']}\n"
        f"五行：{wx_str}\n"
        f"藏幹：{cang_str}\n"
        f"真太陽時：{bazi['solar_hour']:02d}:{bazi['solar_minute']:02d}"
    )
    BAZI_DOC[body.session_id] = doc
    return {"status": "ok"}


@app.post("/api/bazi/dayun")
async def api_bazi_dayun(body: BaziRequest):
    """計算大運（10年大運）。"""
    from chat_bot.utils.bazi import compute_dayun
    try:
        result = await run_in_threadpool(
            compute_dayun,
            body.year, body.month, body.day,
            body.hour, body.minute,
            bool(body.is_male),
            body.longitude,
        )
        return result
    except Exception as exc:
        logger.exception("[bazi-dayun] error")
        raise HTTPException(status_code=400, detail=str(exc))


class BaziProfileRequest(BaseModel):
    session_id: str = "web_default"
    profile_name: str
    year: int
    month: int
    day: int
    hour: int = 0
    minute: int = 0
    longitude: float = 121.5
    is_male: int = 1
    bazi_json: str = "{}"

    @field_validator("profile_name")
    @classmethod
    def name_len(cls, v):
        v = (v or "").strip()[:20]
        if not v:
            raise ValueError("命盤名稱不得為空")
        return v


@app.post("/api/bazi/profiles")
async def api_bazi_profile_save(body: BaziProfileRequest):
    session_id = (body.session_id or "").strip() or "web_default"
    from chat_bot.auth.auth import save_bazi_profile
    pid = await run_in_threadpool(
        save_bazi_profile,
        session_id, body.profile_name,
        body.year, body.month, body.day,
        body.hour, body.minute, body.longitude,
        body.is_male, body.bazi_json,
    )
    if pid < 0:
        raise HTTPException(status_code=500, detail="儲存失敗")
    return {"id": pid, "status": "ok"}


@app.get("/api/bazi/profiles")
async def api_bazi_profile_list(session_id: str = "web_default"):
    session_id = (session_id or "").strip() or "web_default"
    from chat_bot.auth.auth import load_bazi_profiles
    profiles = await run_in_threadpool(load_bazi_profiles, session_id)
    return {"profiles": profiles}


@app.delete("/api/bazi/profiles/{profile_id}")
async def api_bazi_profile_delete(profile_id: int, session_id: str = "web_default"):
    session_id = (session_id or "").strip() or "web_default"
    from chat_bot.auth.auth import delete_bazi_profile
    ok = await run_in_threadpool(delete_bazi_profile, profile_id, session_id)
    return {"status": "ok" if ok else "not_found"}


class BaziSaveHistoryRequest(BaseModel):
    session_id: str = "web_default"
    name: str = "命主"
    bazi_summary: str


@app.post("/api/bazi/save-to-history")
async def api_bazi_save_history(body: BaziSaveHistoryRequest):
    session_id = (body.session_id or "").strip() or "web_default"
    from chat_bot.auth.auth import save_chat_message
    content = body.bazi_summary[:3000]
    await run_in_threadpool(save_chat_message, session_id, "ai", content)
    return {"status": "ok"}


@app.post("/api/bazi/interpret")
async def api_bazi_interpret(body: BaziRequest):
    """SSE 串流：AI 解盤八字命盤。"""
    from chat_bot.utils.bazi import compute_bazi, wuxing_count, day_master, _STEM_ELEMENT

    if _llm is None:
        raise HTTPException(status_code=503, detail="AI 服務未就緒")

    try:
        bazi = await run_in_threadpool(
            compute_bazi,
            body.year, body.month, body.day,
            body.hour, body.minute, body.longitude,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    dm = day_master(bazi)
    dm_elem = _STEM_ELEMENT[bazi["day_pillar"]["stem_idx"]]
    wx = wuxing_count(bazi)
    wx_str = " ".join(f"{k}{v}個" for k, v in wx.items() if v > 0)
    solar_time = f"{bazi['solar_hour']:02d}:{bazi['solar_minute']:02d}"
    birth_dt = f"{body.year}年{body.month}月{body.day}日 {body.hour:02d}:{body.minute:02d}"

    knowledge = await run_in_threadpool(_load_bazi_knowledge_for_chart, bazi)
    knowledge_doc = f"\n【八字命理知識（精選）】\n{knowledge}\n\n" if knowledge else ""

    _inputs = {
        "knowledge_doc": knowledge_doc,
        "year_gz":   bazi["year_pillar"]["ganzhi"],
        "month_gz":  bazi["month_pillar"]["ganzhi"],
        "day_gz":    bazi["day_pillar"]["ganzhi"],
        "hour_gz":   bazi["hour_pillar"]["ganzhi"],
        "day_master": dm,
        "day_master_element": dm_elem,
        "wuxing": wx_str,
        "birth_dt": birth_dt,
        "solar_time": solar_time,
        "correction": bazi["correction_minutes"],
        "shichen": bazi["shichen"],
    }

    async def _stream():
        from langchain_core.prompts import PromptTemplate
        prompt = PromptTemplate.from_template(_BAZI_INTERPRET_PROMPT)
        chain = prompt | _llm
        async for token, err in astream_with_retry(chain, _inputs):
            if err == "__clear__":
                yield f'data: {json.dumps({"clear": True})}\n\n'
            elif err:
                logger.error("[bazi-interpret] error: %s", err)
                yield f'data: {json.dumps({"token": err})}\n\n'
                yield f'data: {json.dumps({"done": True})}\n\n'
                return
            else:
                yield f'data: {json.dumps({"token": token})}\n\n'
        yield f'data: {json.dumps({"done": True, "bazi": bazi})}\n\n'

    return StreamingResponse(_stream(), media_type="text/event-stream")


# ── 大運 AI 解析 ────────────────────────────────────────────────
_DAYUN_INTERPRET_PROMPT = """\
你是資深八字命理師。以下是命主的八字資料與即將解析的大運期間。

命主出生：{birth_dt}
性別：{is_male}
大運：{dayun_gz}（{start_age}歲至{end_age}歲）

請用150字左右，說明這段大運對命主的影響：
1. 此干支組合的五行特性與象意
2. 對命主事業、財運、感情的主要影響方向
3. 需要注意的潛在挑戰或機遇

用簡潔流暢的口吻，不要列點，直接成段。"""

class DayunInterpretRequest(BaseModel):
    year: int
    month: int
    day: int
    hour: int
    minute: int = 0
    longitude: float = 120.0
    is_male: int = 1
    session_id: str = "web_default"
    dayun_stem: str
    dayun_branch: str
    start_age: float
    end_age: float


@app.post("/api/bazi/dayun-interpret")
async def api_bazi_dayun_interpret(body: DayunInterpretRequest):
    """SSE 串流：AI 解析指定大運期間的影響。"""
    if _llm is None:
        raise HTTPException(status_code=503, detail="AI 服務未就緒")

    birth_dt = f"{body.year}年{body.month}月{body.day}日 {body.hour:02d}時"
    dayun_gz = f"{body.dayun_stem}{body.dayun_branch}"
    is_male_str = "男" if body.is_male else "女"

    _inputs = {
        "birth_dt": birth_dt,
        "is_male": is_male_str,
        "dayun_gz": dayun_gz,
        "start_age": int(body.start_age),
        "end_age": int(body.end_age),
    }

    async def _stream():
        from langchain_core.prompts import PromptTemplate
        prompt = PromptTemplate.from_template(_DAYUN_INTERPRET_PROMPT)
        chain = prompt | _llm
        async for token, err in astream_with_retry(chain, _inputs, max_retries=2):
            if err and err != "__clear__":
                yield f'data: {json.dumps({"token": err})}\n\n'
                return
            elif err != "__clear__" and token:
                yield f'data: {json.dumps({"token": token})}\n\n'

    return StreamingResponse(_stream(), media_type="text/event-stream")


# ── 完整命盤宮位解說 ─────────────────────────────────────────────
@app.get("/api/horoscope")
async def api_get_horoscope(session_id: str = "web_default"):
    """回傳命主的完整紫微斗數宮位解說文字。"""
    session_id = (session_id or "").strip() or "web_default"
    horoscope = HOROSCOPE.get(session_id, "")
    if not horoscope:
        from chat_bot.auth.auth import load_horoscope
        result = await run_in_threadpool(load_horoscope, session_id)
        horoscope = result[0] or ""
    return {"horoscope": horoscope}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=False)
