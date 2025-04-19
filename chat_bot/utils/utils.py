import re
from langchain.chat_models import ChatOpenAI
from langchain_community.chat_models import ChatHuggingFace
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.llms.base import BaseLLM
import time
from functools import wraps
from ..global_config import GlobalConfig

def build_llm(model_name: str = "gemini-2", temperature: float = 0.7) -> BaseLLM:
    """
    根據 model_name 回傳對應的 LLM 物件

    支援的 model_name: "gpt-3.5", "gpt-4o", "llama3", "gemini-2"
    """

    model_name = model_name.lower()

    if model_name == "gpt-3.5":
        llm: BaseLLM = ChatOpenAI(model="gpt-3.5-turbo", temperature=temperature)

    elif model_name == "gpt-4o":
        llm: BaseLLM = ChatOpenAI(model="gpt-4o", temperature=temperature)

    elif model_name == "gemini-2":
        llm: BaseLLM = ChatGoogleGenerativeAI(model="gemini-2.0-flash-001", temperature=temperature)

    else:
        raise ValueError(f"Unsupported model_name: {model_name}")

    return llm

def process_llm_output(text):
    """parse llm output

    Args:
        text (str): llm output string with <output></output>

    Returns:
        str: llm output without <output></output>
    """
    if "<output>\n" in text:
        return re.sub(r'^<output>\n(.*)\n</output>$', r'\1', text)
    return text

def sleep_for_tpm(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        time.sleep(GlobalConfig.TPM_TIME)
        return func(*args, **kwargs)
    return wrapper