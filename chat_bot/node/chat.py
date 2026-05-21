import logging
import time
import tiktoken
from typing import Callable, Generator

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate, PromptTemplate

from .chat_state import ChatState
from ..global_config import GlobalConfig
from ..prompt import CHAT_PROMPT, MEMORY_PROMPT

logger = logging.getLogger(__name__)

# Bug #6 Fix: 用於識別命盤展示訊息（此訊息很長且與 state.horoscope 重複，應從 history 中排除）
_CHART_MESSAGE_MARKER = "我已經排好你的命盤了"

# Bug #7 Fix: 保留最近 N 則訊息原文，更早的才壓縮
_RECENT_KEEP = 8


def get_token_count(text_list, model="gpt-3.5-turbo"):
    # Bug #4 Fix: non-OpenAI model names (e.g. "gemini-2.5-flash-lite") are not in tiktoken.
    # Always fall back to cl100k_base so we get a real token estimate instead of a character count.
    try:
        enc = tiktoken.encoding_for_model(model)
    except KeyError:
        enc = tiktoken.get_encoding("cl100k_base")
    try:
        return sum(len(enc.encode(item)) for item in text_list)
    except Exception:
        # Last-resort: rough estimate (average ~3 chars/token for CJK text)
        return sum(max(1, len(item) // 3) for item in text_list)


def _is_chart_message(msg) -> bool:
    """判斷是否為命盤展示訊息（內容很長且已透過 state.horoscope 傳遞，不需重複放入 history）。"""
    content = getattr(msg, "content", "") or ""
    return _CHART_MESSAGE_MARKER in str(content)


def _format_history_string(messages: list) -> str:
    """Format messages with role labels so LLM can distinguish who said what."""
    role_map = {
        HumanMessage: "用戶",
        AIMessage:    "助手",
        SystemMessage: "系統",
    }
    parts = []
    for msg in messages:
        role = role_map.get(type(msg), "未知")
        parts.append(f"{role}: {msg.content}")
    return "\n".join(parts)


def process_hist_chat(hist_chat: list, llm: Callable):
    # Bug #6 Fix: 過濾掉命盤展示訊息（horoscope 已由 state.horoscope 傳遞，不需重複）
    filtered = [m for m in hist_chat if not _is_chart_message(m)]
    logger.info("process_hist_chat: %d messages (after filter: %d)", len(hist_chat), len(filtered))

    # Bug #7 Fix: 近期訊息保留原文，更早的訊息才壓縮成摘要
    if len(filtered) > _RECENT_KEEP:
        older = filtered[:-_RECENT_KEEP]
        recent = filtered[-_RECENT_KEEP:]

        older_texts = [m.content for m in older]
        cur_token_usage = get_token_count(older_texts, model=GlobalConfig.MODEL)
        logger.info("process_hist_chat: older=%d msgs, tokens=%d", len(older), cur_token_usage)

        if cur_token_usage > GlobalConfig.TOKEN_NUM:
            logger.info("process_hist_chat: compressing older history with LLM summary")
            older_string = _format_history_string(older)
            prompt = PromptTemplate.from_template(MEMORY_PROMPT)
            time.sleep(GlobalConfig.TPM_TIME)  # 僅在真正呼叫 LLM 前才 sleep，避免每次串流都阻塞
            summary = (prompt | llm).invoke({"hist_chat": older_string}).content
            logger.info("process_hist_chat: summary length=%d chars", len(summary))
            combined = [AIMessage(content=f"【早期對話摘要】\n{summary}")] + recent
        else:
            combined = filtered
    else:
        combined = filtered

    hist_chat_string = _format_history_string(combined)

    if GlobalConfig.IS_DEBUG:
        print("-" * 50)
        print(hist_chat_string)
        print("-" * 50)

    return combined, hist_chat_string


def _format_user_profile(user_profile: str) -> str:
    if not user_profile:
        return ""
    return f"\n【顧客個人資料】\n{user_profile}\n"


def _build_prompt(
    hist_chat: list,
    current_message: str,
    horoscope: str,
    chart_table: str = "",
    current_year: int = 0,
    user_profile: str = "",
) -> ChatPromptTemplate:
    """Build a ChatPromptTemplate with history as real message turns.

    IMPORTANT: history messages and the current message are passed as
    LangChain Message objects (not (role, str) tuples) so that LangChain
    does NOT try to format them as templates. User messages can contain
    curly braces (e.g. JSON, math) which would cause a KeyError if treated
    as template variables.
    """
    history_turns = []
    for msg in hist_chat:
        if isinstance(msg, HumanMessage):
            history_turns.append(HumanMessage(content=msg.content))
        elif isinstance(msg, AIMessage):
            history_turns.append(AIMessage(content=msg.content))

    return ChatPromptTemplate.from_messages([
        ("system", CHAT_PROMPT),
        *history_turns,
        HumanMessage(content=current_message),
    ])


def chat(state: ChatState, llm: Callable):
    """Chat node — full response (used by LangGraph pipeline)."""
    import datetime
    if not state.messages:
        return state

    user_msg = state.messages[-1].content
    logger.info("chat: user_msg=%.80r has_horoscope=%s", user_msg, bool(state.horoscope))

    hist_chat, _ = process_hist_chat(state.messages[:-1], llm)
    current_year = datetime.datetime.now().year
    user_profile_section = _format_user_profile(state.user_profile)
    prompt = _build_prompt(hist_chat, user_msg, state.horoscope, state.chart_table, current_year, state.user_profile)
    answer = (prompt | llm).invoke({
        "horoscope": state.horoscope,
        "chart_table": state.chart_table,
        "current_year": current_year,
        "user_profile": user_profile_section,
    })
    logger.info("chat: response length=%d chars", len(answer.content))
    hist_chat.append(state.messages[-1])
    hist_chat.append(answer)
    state.messages = hist_chat

    if GlobalConfig.IS_DEBUG:
        print("@" * 50)
        print(state.messages)
        print("@" * 50)

    return state


def chat_stream(messages: list, horoscope: str, llm, chart_table: str = "", current_year: int = 0, user_profile: str = "") -> Generator[str, None, None]:
    """
    Streaming version of chat.
    Yields text tokens one by one using LangChain's chain.stream().
    Used by the /api/chat/stream SSE endpoint.
    """
    if not messages:
        yield "⚠️ 沒有收到訊息，請重新輸入。"
        return

    user_msg = messages[-1].content
    logger.info("chat_stream: user_msg=%.80r has_horoscope=%s", user_msg, bool(horoscope))

    hist_chat, _ = process_hist_chat(messages[:-1], llm)
    user_profile_section = _format_user_profile(user_profile)
    prompt = _build_prompt(hist_chat, user_msg, horoscope, chart_table, current_year, user_profile)
    total_chars = 0
    for chunk in (prompt | llm).stream({"horoscope": horoscope, "chart_table": chart_table, "current_year": current_year, "user_profile": user_profile_section}):
        if hasattr(chunk, "content") and chunk.content:
            total_chars += len(chunk.content)
            yield chunk.content
    logger.info("chat_stream: done, total response chars=%d", total_chars)
