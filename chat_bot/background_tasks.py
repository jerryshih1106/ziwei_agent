"""
BackgroundTaskManager — single ThreadPoolExecutor for all fire-and-forget tasks.

Benefits over bare threading.Thread:
  - Bounded worker count (prevents thread explosion under load)
  - Graceful shutdown: lifespan teardown waits for in-flight tasks
  - Centralised error logging for every background job
  - submit_named() lets callers cancel/replace a named task (e.g. per-session
    playbook update — no need to queue duplicate updates for the same session)
"""

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable

logger = logging.getLogger(__name__)

_DEFAULT_WORKERS = 12  # enough for 12 palace analyses + misc tasks in parallel


class BackgroundTaskManager:
    def __init__(self, max_workers: int = _DEFAULT_WORKERS) -> None:
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="bg",
        )
        self._named: dict[str, Future] = {}

    # ── Anonymous submit (fire-and-forget) ──────────────────────────────

    def submit(self, func: Callable, *args, **kwargs) -> Future:
        """Submit a task; errors are logged but not propagated to the caller."""
        future = self._pool.submit(func, *args, **kwargs)
        future.add_done_callback(self._log_error)
        return future

    # ── Named submit (deduplicated per key) ─────────────────────────────

    def submit_named(self, key: str, func: Callable, *args, **kwargs) -> None:
        """Submit a task under *key*; silently replaces a pending task with the same key.

        Useful for playbook updates: if a previous update for session X is still
        queued, this cancels it and submits the fresher version instead.
        Note: cancellation only works if the previous task hasn't started yet.
        """
        old = self._named.get(key)
        if old is not None and not old.done():
            old.cancel()
        future = self._pool.submit(func, *args, **kwargs)
        future.add_done_callback(lambda f: self._on_named_done(key, f))
        self._named[key] = future

    # ── Lifecycle ────────────────────────────────────────────────────────

    def shutdown(self, wait: bool = True) -> None:
        """Call from lifespan teardown to drain in-flight tasks gracefully."""
        self._pool.shutdown(wait=wait, cancel_futures=False)

    # ── Internal ─────────────────────────────────────────────────────────

    @staticmethod
    def _log_error(future: Future) -> None:
        exc = future.exception()
        if exc:
            logger.error("Background task failed: %s", exc, exc_info=exc)

    def _on_named_done(self, key: str, future: Future) -> None:
        self._named.pop(key, None)
        self._log_error(future)


# Module-level singleton used by main.py
bg = BackgroundTaskManager()
