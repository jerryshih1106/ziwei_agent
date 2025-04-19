from langchain.prompts import ChatPromptTemplate
from typing import Callable
from .chat_state import ChatState
from ..prompt import CHAT_PROMPT

def get_ziwei_information(state: ChatState, llm: Callable):
    current_prompt = ChatPromptTemplate.from_messages([
    ("system", CHAT_PROMPT),
    ("user", state["messages"])
    ])

    chain = current_prompt | llm
    answer = chain.invoke({"horoscope": state["horoscope"]})
    return {"messages": [answer]}
