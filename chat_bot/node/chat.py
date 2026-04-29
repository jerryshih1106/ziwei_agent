from langchain_core.prompts import ChatPromptTemplate, PromptTemplate
from typing import Callable
from langchain_core.messages import AIMessage
import tiktoken
from .chat_state import ChatState
from ..utils.utils import sleep_for_tpm
from ..global_config import GlobalConfig
from ..prompt import CHAT_PROMPT, MEMORY_PROMPT

def get_token_count(text_list, model="gpt-3.5-turbo"):
    try:
        enc = tiktoken.encoding_for_model(model)
    except KeyError:
        # Gemini / unknown models — use cl100k_base as a reasonable approximation
        enc = tiktoken.get_encoding("cl100k_base")
    try:
        res = sum(len(enc.encode(item)) for item in text_list)
    except Exception:
        res = sum(len(item) for item in text_list)
    return res

def process_hist_chat(hist_chat:list, llm: Callable):
    # Fix #3：移除 @sleep_for_tpm，只在實際呼叫 LLM 時才延遲
    review_list = [text.content for text in hist_chat]
    hist_chat_string = "".join(review_list)
    if get_token_count(review_list, model=GlobalConfig.MODEL) > GlobalConfig.MAX_TOKENS:
        import time; time.sleep(GlobalConfig.TPM_TIME)  # 只在壓縮時才等待
        current_prompt = PromptTemplate.from_template(MEMORY_PROMPT)
        chain = current_prompt | llm
        hist_chat_string = chain.invoke({"hist_chat": ''.join(review_list)}).content
        hist_chat = [AIMessage(content=hist_chat_string)]
    if GlobalConfig.IS_DEBUG:
        print("-" * 50)
        print(hist_chat_string)
        print("-" * 50)
    return hist_chat, hist_chat_string

@sleep_for_tpm
def chat(state: ChatState, llm: Callable):
    """Chat node，使用 LLM 產生回應"""
    # Fix #6：messages 為空時直接返回，避免 IndexError
    if not state.messages:
        return state
    hist_chat, hist_chat_string = process_hist_chat(state.messages[:-1], llm)
    current_prompt = ChatPromptTemplate.from_messages([
    ("system", CHAT_PROMPT),
    ("user", state.messages[-1].content)
    ])

    chain = current_prompt | llm
    answer = chain.invoke({"memory": hist_chat_string, "horoscope": state.horoscope})
    hist_chat.append(state.messages[-1])
    hist_chat.append(answer)
    state.messages = hist_chat
    if GlobalConfig.IS_DEBUG:
        print("@" * 50)
        print(state.messages)
        print("@" * 50)
    return state
