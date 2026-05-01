"""
Lightweight markdown-based RAG processors.
Replaces ModernBERT semantic search with two lighter alternatives:

  MdRagProcessor        — keyword lookup (matching mode)
  AgentSkillRagProcessor — LLM-guided lookup (agent_skill mode)

No ML model loading required for either — near-zero startup time and memory.
"""
import json
import logging
import os

logger = logging.getLogger(__name__)

_KNOWLEDGE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "knowledge",
)
_KNOWLEDGE_FILES = [
    "stars_main.md",
    "palaces.md",
    "stars_minor.md",
    "limits.md",
]

# Module-level index cache: {keyword: definition_text}
_INDEX: dict[str, str] | None = None


def _build_index() -> dict[str, str]:
    """
    Parse all knowledge markdown files and build a flat keyword → content index.
    Each entry starts with a '# <keyword>' header and ends before the next '---'.
    """
    index: dict[str, str] = {}
    for fname in _KNOWLEDGE_FILES:
        path = os.path.join(_KNOWLEDGE_DIR, fname)
        if not os.path.exists(path):
            logger.warning(f"Knowledge file not found: {path}")
            continue
        with open(path, encoding="utf-8") as f:
            text = f.read()

        for block in text.split("---"):
            block = block.strip()
            if not block:
                continue
            lines = block.splitlines()
            # Find the first '# header' line (skip HTML comments)
            header_idx = next(
                (i for i, line in enumerate(lines) if line.strip().startswith("#")),
                None,
            )
            if header_idx is None:
                continue
            keyword = lines[header_idx].strip().lstrip("#").strip()
            content = "\n".join(lines[header_idx + 1:]).strip()
            if keyword and content:
                index[keyword] = content

    logger.info(f"Knowledge index built: {len(index)} entries")
    return index


def _get_index() -> dict[str, str]:
    global _INDEX
    if _INDEX is None:
        _INDEX = _build_index()
    return _INDEX


# ── Mode 1: matching ─────────────────────────────────────────────────────────

class MdRagProcessor:
    """
    Keyword-based lookup from local markdown knowledge files.
    Tries exact match first, then partial/suffix match.
    API-compatible with RagProcessor so gen_ziwei.py can swap transparently.
    """

    def retrieve_definitions(
        self,
        _domain_df,        # kept for API compatibility, not used
        keywords: list[str],
        top_k: int = 1,    # kept for API compatibility
    ) -> str:
        index = _get_index()
        results: list[str] = []

        for kw in keywords:
            if kw in index:
                results.append(f"**{kw}**\n\n{index[kw]}")
                continue
            # Partial / suffix match (e.g. "紫薇星" → "紫薇")
            matched = next(
                (key for key in index if kw in key or key in kw),
                None,
            )
            if matched:
                results.append(f"**{matched}**\n\n{index[matched]}")

        if not results:
            return ""

        # Deduplicate while preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for item in results:
            if item not in seen:
                seen.add(item)
                unique.append(item)

        return "\n\n---\n\n".join(unique)


# ── Mode 3: agent_skill ──────────────────────────────────────────────────────

_SELECT_KEYS_PROMPT = """\
你是紫微斗數知識庫的檢索助手。
知識庫中有以下條目：
{available_keys}

根據以下命盤描述，請列出最相關的條目名稱（JSON 陣列，最多 {top_k} 個，只回傳陣列不要其他說明）：
{context}"""


class AgentSkillRagProcessor:
    """
    LLM-guided lookup from local markdown knowledge files.

    Flow:
      1. Show the LLM the full list of available knowledge keys.
      2. LLM selects the most relevant keys for the given context.
      3. Retrieve and return the full definitions for those keys.

    This handles fuzzy / variant spellings and implicit references that
    pure keyword matching would miss.
    """

    def retrieve_definitions(
        self,
        _domain_df,
        keywords: list[str],
        top_k: int = 3,
    ) -> str:
        from ..utils.utils import build_llm
        from ..global_config import GlobalConfig

        index = _get_index()
        available_keys = list(index.keys())
        context = "、".join(keywords)

        prompt = _SELECT_KEYS_PROMPT.format(
            available_keys="、".join(available_keys),
            top_k=top_k,
            context=context,
        )

        llm = build_llm(GlobalConfig.MODEL)
        raw = llm.invoke(prompt).content.strip()

        # Parse the JSON array the LLM returned
        selected_keys: list[str] = []
        try:
            # Strip markdown code fences if present
            cleaned = raw.strip("` \n")
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].strip()
            selected_keys = json.loads(cleaned)
            if not isinstance(selected_keys, list):
                selected_keys = []
        except (json.JSONDecodeError, ValueError):
            logger.warning(f"AgentSkillRag: failed to parse LLM key selection: {raw!r}")
            # Fallback to exact match on the original keywords
            selected_keys = [kw for kw in keywords if kw in index]

        results: list[str] = []
        for key in selected_keys:
            if key in index:
                results.append(f"**{key}**\n\n{index[key]}")
            else:
                # Fuzzy fallback: partial match
                matched = next(
                    (k for k in index if key in k or k in key),
                    None,
                )
                if matched:
                    results.append(f"**{matched}**\n\n{index[matched]}")

        return "\n\n---\n\n".join(results) if results else ""
