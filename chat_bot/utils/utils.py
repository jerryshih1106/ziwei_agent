import logging
import os
import re
import time
from functools import wraps
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.language_models import BaseChatModel as BaseLLM
from ..global_config import GlobalConfig

logger = logging.getLogger(__name__)

_langfuse_handler = None

def _get_langfuse_handler():
    global _langfuse_handler
    if _langfuse_handler is not None:
        return _langfuse_handler
    if not os.environ.get("LANGFUSE_PUBLIC_KEY"):
        return None
    try:
        from langfuse.langchain import CallbackHandler
        _langfuse_handler = CallbackHandler()
        logger.info("Langfuse tracing enabled")
        return _langfuse_handler
    except ImportError:
        try:
            from langfuse.callback import CallbackHandler
            _langfuse_handler = CallbackHandler()
            logger.info("Langfuse tracing enabled")
            return _langfuse_handler
        except Exception as e:
            logger.warning("Langfuse init failed: %s", e)
            return None
    except Exception as e:
        logger.warning("Langfuse init failed: %s", e)
        return None


def build_llm(model_name: str = "gemini-2.5-flash-lite", temperature: float = 0.7) -> BaseLLM:
    callbacks = []
    handler = _get_langfuse_handler()
    if handler:
        callbacks.append(handler)

    kwargs: dict = {"temperature": temperature}
    if callbacks:
        kwargs["callbacks"] = callbacks

    model_name = model_name.lower()

    if model_name == "gpt-3.5":
        llm: BaseLLM = ChatOpenAI(model="gpt-3.5-turbo", **kwargs)
    elif model_name == "gpt-4o":
        llm: BaseLLM = ChatOpenAI(model="gpt-4o", **kwargs)
    else:
        try:
            llm: BaseLLM = ChatGoogleGenerativeAI(model=model_name, **kwargs)
        except Exception as e:
            raise ValueError(f"Unsupported model_name: {model_name}, {e}")

    return llm

def process_llm_output(text):
    """parse llm output

    Args:
        text (str): llm output string with <output></output>

    Returns:
        str: llm output without <output></output>
    """
    if "<output>" in text:
        match = re.search(r'<output>\n?(.*?)\n?</output>', text, re.DOTALL)
        if match:
            return match.group(1).strip()
    return text

def sleep_for_tpm(func):
    """Rate-limit decorator: sleeps GlobalConfig.TPM_TIME seconds before each call.

    TPM_TIME defaults to 0 (no sleep). Set the TPM_TIME env-var only when you are
    hitting Gemini / OpenAI TPM limits (e.g. TPM_TIME=2 for free-tier quotas).
    Sleeping unconditionally on every LLM call adds 3 s × 12 palaces = 36 s of
    pure waiting to every chart generation even when no rate-limit occurs.
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        delay = GlobalConfig.TPM_TIME
        if delay > 0:
            time.sleep(delay)
        return func(*args, **kwargs)
    return wrapper