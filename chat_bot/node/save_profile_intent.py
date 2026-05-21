import json
import logging
import re

logger = logging.getLogger(__name__)

_ZHI_TO_HOUR = {
    "子": 0, "丑": 2, "寅": 4, "卯": 6, "辰": 8, "巳": 10,
    "午": 12, "未": 14, "申": 16, "酉": 18, "戌": 20, "亥": 22,
}

_DETECT_PROMPT = """你是意圖識別助手，判斷使用者是否想把「另一個人」的出生資料存入命盤庫。

觸發條件：使用者提供了某人的關係稱呼（女友、媽媽、老公、朋友等）以及出生資訊（年月日時）。

如果符合，回傳 JSON：
{{"want_to_save": true, "label": "稱呼名稱", "year": 年整數或null, "month": 月整數或null, "day": 日整數或null, "hour_zhi": "時辰地支或null", "is_male": true/false或null}}

從稱呼推斷性別（若未明說）：
- 女性：女友、媽媽、姐姐、妹妹、老婆、太太、女兒、阿姨、奶奶
- 男性：男友、爸爸、哥哥、弟弟、老公、先生、兒子、叔叔、爺爺

時辰地支：子丑寅卯辰巳午未申酉戌亥（對應 0 2 4 6 8 10 12 14 16 18 20 22 點）

若不是儲存意圖，只回傳 {{"want_to_save": false}}

使用者訊息：{message}
"""


def detect_save_intent(msg: str, llm) -> dict | None:
    """
    Returns parsed data dict if user wants to save a profile, else None.
    Fast: single LLM call, no chart generation.
    """
    prompt = _DETECT_PROMPT.format(message=msg)
    try:
        response = llm.invoke(prompt)
        match = re.search(r'\{.*?\}', response.content, re.DOTALL)
        if not match:
            return None
        data = json.loads(match.group())
    except Exception:
        logger.warning("[save_profile_intent] detect LLM parse failed", exc_info=True)
        return None

    if not data.get("want_to_save"):
        return None

    logger.info("[save_profile_intent] detected: %s", data)
    return data


def execute_save_profile(data: dict, session_id: str) -> str:
    """
    Generate chart and save to profile library.
    Slow: runs 12 parallel LLM calls for palace analysis.
    Returns a user-facing confirmation message.
    """
    from ziweidoushu.kernel import ZiweiChart
    from ziweidoushu.base import ZiWeiConfig
    from chat_bot.auth.auth import save_profile
    from chat_bot.node.gen_ziwei import get_palace_information, _get_domain_df

    label = (data.get("label") or "命盤").strip()
    year = data.get("year")
    month = data.get("month")
    day = data.get("day")
    hour_zhi = data.get("hour_zhi")
    is_male = data.get("is_male")

    missing = []
    if not year:     missing.append("出生年")
    if not month:    missing.append("出生月")
    if not day:      missing.append("出生日")
    if not hour_zhi: missing.append("出生時辰")
    if is_male is None: missing.append("性別")

    if missing:
        return f"想幫 **{label}** 存入命盤庫，但還缺少：{'、'.join(missing)}，請補充後再說一次。"

    hour = _ZHI_TO_HOUR.get(hour_zhi, 0)

    try:
        config = ZiWeiConfig(int(year), int(month), int(day), int(hour), bool(is_male))
        chart = ZiweiChart(config)
        df = chart.gen_chart()
        horoscope = get_palace_information(df, _get_domain_df())
        chart_table = df.to_markdown()
        birth_info = {
            "year": int(year), "month": int(month), "day": int(day),
            "hour": int(hour), "is_male": 1 if is_male else 0,
        }
    except Exception:
        logger.exception("[save_profile_intent] chart generation failed for label=%s", label)
        return "⚠️ 排盤時發生錯誤，請確認出生日期是否正確。"

    try:
        profile_id = save_profile(session_id, label, horoscope, chart_table, birth_info)
        if profile_id:
            gender = "男" if is_male else "女"
            return (
                f"✅ 已將 **{label}** 的命盤存入命盤庫！\n\n"
                f"生辰：{year}年{month}月{day}日{hour_zhi}時，{gender}生\n\n"
                f"可點右上角「👥 命盤庫」查看或載入。"
            )
        return "⚠️ 儲存命盤時發生錯誤，請稍後再試。"
    except Exception:
        logger.exception("[save_profile_intent] save_profile failed")
        return "⚠️ 儲存命盤時發生錯誤，請稍後再試。"
