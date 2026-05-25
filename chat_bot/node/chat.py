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

# 只有問到星曜位置或宮位結構時才帶入 chart_table，其他問題靠 horoscope 即可
_CHART_TABLE_KEYWORDS = frozenset([
    "哪個宮", "在哪", "哪顆星", "星在", "星曜", "表格", "命盤結構", "看命盤", "命盤表",
    "命宮", "夫妻宮", "財帛宮", "官祿宮", "田宅宮", "父母宮", "兄弟宮",
    "疾厄宮", "遷移宮", "交友宮", "子女宮", "福德宮",
    "紫微星", "天機星", "太陽星", "武曲星", "天同星", "廉貞星",
    "天府星", "太陰星", "貪狼星", "巨門星", "天相星", "天梁星", "七殺星", "破軍星",
])


def _needs_chart_table(user_msg: str) -> bool:
    return any(kw in user_msg for kw in _CHART_TABLE_KEYWORDS)


def _format_chart_table_section(user_msg: str, chart_table: str) -> str:
    """只有問題涉及星曜位置/宮位結構時才回傳 chart_table 段落，否則回空字串。"""
    if chart_table and _needs_chart_table(user_msg):
        return f"顧客的命盤表格：\n{chart_table}\n"
    return ""

# Bug #7 Fix: 保留最近 N 則訊息原文，更早的才壓縮
_RECENT_KEEP = 6


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
    bazi_doc: str = "",
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


def _resolve_chart_table_section(user_msg: str, chart_table: str) -> str:
    """Alias used by both chat() and chat_stream() to keep call sites clean."""
    return _format_chart_table_section(user_msg, chart_table)


# ── Horoscope compression ─────────────────────────────────────────────────────

_COMPRESS_CHARS = 200   # max chars kept per palace block in summary mode

# 主題 → 相關宮位關鍵字（用於 horoscope 段落比對）
_TOPIC_PALACES: list[tuple[frozenset, frozenset]] = [
    # (觸發關鍵字, 相關宮位名稱)
    (frozenset(["感情", "愛情", "婚姻", "另一半", "伴侶", "桃花", "對象", "交往", "戀愛", "男友", "女友", "老公", "老婆"]),
     frozenset(["夫妻宮", "福德宮", "子女宮"])),
    (frozenset(["事業", "工作", "職場", "升遷", "老闆", "同事", "職業", "創業", "跳槽"]),
     frozenset(["官祿宮", "財帛宮", "交友宮"])),
    (frozenset(["財運", "財富", "收入", "薪水", "投資", "錢", "賺錢", "存錢", "財務"]),
     frozenset(["財帛宮", "田宅宮", "官祿宮"])),
    (frozenset(["健康", "身體", "疾病", "生病", "體質", "養生"]),
     frozenset(["疾厄宮", "命宮"])),
    (frozenset(["家庭", "家人", "父母", "兄弟姐妹", "子女", "小孩", "長輩"]),
     frozenset(["父母宮", "兄弟宮", "子女宮", "田宅宮"])),
    (frozenset(["出行", "旅遊", "移民", "搬家", "出國", "遷移"]),
     frozenset(["遷移宮", "田宅宮"])),
]

# 強制回傳完整 horoscope 的關鍵字（用戶明確要求詳細）
_FULL_HOROSCOPE_KEYWORDS = frozenset([
    "詳細", "仔細", "完整", "深入", "全部宮位", "所有宮",
    "命宮", "夫妻宮", "財帛宮", "官祿宮", "田宅宮", "父母宮",
    "兄弟宮", "疾厄宮", "遷移宮", "交友宮", "子女宮", "福德宮",
])


def _compress_horoscope(horoscope: str) -> str:
    """Truncate each palace block to _COMPRESS_CHARS, ending at a sentence boundary."""
    if not horoscope:
        return horoscope
    blocks = horoscope.split("\n\n")
    out = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        if len(block) <= _COMPRESS_CHARS:
            out.append(block)
        else:
            cut = block[:_COMPRESS_CHARS]
            for punct in "。！？.!?":
                last = cut.rfind(punct)
                if last >= _COMPRESS_CHARS // 2:
                    cut = cut[: last + 1]
                    break
            else:
                cut = cut.rstrip() + "…"
            out.append(cut)
    return "\n\n".join(out)


