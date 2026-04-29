import json
import logging
import time
import traceback
import uuid
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

# Fix #1：load_dotenv 必須在所有 chat_bot 模組 import 前呼叫，
# 否則 GlobalConfig 的 os.environ.get() 讀不到 .env 的值
from dotenv import load_dotenv
load_dotenv()

from fastapi import Depends, FastAPI, HTTPException, Request, Security, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse
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
_line_bot_api: "LineBotApi | None" = None   # Fix #9：lazy singleton
_line_handler: "WebhookHandler | None" = None  # Fix #9：lazy singleton
SESSION_STATS: dict = {}
HOROSCOPE: dict = {}
AGENT_THREAD: dict = {}
_SESSION_LAST_SEEN: dict = {}  # Fix #5: session 最後活躍時間戳

# 每個 session 最多保留的訊息輪數（fix #9：防止無限成長）
_MAX_HISTORY = 20
# Fix #5：session 閒置超過此秒數就清除（預設 2 小時）
_SESSION_TTL_SECS = 7200


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pipeline
    _pipeline = LLMProcessor().set_pipeline()
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
    """Fix #9：超過上限時，保留最新的 _MAX_HISTORY 條訊息。"""
    msgs = SESSION_STATS.get(session_id, [])
    if len(msgs) > _MAX_HISTORY:
        SESSION_STATS[session_id] = msgs[-_MAX_HISTORY:]


def _touch_session(session_id: str) -> None:
    """Fix #5：更新 session 活躍時間。"""
    _SESSION_LAST_SEEN[session_id] = time.time()


def _evict_stale_sessions() -> None:
    """Fix #5：清除超過 TTL 的閒置 sessions，防止記憶體無限成長。"""
    now = time.time()
    stale = [sid for sid, ts in _SESSION_LAST_SEEN.items() if now - ts > _SESSION_TTL_SECS]
    for sid in stale:
        SESSION_STATS.pop(sid, None)
        HOROSCOPE.pop(sid, None)
        AGENT_THREAD.pop(sid, None)
        _SESSION_LAST_SEEN.pop(sid, None)


# ── 行銷首頁 ──────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    line_id = GlobalConfig.LINE_BOT_ID or "@ziwei_ai"
    qr_url = f"https://qr-official.line.me/gs/M_{line_id.lstrip('@')}_BW.png"
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "line_id": line_id, "qr_url": qr_url, "time_table": TIME_TABLE},
    )


# ── 網頁聊天介面 ───────────────────────────────────────────────
@app.get("/chat", response_class=HTMLResponse)
def chat_page(request: Request):
    return templates.TemplateResponse(
        "chat.html",
        {"request": request, "time_table": TIME_TABLE},
    )


# ── 網頁聊天 API ───────────────────────────────────────────────
@app.post("/api/chat", dependencies=[Depends(verify_api_key)])
def api_chat(body: ChatRequest):
    """
    網頁版聊天 API。需在 Header 帶 `X-API-Key`（若伺服器有設定 API_KEY）。
    Request body: { "message": "...", "session_id": "..." }
    Response:     { "reply": "..." }
    """
    # Fix #2：pipeline 尚未就緒時提早返回
    if _pipeline is None:
        return JSONResponse(status_code=503, content={"reply": "⚠️ 服務啟動中，請稍後再試。"})

    try:
        msg = body.message.strip()
        session_id = body.session_id or "web_default"

        if not msg:
            return {"reply": "請輸入訊息。"}

        is_reset = "/reset" in msg

        _evict_stale_sessions()  # Fix #5：每次請求順帶清理過期 sessions
        _touch_session(session_id)  # Fix #5

        if GlobalConfig.USE_AGENT_SKILL:
            if is_reset or session_id not in AGENT_THREAD:
                AGENT_THREAD[session_id] = str(uuid.uuid4())
                if is_reset:
                    return {"reply": "✅ 對話已重置！請重新告訴我你的出生年月日、時辰和性別。"}

            config = {"configurable": {"thread_id": AGENT_THREAD[session_id]}}
            output = _pipeline.invoke({"messages": [HumanMessage(content=msg)]}, config=config)
            reply = _extract_last_message(output)

        else:
            config = {"configurable": {"thread_id": session_id, "session_id": session_id}}
            # Fix #3：移除多餘的 setdefault（下方 if 判斷已涵蓋初始化）
            if session_id not in HOROSCOPE or is_reset:
                HOROSCOPE[session_id] = ""
                SESSION_STATS[session_id] = []
                if is_reset:
                    return {"reply": "✅ 對話已重置！請重新告訴我你的出生年月日、時辰和性別。"}

            SESSION_STATS[session_id].append(HumanMessage(content=msg))
            _trim_history(session_id)  # Fix #9

            output = _pipeline.invoke(
                {"messages": SESSION_STATS[session_id], "horoscope": HOROSCOPE[session_id]},
                config=config,
            )
            if not HOROSCOPE[session_id]:
                HOROSCOPE[session_id] = output.get("horoscope", "")

            reply = _extract_last_message(output)
            SESSION_STATS[session_id].append(AIMessage(content=reply))

        return {"reply": reply}

    except Exception as e:
        tb = traceback.format_exc()
        print(tb)
        if "RESOURCE_EXHAUSTED" in str(e) or "429" in str(e):
            return JSONResponse(status_code=429, content={"reply": "⚠️ AI 服務目前請求過多，請稍後再試。"})
        return JSONResponse(status_code=500, content={"reply": "⚠️ 發生錯誤，請稍後再試。"})


# ── 重置 API ──────────────────────────────────────────────────
@app.post("/api/reset", dependencies=[Depends(verify_api_key)])
def api_reset(body: ResetRequest):
    """清除指定 session 的對話記憶"""
    session_id = body.session_id or "web_default"
    SESSION_STATS.pop(session_id, None)
    HOROSCOPE.pop(session_id, None)
    AGENT_THREAD.pop(session_id, None)
    _SESSION_LAST_SEEN.pop(session_id, None)  # Fix #3：重置時一併清除 TTL 記錄
    return {"status": "ok"}


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

                tk = event["replyToken"]
                msg = event["message"]["text"]
                user_id = event["source"]["userId"]
                is_reset = "/reset" in msg

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
                    config = {"configurable": {"thread_id": user_id, "session_id": user_id}}
                    if user_id not in HOROSCOPE or is_reset:
                        HOROSCOPE[user_id] = ""
                        SESSION_STATS[user_id] = []
                        if is_reset:
                            line_bot_api.reply_message(tk, TextSendMessage("已重置對話！請重新告訴我你的出生資料。"))
                            continue

                    SESSION_STATS[user_id].append(HumanMessage(content=msg))
                    _trim_history(user_id)

                    output = await run_in_threadpool(
                        _pipeline.invoke,
                        {"messages": SESSION_STATS[user_id], "horoscope": HOROSCOPE[user_id]},
                        config,
                    )
                    if not HOROSCOPE[user_id]:
                        HOROSCOPE[user_id] = output.get("horoscope", "")

                    reply = _extract_last_message(output)
                    SESSION_STATS[user_id].append(AIMessage(content=reply))
                    print("cur_state:", SESSION_STATS[user_id])

                print(reply)
                line_bot_api.reply_message(tk, TextSendMessage(reply))

            except Exception:
                print(traceback.format_exc())

    except Exception:
        print(traceback.format_exc())

    return "OK"


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=False)
