"""
LLMClient — thin wrapper around LangChain LLM that adds:
  - uniform retry / exponential-backoff for transient errors
  - a single canonical list of "service busy" error signatures
  - astream_with_retry() used by all SSE streaming endpoints

Usage:
    from chat_bot.llm_client import astream_with_retry, is_transient_error

    async def _stream():
        async for token, error in astream_with_retry(chain, inputs, max_retries=3):
            if error:
                yield f'data: {json.dumps({"token": error})}\n\n'
                return
            yield f'data: {json.dumps({"token": token})}\n\n'
"""

import asyncio
import logging
from typing import AsyncGenerator

logger = logging.getLogger(__name__)

# Signatures that indicate a temporary service overload, not a permanent error.
_TRANSIENT_KEYWORDS = (
    "503",
    "429",
    "high demand",
    "overloaded",
    "UNAVAILABLE",
    "Resource has been exhausted",
    "RESOURCE_EXHAUSTED",
    "quota",
    "rate limit",
)


def is_transient_error(exc: BaseException) -> bool:
    """Return True when the exception looks like a temporary API overload."""
    msg = str(exc)
    return any(k in msg for k in _TRANSIENT_KEYWORDS)


_CHUNK_TIMEOUT_SECS = float(60)  # max seconds to wait for the next token chunk


async def astream_with_retry(
    chain,
    inputs: dict,
    max_retries: int = 3,
    base_wait: float = 5.0,
) -> AsyncGenerator[tuple[str, str | None], None]:
    """
    Stream tokens from *chain.astream(inputs)* with automatic retry on transient errors.

    Yields (token, None) for each successful token chunk.
    Yields ("", error_message) exactly once when all retries are exhausted,
    then stops — the caller should treat this as a terminal error.

    Between retries the generator yields ("", retry_notice) so the client can
    display a progress message, then yields ("", "__clear__") to signal the
    client to clear that notice before the real content arrives.
    """
    for attempt in range(max_retries):
        tokens: list[str] = []
        try:
            aiter = chain.astream(inputs).__aiter__()
            while True:
                try:
                    chunk = await asyncio.wait_for(aiter.__anext__(), timeout=_CHUNK_TIMEOUT_SECS)
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    raise TimeoutError(f"LLM 回應逾時（超過 {int(_CHUNK_TIMEOUT_SECS)} 秒未收到回應）")
                token = chunk.content if hasattr(chunk, "content") else str(chunk)
                if token:
                    tokens.append(token)
                    yield token, None
            return  # success
        except Exception as exc:
            if attempt < max_retries - 1 and is_transient_error(exc):
                wait = base_wait * (2 ** attempt)
                logger.warning(
                    "astream_with_retry: transient error (attempt %d/%d), retrying in %.0fs: %s",
                    attempt + 1, max_retries, wait, exc,
                )
                yield "", f"⚠️ AI 服務繁忙，{int(wait)} 秒後自動重試（第 {attempt + 2}/{max_retries} 次）…"
                await asyncio.sleep(wait)
                yield "", "__clear__"
            else:
                logger.exception("astream_with_retry: non-recoverable error after %d attempt(s)", attempt + 1)
                yield "", "⚠️ 生成失敗，請稍後再試。"
                return
