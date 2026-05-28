import datetime as _dt  # Bug R4#4 Fix: 模組層級 import，避免每次 get_birth_info 呼叫重複 import
import json
import logging
import re
from .chat_state import ChatState
from langchain_core.messages import AIMessage, HumanMessage
from ..global_config import GlobalConfig

logger = logging.getLogger(__name__)

_FIELD_NAMES = {
    "year": "出生年份",
    "month": "出生月份",
    "day": "出生日期",
    "hour": "出生時辰",
    "is_male": "性別",
}
# is_lunar is derived from context, not asked for explicitly — omitted from _FIELD_NAMES

_VALID_RANGES = {"year": (1800, 2100), "month": (1, 12), "day": (1, 30), "hour": (0, 23)}


def _format_known(birth_info: dict) -> str:
    """將已知的 birth_info 格式化成人類可讀字串。"""
    hour_to_zhi = {0: "子", 2: "丑", 4: "寅", 6: "卯", 8: "辰", 10: "巳",
                   12: "午", 14: "未", 16: "申", 18: "酉", 20: "戌", 22: "亥"}
    parts = []
    if birth_info.get("year") is not None:
        parts.append(f"{birth_info['year']}年")
    if birth_info.get("month") is not None:
        parts.append(f"{birth_info['month']}月")
    if birth_info.get("day") is not None:
        parts.append(f"{birth_info['day']}日")
    if birth_info.get("hour") is not None:
        h = birth_info["hour"]
        zhi = hour_to_zhi.get(h, f"{h}時")
        parts.append(f"{zhi}時（{h}點）")
    if birth_info.get("is_male") is not None:
        parts.append("男" if birth_info["is_male"] else "女")
    return "、".join(parts) if parts else "尚無"


def get_birth_info(state: ChatState, llm):
    if not state.messages:
        return state

    birth_info = state.birth_info

    # Bug #1 Fix: 取最近 5 則對話作為 context（不只看最後一則）
    recent_msgs = state.messages[-5:]
    conversation_context = "\n".join(
        f"{'用戶' if isinstance(m, HumanMessage) else '助手'}: {m.content}"
        for m in recent_msgs
    )

    # Bug #2 Fix: 在 prompt 中告知 LLM 哪些欄位已知，只需補充缺少的
    already_known_str = _format_known(birth_info)
    missing_keys = [k for k, v in birth_info.items() if v is None and k in _FIELD_NAMES]

    prompt = f"""
你是有用的資料整理人員，請從以下對話中抽取出生資訊，回覆 JSON 格式，不包含任何其他字句。

【已知資訊（不需重新抽取，保持原值）】
{already_known_str}

【尚需補充的欄位】
{', '.join(_FIELD_NAMES[k] for k in missing_keys)}

【時辰對照表（地支 → hour 整數）】
子=0, 丑=2, 寅=4, 卯=6, 辰=8, 巳=10, 午=12, 未=14, 申=16, 酉=18, 戌=20, 亥=22

性別：男生 is_male="1"，女生 is_male="0"
曆法：對話中出現「農曆」「陰曆」「舊曆」或「（農曆）」→ is_lunar="1"；明確說「國曆」「陽曆」或「（國曆）」→ is_lunar="0"；未提及 → is_lunar="0"
如果對話中完全未提到某欄位，該欄位請填 null（is_lunar 除外，預設填 "0"）。

【對話記錄】
{conversation_context}

output example（僅回傳 JSON）:
{{"year":"1998","month":"8","day":"27","hour":"16","is_male":"1","is_lunar":"0"}}
"""

    response = llm.invoke(prompt)
    try:
        json_str = response.content
        match = re.search(r'\{.*?\}', json_str, re.DOTALL)
        if match:
            json_str = match.group()
        extracted = json.loads(json_str)
        for key in ["year", "month", "day", "hour", "is_male", "is_lunar"]:
            val = extracted.get(key)
            if val is not None:
                try:
                    if key in ("is_male", "is_lunar") and isinstance(val, str):
                        val_lower = val.strip().lower()
                        true_vals  = ("true", "1", "male", "男", "男生") if key == "is_male" else ("true", "1")
                        false_vals = ("false", "0", "female", "女", "女生") if key == "is_male" else ("false", "0")
                        if val_lower in true_vals:
                            birth_info[key] = 1
                        elif val_lower in false_vals:
                            birth_info[key] = 0
                        else:
                            logger.warning("[get_birth_info] %s 值 %r 無法辨識，略過", key, val)
                        continue
                    new_val = int(val)
                    if key in _VALID_RANGES:
                        lo, hi = _VALID_RANGES[key]
                        if not (lo <= new_val <= hi):
                            logger.warning(
                                "[get_birth_info] 欄位 %s 值 %r 超出合理範圍 [%d, %d]，略過",
                                key, new_val, lo, hi,
                            )
                            continue
                    birth_info[key] = new_val
                    if key == "hour":
                        h = birth_info[key]
                        # Round to the canonical even hour for the 時辰.
                        # Each 時辰 spans odd-to-even (e.g., 3:00-4:59 = 寅時, canonical=4).
                        # (h//2)*2 rounds down, mapping 3→2 (丑時) instead of 4 (寅時).
                        # ((h+1)//2)*2 %24 rounds up: 3→4 (寅時), 23→0 (子時). ✓
                        birth_info[key] = ((h + 1) // 2) * 2 % 24
                except (ValueError, TypeError):
                    logger.warning("[get_birth_info] 欄位 %s 值 %r 無法轉為整數，略過", key, val)
    except Exception:
        logger.warning("[get_birth_info] LLM 回傳無法解析為 JSON，略過本次抽取", exc_info=True)

    # 國曆才做嚴格日期驗證（農曆月可達 30 日，_dt.date 會誤判為無效）
    y, m, d = birth_info.get("year"), birth_info.get("month"), birth_info.get("day")
    is_lunar_flag = bool(birth_info.get("is_lunar"))
    if y is not None and m is not None and d is not None and not is_lunar_flag:
        try:
            _dt.date(y, m, d)
        except ValueError:
            logger.warning("[get_birth_info] 無效日期 %d-%02d-%02d，清除日欄位", y, m, d)
            birth_info["day"] = None

    logger.debug("[get_birth_info] parsing 結果: %s", birth_info)
    missing = [_FIELD_NAMES[k] for k, v in birth_info.items() if v is None and k in _FIELD_NAMES]

    if missing:
        known_str = _format_known(birth_info)
        missing_str = "、".join(missing)

        # Bug #3 Fix: 友善格式，顯示已收到與缺少的欄位
        reply = (
            f"我已收到你的資料：{known_str}\n\n"
            f"還需要你提供：**{missing_str}**\n\n"
            f"請一次說清楚（例如：午時，性別男）"
        )

        # Bug #4 Fix: append 而非替換所有 messages，保留對話流
        state.messages = list(state.messages) + [AIMessage(content=reply)]
        state.birth_info = birth_info
        return state

    state.birth_info = birth_info
    state.is_fortune = True
    return state

def check_horoscope(state: ChatState) -> str:
    if state.horoscope == "" and not GlobalConfig.IS_ONLYCHAT:
        return "get_birth_info"  # 這個是你自定義的 "虛擬" 節點，用來表達直接跳過
    else:
        return "chat"
 
def start(state: ChatState):
    # Bug #1 Fix: reset is_fortune to False each invocation so a completed session's
    # True value stored in MemorySaver doesn't skip birth-info collection for a new
    # question arriving in the same thread after horoscope is cleared.
    state.is_fortune = False
    return state
