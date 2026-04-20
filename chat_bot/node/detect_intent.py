import json
import logging
import re
from .chat_state import ChatState
from langchain_core.messages import AIMessage
from ..global_config import GlobalConfig

logger = logging.getLogger(__name__)


def get_birth_info(state: ChatState, llm):
    logger.debug("[get_birth_info] messages=%s type=%s", state.messages, type(state.messages[-1]))
    user_input = state.messages[-1].content
    birth_info = state.birth_info

    # 將目前已知的資料加入提示中
    prompt = f"""
        你是有用的資料整理人員, 請從以下使用者輸入中，抽取出生的「年份、月份、日期、時辰、性別」，回覆 JSON 格式, 不包含任何其他字句
        如果有任何欄位缺失，請用 null 表示。
        
        此外, hour 使用者可能是輸入地支: 子丑寅卯.., 
        hour 的對照表如下:
            子:  hour 設為 '0'
            丑:  hour 設為 '2'
            寅:  hour 設為 '4'
            卯:  hour 設為 '6'
            辰:  hour 設為 '8'
            巳:  hour 設為 '10'
            午:  hour 設為 '12'
            未:  hour 設為 '14'
            申:  hour 設為 '16'
            酉:  hour 設為 '18'
            戌:  hour 設為 '20'
            亥:  hour 設為 '22'
        
        性別如果是男生, is_male 為 "1", 是女生 is_male 則為 "0"
        
        使用者輸入：{user_input}

        input example:
        1. 2000年 3月 22日 12時出生 性別女生
        2. 1998.8.27.16 男
        3. 西元1999年 02月 23號 子, 男生

        output example:
        1. {{"year":"2000","month":"3","day":"22","hour":"12","is_male": "0"}}
        2. {{"year":"1998","month":"8","day":"27","hour":"16","is_male": "1"}}
        3. {{"year":"1999","month":"2","day":"23","hour":"0","is_male": "1"}}
    """

    response = llm.invoke(prompt)
    try:
        json_str = response.content
        match = re.search(r'\{.*?\}', json_str)
        if match:
            json_str = match.group()
        extracted = json.loads(json_str)
        for key in ["year", "month", "day", "hour", "is_male"]:
            if extracted.get(key) is not None:
                birth_info[key] = int(extracted[key])
    except Exception:
        logger.warning("[get_birth_info] LLM 回傳無法解析為 JSON，略過本次抽取", exc_info=True)

    logger.debug("[get_birth_info] parsing 結果: %s", birth_info)
    missing_dic = {"year": "出生年", "month": "出生月", "day": "出生日", "hour": "時辰", "is_male": "性別"}
    missing = [missing_dic[key] for key, val in birth_info.items() if val is None]

    if missing:
        prompt = f"缺少的 information: {missing}\n\n 請一次性提供你的西元完整出生年月日以及時辰以及生理性別, 請盡量講明白一點（例如：1995年7月15日出生在16點, 性別男）"
        state.messages = [AIMessage(role="system", content=prompt)]
        state.birth_info = birth_info
        return state
    state.birth_info = birth_info
    state.is_fortune = True
    # 資料完整，可以進入命盤生成
    return state

def check_horoscope(state: ChatState) -> str:
    if state.horoscope == "" and not GlobalConfig.IS_ONLYCHAT:
        return "get_birth_info"  # 這個是你自定義的 "虛擬" 節點，用來表達直接跳過
    else:
        return "chat"
 
def start(state: ChatState):
    return state