def _filter_horoscope_by_topic(user_msg: str, horoscope: str) -> str:
    """Return only palace blocks relevant to the topic of user_msg.

    Each block is kept when its text mentions any of the matched palace names.
    Falls back to compressed full horoscope when no topic is matched.
    """
    matched_palaces: set[str] = set()
    for triggers, palaces in _TOPIC_PALACES:
        if any(kw in user_msg for kw in triggers):
            matched_palaces |= palaces
    if not matched_palaces:
        return _compress_horoscope(horoscope)

    blocks = horoscope.split("\n\n")
    relevant = [b for b in blocks if b.strip() and any(p in b for p in matched_palaces)]
    # Always prepend the 命宮 block (identity/core) for context
    ming_blocks = [b for b in blocks if "命宮" in b and b not in relevant]
    chosen = ming_blocks[:1] + relevant
    if not chosen:
        return _compress_horoscope(horoscope)
    return "\n\n".join(b.strip() for b in chosen)


def _horoscope_for_chat(user_msg: str, horoscope: str) -> str:
    """Select the right horoscope slice for this chat turn.

    Priority:
    1. User asks for detail / specific palace → full horoscope
    2. Topic detected (感情/事業/財運/…) → relevant palace blocks only
    3. Default → compressed summary (200 chars/palace)
    """
    if not horoscope:
        return horoscope
    if any(kw in user_msg for kw in _FULL_HOROSCOPE_KEYWORDS):
        return horoscope
    return _filter_horoscope_by_topic(user_msg, horoscope)


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
    bazi_section = f"\n顧客的八字命盤：\n{state.bazi_doc}\n" if getattr(state, "bazi_doc", "") else ""
    chart_table_section = _resolve_chart_table_section(user_msg, state.chart_table)
    horoscope_ctx = _horoscope_for_chat(user_msg, state.horoscope)
    prompt = _build_prompt(hist_chat, user_msg, horoscope_ctx, state.chart_table, current_year, state.user_profile)
    answer = (prompt | llm).invoke({
        "horoscope": horoscope_ctx,
        "chart_table_section": chart_table_section,
        "current_year": current_year,
        "user_profile": user_profile_section,
        "bazi_doc": bazi_section,
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


def chat_stream(messages: list, horoscope: str, llm, chart_table: str = "", current_year: int = 0, user_profile: str = "", bazi_doc: str = "") -> Generator[str, None, None]:
    """
    Streaming version of chat.
    Yields text tokens one by one using LangChain's chain.stream().
    Used by the /api/chat/stream SSE endpoint.
    """
    if not messages:
        yield "⚠️ 沒有收到訊息，請重新輸入。"
        return

    user_msg = messages[-1].content
    logger.info("chat_stream: user_msg=%.80r has_horoscope=%s has_bazi=%s", user_msg, bool(horoscope), bool(bazi_doc))

    hist_chat, _ = process_hist_chat(messages[:-1], llm)
    user_profile_section = _format_user_profile(user_profile)
    bazi_section = f"\n顧客的八字命盤：\n{bazi_doc}\n" if bazi_doc else ""
    chart_table_section = _resolve_chart_table_section(user_msg, chart_table)
    horoscope_ctx = _horoscope_for_chat(user_msg, horoscope)
    prompt = _build_prompt(hist_chat, user_msg, horoscope_ctx, chart_table, current_year, user_profile)
    total_chars = 0
    for chunk in (prompt | llm).stream({"horoscope": horoscope_ctx, "chart_table_section": chart_table_section, "current_year": current_year, "user_profile": user_profile_section, "bazi_doc": bazi_section}):
        if hasattr(chunk, "content") and chunk.content:
            total_chars += len(chunk.content)
            yield chunk.content
    logger.info("chat_stream: done, total response chars=%d", total_chars)
