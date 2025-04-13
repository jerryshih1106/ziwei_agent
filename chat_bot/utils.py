from langchain.chat_models import ChatOpenAI
# from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_community.chat_models import ChatHuggingFace
from langchain.chains import LLMChain
from langchain.llms.base import BaseLLM
import re

def build_llm(model_name: str, temperature: float = 0.7) -> LLMChain:
    """
    根據 model_name 回傳對應的 LLM Chain
    
    支援的 model_name: "gpt3.5", "gpt4o", "llama3", "gemini"
    """

    # 根據 model_name 選擇對應的 llm
    if model_name.lower() == "gpt-3.5":
        llm: BaseLLM = ChatOpenAI(model="gpt-3.5-turbo", temperature=temperature)
    
    elif model_name.lower() == "gpt-4o":
        llm: BaseLLM = ChatOpenAI(model="gpt-4o", temperature=temperature)
    
    # elif model_name.lower() == "gemini":
    #     llm: BaseLLM = ChatGoogleGenerativeAI(model="gemini-pro", temperature=temperature)
    
    elif model_name.lower() == "llama3":
        # 這裡以 HuggingFace 上的 LLaMA 3 為例，需事先設定好 HF token
        llm: BaseLLM = ChatHuggingFace(
            repo_id="meta-llama/Meta-Llama-3-8B-Instruct",
            task="text-generation",
            model_kwargs={"temperature": temperature, "max_new_tokens": 512}
        )
    
    else:
        raise ValueError(f"Unsupported model_name: {model_name}")
    
    return llm
def process_llm_output(text):
    if "<output>\n" in text:
        return re.sub(r'^<output>\n(.*)\n</output>$', r'\1', text)
    return text