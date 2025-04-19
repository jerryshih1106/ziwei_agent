import json
import re
from .chat_state import ChatState
from ..global_config import GlobalConfig

# def detect_intent(state: ChatState, llm: Callable):
#     user_input = state.messages[-1].content

#     prompt = f"""
#         請判斷這句話是否完整包含出生年, 月, 日, 時辰。請只回覆 "yes" 或 "no"。
#         句子: {user_input}
#         """

#     response = llm.invoke(prompt).strip().lower()
#     is_fortune = "yes" in response
#     print("is_fortune_is_fortune_is_fortune: ", is_fortune)
#     return state.copy(update={"is_fortune": is_fortune})

def get_birth_info(state: ChatState, llm):
    print("[get_birth_info] ", state.messages, " type: " ,type(state.messages[-1]))
    user_input = state.messages[-1].content
    birth_info = state.birth_info

    # 將目前已知的資料加入提示中
    prompt = f"""
        你是有用的資料整理人員, 請從以下使用者輸入中，抽取出生的「年份、月份、日期、時辰」，回覆 JSON 格式, 不包含任何其他字句
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
        
        使用者輸入：{user_input}

        output example:
        1. {{"year":"1999","month":"2","day":"23","hour":"16"}}
        2. {{"year":"1998","month":"8","day":"27","hour":"16"}}
        3. {{"year":"2000","month":"3","day":"22","hour":"16"}}
    """

    response = llm.invoke(prompt)
    # response = response.replace(" ", "").replace("\n", "")
    # print("[get_birth_info] 解析 input 結果", response)
    try:
        json_str = response.content
        match = re.search(r'\{.*?\}', json_str)
        if match:
            json_str = match.group()
        extracted = json.loads(json_str)
        # print("[get_birth_info] extracted", extracted)
        for key in ["year", "month", "day", "hour"]:
            if extracted.get(key):
                birth_info[key] = int(extracted[key])
    except Exception:
        pass  # 若 LLM 回傳不是 JSON，就略過這次抽取
    
    print("[get_birth_info] parsing 結果", birth_info)
    # 檢查還有哪些欄位缺失
    missing = [key for key, val in birth_info.items() if not val]
    if missing:
        next_missing = missing[0]
        prompt = f"'{response.content}', 請再次提供你的出生{next_missing}（例如：1995、7、15、辰時）"
        return {
            "messages": state.messages + [{"role": "assistant", "content": prompt}],
            "birth_info": birth_info
        }
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
