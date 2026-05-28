"""
SessionStore — centralised in-memory session state.

All session-scoped dicts previously scattered as globals in main.py live here.
Public dict attributes (messages, horoscopes, …) are intentionally exposed so
existing code in main.py can keep using dict syntax via module-level aliases;
no existing call-sites need to change.

Additional *atomic* helper methods ensure the three horoscope-related fields
(horoscope text, chart_table, birth_info) are always read and written together,
preventing the partial-update race where one field is set but another is not.
"""

import asyncio
import logging
import time
import uuid

logger = logging.getLogger(__name__)


class SessionStore:
    def __init__(self) -> None:
        # ── per-session state ────────────────────────────────────────────
        self.messages: dict[str, list] = {}        # was SESSION_STATS
        self.horoscopes: dict[str, str] = {}       # was HOROSCOPE
        self.chart_tables: dict[str, str] = {}     # was CHART_TABLE
        self.birth_infos: dict[str, dict] = {}     # was BIRTH_INFO
        self.bazi_docs: dict[str, str] = {}        # was BAZI_DOC
        self.agent_threads: dict[str, str] = {}    # was AGENT_THREAD
        self.thread_ids: dict[str, str] = {}       # was NON_AGENT_THREAD
        self.user_profiles: dict[str, str] = {}    # was USER_PROFILES

        # ── TTL tracking ─────────────────────────────────────────────────
        self._last_seen: dict[str, float] = {}     # was _SESSION_LAST_SEEN

        # ── asyncio lock registry ────────────────────────────────────────
        self._locks: dict[str, asyncio.Lock] = {}  # was _SESSION_LOCKS
        self._meta_lock: asyncio.Lock | None = None

    # ── Lifecycle ────────────────────────────────────────────────────────

    def init_event_loop(self) -> None:
        """Call once inside the running event loop (lifespan startup)."""
        self._meta_lock = asyncio.Lock()

    def touch(self, session_id: str) -> None:
        self._last_seen[session_id] = time.time()

    def evict_stale(self, ttl_secs: int = 7200) -> None:
        now = time.time()
        stale = [
            sid for sid, ts in list(self._last_seen.items())
            # web_default is a shared anonymous session — never fully evict it,
            # but trim its message history to avoid unbounded growth
            if sid != "web_default" and now - ts > ttl_secs
        ]
        for sid in stale:
            self.clear_session(sid)
            logger.debug("SessionStore: evicted stale session %s", sid)
        # Cap web_default message history to the last 10 messages
        if "web_default" in self.messages and len(self.messages["web_default"]) > 10:
            self.messages["web_default"] = self.messages["web_default"][-10:]

    # ── Asyncio lock ─────────────────────────────────────────────────────

    async def get_lock(self, session_id: str) -> asyncio.Lock:
        if self._meta_lock is None:
            self._meta_lock = asyncio.Lock()
        async with self._meta_lock:
            if session_id not in self._locks:
                self._locks[session_id] = asyncio.Lock()
            return self._locks[session_id]

    # ── Atomic horoscope helpers (#2) ────────────────────────────────────

    def set_horoscope_atomic(
        self,
        session_id: str,
        horoscope: str,
        chart_table: str,
        birth_info: "dict | None",
    ) -> None:
        """Write horoscope / chart_table / birth_info in one operation."""
        self.horoscopes[session_id] = horoscope
        self.chart_tables[session_id] = chart_table or ""
        if birth_info:
            self.birth_infos[session_id] = birth_info

    def get_horoscope_snapshot(
        self, session_id: str
    ) -> "tuple[str, str, dict | None]":
        """Return (horoscope, chart_table, birth_info) as a consistent read."""
        return (
            self.horoscopes.get(session_id, ""),
            self.chart_tables.get(session_id, ""),
            self.birth_infos.get(session_id),
        )

    # ── Thread-ID helpers ────────────────────────────────────────────────

    def ensure_thread_id(self, session_id: str) -> str:
        if session_id not in self.thread_ids:
            self.thread_ids[session_id] = str(uuid.uuid4())
        return self.thread_ids[session_id]

    def new_thread_id(self, session_id: str) -> str:
        tid = str(uuid.uuid4())
        self.thread_ids[session_id] = tid
        return tid

    def new_agent_thread(self, session_id: str) -> str:
        tid = str(uuid.uuid4())
        self.agent_threads[session_id] = tid
        return tid

    # ── Message helpers ──────────────────────────────────────────────────

    def trim_messages(self, session_id: str, max_n: int) -> None:
        msgs = self.messages.get(session_id)
        if msgs and len(msgs) > max_n:
            self.messages[session_id] = msgs[-max_n:]

    def pipeline_messages(self, session_id: str, has_horoscope: bool, max_with_chart: int = 12) -> list:
        # Invariant: max_with_chart should be <= _MAX_HISTORY in main.py (currently 20)
        """Return the message slice to feed into the pipeline.

        When a chart already exists we only need recent turns; without a chart
        the full history is required so birth-info collection stays coherent.
        """
        msgs = self.messages.get(session_id, [])
        if has_horoscope:
            return msgs[-max_with_chart:] if len(msgs) > max_with_chart else msgs
        return msgs

    # ── Session cleanup ──────────────────────────────────────────────────

    def clear_session(self, session_id: str) -> None:
        """Remove all data for this session (used on /reset)."""
        self.messages.pop(session_id, None)
        self.horoscopes.pop(session_id, None)
        self.chart_tables.pop(session_id, None)
        self.birth_infos.pop(session_id, None)
        self.bazi_docs.pop(session_id, None)
        self.agent_threads.pop(session_id, None)
        self.thread_ids.pop(session_id, None)
        self.user_profiles.pop(session_id, None)
        self._last_seen.pop(session_id, None)
        self._locks.pop(session_id, None)

    def clear_chart(self, session_id: str) -> None:
        """Remove chart-related data only (used on /clear-chart)."""
        self.horoscopes.pop(session_id, None)
        self.chart_tables.pop(session_id, None)
        self.birth_infos.pop(session_id, None)
        self.bazi_docs.pop(session_id, None)
        self.messages.pop(session_id, None)
        self.thread_ids.pop(session_id, None)
        self.user_profiles.pop(session_id, None)
