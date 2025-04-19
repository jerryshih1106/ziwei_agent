from langchain.prompts import ChatPromptTemplate
from typing import Callable
from .chat_state import ChatState
from ..prompt import CHAT_PROMPT


def chat(state: ChatState, llm: Callable):
    """Chat node，使用 LLM 產生回應"""
    current_prompt = ChatPromptTemplate.from_messages([
    ("system", CHAT_PROMPT),
    ("user", state.messages[-1].content)
    ])

    chain = current_prompt | llm
    # print("----------------------------------------------------------------------")
    # print("[CHAT] current state horoscope: ", state.horoscope)
    # print("----------------------------------------------------------------------")
    answer = chain.invoke({"horoscope": state.horoscope})
    return {"messages": [answer]}
