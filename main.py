import asyncio
import json
import logging
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
AGENT_THREAD: dict = {}
NON_AGENT_THREAD: dict = {}    # Bug #1 Fix: non-agent 模式的 LangGraph thread_id，重置時換新 UUID
_SESSION_LAST_SEEN: dict = {}  # Fix #5: session 最後活躍時間戳

# 每個 session 最多保留的訊息輪數（fix #9：防止無限成長）
_MAX_HISTORY = 20
# Fix #5：session 閒置超過此秒數就清除（預設 2 小時）
_SESSION_TTL_SECS = 7200


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pipeline, _llm
    from chat_bot.utils.utils import build_llm
    _pipeline = LLMProcessor().set_pipeline()
    _llm = build_llm(GlobalConfig.MODEL)  # Bug #5 Fix: build once, reuse in _stream_worker
    yield


app = FastAPI(title="紫微斗數 API", lifespan=lifespan)
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
        AGENT_THREAD.pop(sid, None)
        NON_AGENT_THREAD.pop(sid, None)
        _SESSION_LAST_SEEN.pop(sid, None)


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

        if GlobalConfig.USE_AGENT_SKILL:
            if is_reset or session_id not in AGENT_THREAD:
                AGENT_THREAD[session_id] = str(uuid.uuid4())
                if is_reset:
                    return {"reply": "✅ 對話已重置！請重新告訴我你的出生年月日、時辰和性別。"}

            config = {"configurable": {"thread_id": AGENT_THREAD[session_id]}}
            output = await run_in_threadpool(
                _pipeline.invoke, {"messages": [HumanMessage(content=msg)]}, config
            )
            reply = _extract_last_message(output)

        else:
            # Bug #1 Fix: 重置時換新 thread_id，LangGraph MemorySaver 不再恢復舊 birth_info
            if session_id not in HOROSCOPE or is_reset:
                NON_AGENT_THREAD[session_id] = str(uuid.uuid4())
                HOROSCOPE[session_id] = ""
                SESSION_STATS[session_id] = []
                if is_reset:
                    return {"reply": "✅ 對話已重置！請重新告訴我你的出生年月日、時辰和性別。"}

            thread_id = NON_AGENT_THREAD.setdefault(session_id, str(uuid.uuid4()))
            config = {"configurable": {"thread_id": thread_id, "session_id": session_id}}

            SESSION_STATS[session_id].append(HumanMessage(content=msg))
            _trim_history(session_id)

            output = await run_in_threadpool(
                _pipeline.invoke,
                {"messages": _pipeline_messages(session_id), "horoscope": HOROSCOPE[session_id]},
                config,
            )
            if not HOROSCOPE[session_id]:
                HOROSCOPE[session_id] = output.get("horoscope", "")

            reply = _extract_last_message(output)
            SESSION_STATS[session_id].append(AIMessage(content=reply))

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
    """清除指定 session 的對話記憶"""
    # Bug #11 Fix: strip whitespace before fallback so "   " doesn't create a phantom session
    session_id = (body.session_id or "").strip() or "web_default"
    SESSION_STATS.pop(session_id, None)
    HOROSCOPE.pop(session_id, None)
    AGENT_THREAD.pop(session_id, None)
    NON_AGENT_THREAD.pop(session_id, None)  # Bug #1 Fix: 清除 non-agent thread_id
    _SESSION_LAST_SEEN.pop(session_id, None)
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

    # ── 若尚未有命盤 or 需要重置，走一般 pipeline（非串流）─────────
    horoscope_ready = (
        session_id in HOROSCOPE
        and bool(HOROSCOPE[session_id])
        and not is_reset
        and not GlobalConfig.USE_AGENT_SKILL
    )

    if not horoscope_ready:
        # Run the full pipeline synchronously in a thread
        def _run():
            # Bug R4#5 Fix: agent 模式使用 AGENT_THREAD，與 api_chat 邏輯一致
            if GlobalConfig.USE_AGENT_SKILL:
                if is_reset or session_id not in AGENT_THREAD:
                    AGENT_THREAD[session_id] = str(uuid.uuid4())
                    if is_reset:
                        return "✅ 對話已重置！請重新告訴我你的出生年月日、時辰和性別。"
                config = {"configurable": {"thread_id": AGENT_THREAD[session_id]}}
                output = _pipeline.invoke({"messages": [HumanMessage(content=msg)]}, config)
                return _extract_last_message(output)

            if is_reset or session_id not in HOROSCOPE:
                # Bug #1 Fix: 重置時換新 thread_id，清除 MemorySaver 的舊 birth_info
                NON_AGENT_THREAD[session_id] = str(uuid.uuid4())
                HOROSCOPE[session_id] = ""
                SESSION_STATS[session_id] = []
                if is_reset:
                    return "✅ 對話已重置！請重新告訴我你的出生年月日、時辰和性別。"
            SESSION_STATS.setdefault(session_id, [])
            SESSION_STATS[session_id].append(HumanMessage(content=msg))
            _trim_history(session_id)
            thread_id = NON_AGENT_THREAD.setdefault(session_id, str(uuid.uuid4()))
            config = {"configurable": {"thread_id": thread_id, "session_id": session_id}}
            output = _pipeline.invoke(
                {"messages": _pipeline_messages(session_id), "horoscope": HOROSCOPE[session_id]},
                config=config,
            )
            if not HOROSCOPE[session_id]:
                HOROSCOPE[session_id] = output.get("horoscope", "")
            reply = _extract_last_message(output)
            SESSION_STATS[session_id].append(AIMessage(content=reply))
            return reply

        try:
            reply = await run_in_threadpool(_run)
        except Exception as e:
            # Bug #8 Fix: surface rate-limit errors distinctly, same as non-stream endpoint
            if "RESOURCE_EXHAUSTED" in str(e) or "429" in str(e):
                reply = "⚠️ AI 服務目前請求過多，請稍後再試。"
            else:
                reply = "⚠️ 發生錯誤，請稍後再試。"
            logger.exception("[stream] pipeline error")

        async def _single(r):
            yield f'data: {json.dumps({"token": r})}\n\n'
            yield f'data: {json.dumps({"done": True})}\n\n'
        return StreamingResponse(_single(reply), media_type="text/event-stream")

    # ── 已有命盤，串流 chat 回應 ──────────────────────────────────
    from chat_bot.node.chat import chat_stream

    SESSION_STATS[session_id].append(HumanMessage(content=msg))
    _trim_history(session_id)

    messages_snapshot = list(SESSION_STATS[session_id])
    horoscope_snapshot = HOROSCOPE[session_id]

    token_q: _queue.Queue = _queue.Queue()

    def _stream_worker():
        try:
            # Bug R4#6 Fix: guard against _llm being None (e.g. lifespan startup failed)
            if _llm is None:
                token_q.put("⚠️ 服務尚未就緒，請稍後再試。")
                return
            for token in chat_stream(messages_snapshot, horoscope_snapshot, _llm):  # Bug #5 Fix: reuse singleton
                token_q.put(token)
        except Exception:
            logger.exception("[stream] chat_stream error")
            # Bug #8 Fix: 將錯誤訊息送入 queue，前端可顯示而非留空白 bubble
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
            # Bug R4#2 Fix: 無論是否中途斷線都要清理 SESSION_STATS，
            # 避免 HumanMessage 孤立在歷史中破壞下一輪對話 context
            if full_reply:
                complete = "".join(full_reply)
                SESSION_STATS[session_id].append(AIMessage(content=complete))
                _trim_history(session_id)
            elif SESSION_STATS.get(session_id) and isinstance(
                SESSION_STATS[session_id][-1], HumanMessage
            ):
                # 沒有收到任何 token（斷線前 LLM 未回應）→ 移除懸空的 HumanMessage
                SESSION_STATS[session_id].pop()

    return StreamingResponse(_token_generator(), media_type="text/event-stream")


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
        if _line_bot_api is None:
            if not GlobalConfig.LINE_ACCESS_KEY or not GlobalConfig.LINE_SECRET:
                logger.warning("LINE_ACCESS_KEY 或 LINE_SECRET 未設定，略過 webhook 處理")
                return "OK"
            _line_bot_api = LineBotApi(GlobalConfig.LINE_ACCESS_KEY)
            _line_handler = WebhookHandler(GlobalConfig.LINE_SECRET)
        line_bot_api = _line_bot_api
        handler = _line_handler
        handler.handle(body_text, request.headers.get("X-Line-Signature", ""))

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
                        {"messages": _pipeline_messages(user_id), "horoscope": HOROSCOPE[user_id]},
                        config,
                    )
                    if not HOROSCOPE[user_id]:
                        HOROSCOPE[user_id] = output.get("horoscope", "")

                    reply = _extract_last_message(output)
                    SESSION_STATS[user_id].append(AIMessage(content=reply))
                    logger.debug("[linebot] cur_state: %s", SESSION_STATS[user_id])

                logger.debug("[linebot] reply: %.100s", reply)
                line_bot_api.reply_message(tk, TextSendMessage(reply))

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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=False)
