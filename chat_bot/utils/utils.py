import re
from langchain.chat_models import ChatOpenAI
from langchain_community.chat_models import ChatHuggingFace
from langchain.llms.base import BaseLLM

def build_llm(model_name: str, temperature: float = 0.7) -> BaseLLM:
    """
    根據 model_name 回傳對應的 LLM 物件

    支援的 model_name: "gpt-3.5", "gpt-4o", "llama3", "qwen"
    """

    model_name = model_name.lower()

    if model_name == "gpt-3.5":
        llm: BaseLLM = ChatOpenAI(model="gpt-3.5-turbo", temperature=temperature)

    elif model_name == "gpt-4o":
        llm: BaseLLM = ChatOpenAI(model="gpt-4o", temperature=temperature)

    # elif model_name == "gemini":
    #     llm: BaseLLM = ChatGoogleGenerativeAI(model="gemini-pro", temperature=temperature)

    elif model_name == "llama3":
        llm: BaseLLM = ChatHuggingFace(
            repo_id="meta-llama/Meta-Llama-3-8B-Instruct",
            task="text-generation",
            model_kwargs={"temperature": temperature, "max_new_tokens": 4096}
        )

    elif model_name == "qwen":
        llm: BaseLLM = ChatHuggingFace(
            repo_id="Qwen/Qwen1.5-7B-Chat",
            task="text-generation",
            model_kwargs={"temperature": temperature, "max_new_tokens": 4096}
        )

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
