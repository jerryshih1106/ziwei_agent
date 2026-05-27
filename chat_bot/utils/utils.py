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

# ── Langfuse v4 singleton ─────────────────────────────────────
_langfuse_client = None
_LANGFUSE_FAILED = object()


def _get_langfuse_client():
    """Return the Langfuse v4 client (lazy init). Returns None if not configured.

    auth_check() is best-effort only — a 401 logs a warning but does NOT
    disable tracing so that network hiccups or wrong-region errors don't
    silence all observability.
    """
    global _langfuse_client
    if _langfuse_client is _LANGFUSE_FAILED:
        return None
    if _langfuse_client is not None:
        return _langfuse_client
    if not os.environ.get("LANGFUSE_PUBLIC_KEY"):
        return None
    try:
        from langfuse import Langfuse
        _langfuse_client = Langfuse()
        logger.info("Langfuse v4 client initialised")
    except Exception as e:
        logger.warning("Langfuse init failed: %s", e)
        _langfuse_client = _LANGFUSE_FAILED
        return None
    # auth_check is optional validation — failure is a warning, not fatal
    try:
        _langfuse_client.auth_check()
        logger.info("Langfuse auth_check OK — tracing active")
    except Exception as e:
        logger.warning("Langfuse auth_check failed (check keys/host): %s", e)
    return _langfuse_client


def flush_langfuse():
    """Flush pending Langfuse traces. Call during app shutdown."""
    if _langfuse_client and _langfuse_client is not _LANGFUSE_FAILED:
        try:
            _langfuse_client.flush()
            logger.info("Langfuse: traces flushed")
        except Exception as e:
            logger.warning("Langfuse flush error: %s", e)


# ── @observe helper ───────────────────────────────────────────
try:
    from langfuse import observe as _lf_observe

    def langfuse_observe(**kwargs):
        """Decorator factory: wraps a function with Langfuse @observe.
        Falls back to no-op if Langfuse is not installed or not configured.
        """
        def decorator(func):
            if not os.environ.get("LANGFUSE_PUBLIC_KEY"):
                return func
            return _lf_observe(**kwargs)(func)
        return decorator

except ImportError:
    def langfuse_observe(**kwargs):  # type: ignore[misc]
        def decorator(func):
            return func
        return decorator


def build_llm(model_name: str = "gemini-2.5-flash-lite", temperature: float = 0.5) -> BaseLLM:
    # Ensure Langfuse client is initialised so @observe decorators have a tracer to report to.
    _get_langfuse_client()

    kwargs: dict = {"temperature": temperature}
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
