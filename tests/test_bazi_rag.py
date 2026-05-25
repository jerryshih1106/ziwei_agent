"""Unit tests for BaZi RAG logic from main.py.

Uses exec() to load only the relevant snippet from main.py so no heavy
dependencies (FastAPI, linebot, langchain) need to be imported. This also
avoids polluting sys.modules with stubs that would interfere with other test
modules.
"""
import os
import re
import pathlib
import textwrap
import unittest.mock as mock
import pytest


_MAIN_PATH = pathlib.Path(__file__).parent.parent / "main.py"


def _extract_bazi_rag_functions(bazi_md_path: str):
    """Exec the BaZi-RAG snippet from main.py with the knowledge file path
    overridden to bazi_md_path. Returns (_build_bazi_rag, _load_bazi_knowledge_for_chart, _SHENSHA_ALIASES)."""
    src = _MAIN_PATH.read_text(encoding="utf-8")

    # Extract from _BAZI_KNOWLEDGE_FILE definition to just before _BAZI_INTERPRET_PROMPT
    start = src.index("_BAZI_KNOWLEDGE_FILE = os.path.join(")
    end   = src.index("_BAZI_INTERPRET_PROMPT")
    snippet = src[start:end].strip()

    # Python 3.9: `tuple[...] | None` is invalid at runtime (needs 3.10+).
    # Strip the annotation so the variable declaration still works.
    snippet = re.sub(
        r'(_BAZI_RAG_CACHE)\s*:.*?(?==)',
        r'\1 ',
        snippet,
    )

    # Append an override so the variable points to the temp file.
    # Appending (not replacing) avoids fragile regex over nested parens;
    # the last assignment wins in Python's sequential execution.
    snippet += f'\n_BAZI_KNOWLEDGE_FILE = {repr(bazi_md_path)}\n'

    ns = {"re": re, "os": os, "logger": mock.MagicMock(), "__file__": str(_MAIN_PATH)}
    exec(snippet, ns)  # noqa: S102
    return ns["_build_bazi_rag"], ns["_load_bazi_knowledge_for_chart"], ns["_SHENSHA_ALIASES"]


# ── Minimal bazi.md fixture ───────────────────────────────────────────────

_MINI_BAZI_MD = textwrap.dedent("""\
    # 八字命理知識庫

    ## 核心概念
    四柱八字由年柱、月柱、日柱、時柱組成。

    ## 神煞定義
    神煞是命盤中的特殊標記。

    ### 神煞要義

    1，天乙貴人------
    天乙貴人是最吉祥的貴人星，代表得到他人提拔與幫助。
    凡命中有天乙貴人，遇難呈祥，逢凶化吉。

    2，文昌星------
    文昌星主文學、考試、聰明智慧。
    有文昌者善於學習，適合文職工作。

    3，驛馬星------
    驛馬星主動，代表奔波、出行、遷移。
    命有驛馬者，常需外出奔走。

    4，空亡煞------
    空亡又稱六甲空亡，代表空洞、虛無。
    所逢宮位的六親緣薄，凡事多阻。

    5，天德貴人、天德合------
    天德貴人是上天賜予的德行之星。
    天德合與天德貴人同論，逢凶化吉之力極強。

    6，紅鸞天喜------
    紅鸞主桃花感情，天喜主喜慶。
    二星同論，流年逢之主婚嫁、喜事。
""")


@pytest.fixture
def mini_bazi_file(tmp_path):
    p = tmp_path / "bazi.md"
    p.write_text(_MINI_BAZI_MD, encoding="utf-8")
    return str(p)


@pytest.fixture
def rag_functions(mini_bazi_file):
    build_fn, load_fn, aliases = _extract_bazi_rag_functions(mini_bazi_file)
    return build_fn, load_fn, aliases


# ═══════════════════════════════════════════════════════════════════════════
# _build_bazi_rag
# ═══════════════════════════════════════════════════════════════════════════

class TestBuildBaziRag:
    def test_core_text_contains_definition_sections(self, rag_functions):
        build_fn, _, _ = rag_functions
        core, index = build_fn()
        assert "核心概念" in core
        assert "神煞定義" in core

    def test_core_text_excludes_shensha_entries(self, rag_functions):
        build_fn, _, _ = rag_functions
        core, index = build_fn()
        assert "天乙貴人" not in core

    def test_index_contains_expected_entries(self, rag_functions):
        build_fn, _, _ = rag_functions
        _, index = build_fn()
        assert "天乙貴人" in index
        assert "文昌星" in index
        assert "驛馬星" in index

    def test_index_entry_capped_at_500_chars(self, rag_functions):
        build_fn, _, _ = rag_functions
        _, index = build_fn()
        for k, v in index.items():
            assert len(v) <= 510, f"Entry '{k}' too long: {len(v)}"

    def test_two_name_entry_indexed_by_first_name(self, rag_functions):
        build_fn, _, _ = rag_functions
        _, index = build_fn()
        assert "天德貴人" in index

    def test_empty_index_on_missing_section(self, tmp_path):
        p = tmp_path / "bad.md"
        p.write_text("no shensha section here", encoding="utf-8")
        build_fn, _, _ = _extract_bazi_rag_functions(str(p))
        core, index = build_fn()
        assert index == {}
        assert "no shensha section here" in core

    def test_result_is_cached(self, rag_functions):
        build_fn, _, _ = rag_functions
        r1 = build_fn()
        r2 = build_fn()
        assert r1 is r2   # same tuple object (cached)


# ═══════════════════════════════════════════════════════════════════════════
# _load_bazi_knowledge_for_chart
# ═══════════════════════════════════════════════════════════════════════════

class TestLoadBaziKnowledgeForChart:
    def _bazi(self, shenshas):
        return {"shenshas": shenshas}

    def test_exact_match_found(self, rag_functions):
        _, load_fn, _ = rag_functions
        result = load_fn(self._bazi({"年柱": ["天乙貴人"]}))
        assert "天乙貴人" in result

    def test_no_shenshas_returns_core_only(self, rag_functions):
        _, load_fn, _ = rag_functions
        result = load_fn(self._bazi({}))
        assert "核心概念" in result
        assert "命盤相關神煞知識" not in result

    def test_multiple_shenshas_all_included(self, rag_functions):
        _, load_fn, _ = rag_functions
        result = load_fn(self._bazi({"年柱": ["天乙貴人", "文昌星"]}))
        assert "天乙貴人" in result
        assert "文昌星" in result

    def test_alias_kong_wang(self, rag_functions):
        _, load_fn, _ = rag_functions
        # 六甲空亡 → alias "空亡" → matches "空亡煞" entry via substring
        result = load_fn(self._bazi({"日柱": ["六甲空亡"]}))
        assert "空亡" in result

    def test_two_names_same_entry_no_duplicate(self, rag_functions):
        _, load_fn, _ = rag_functions
        # 紅鸞 and 天喜 both live in entry "6，紅鸞天喜"; should appear once
        result = load_fn(self._bazi({"年柱": ["紅鸞"], "月柱": ["天喜"]}))
        assert result.count("紅鸞天喜") == 1

    def test_tian_de_he_found_via_text_search(self, rag_functions):
        _, load_fn, _ = rag_functions
        # 天德合 is not the index key but appears in the 天德貴人 entry body
        result = load_fn(self._bazi({"月柱": ["天德合"]}))
        assert "天德" in result

    def test_unknown_shensha_gracefully_skipped(self, rag_functions):
        _, load_fn, _ = rag_functions
        result = load_fn(self._bazi({"年柱": ["完全不存在的神煞XYZ"]}))
        assert "核心概念" in result   # core always present; no crash

    def test_core_text_always_prepended(self, rag_functions):
        _, load_fn, _ = rag_functions
        result = load_fn(self._bazi({"年柱": ["天乙貴人"]}))
        assert result.startswith("# 八字命理知識庫")

    def test_result_shorter_than_full_file(self, rag_functions, mini_bazi_file):
        _, load_fn, _ = rag_functions
        full = open(mini_bazi_file, encoding="utf-8").read()
        result = load_fn(self._bazi({"年柱": ["天乙貴人"]}))
        assert len(result) < len(full)


# ═══════════════════════════════════════════════════════════════════════════
# _SHENSHA_ALIASES
# ═══════════════════════════════════════════════════════════════════════════

class TestShenShaAliases:
    def test_kong_wang_alias(self, rag_functions):
        _, _, aliases = rag_functions
        assert aliases.get("六甲空亡") == "空亡"

    def test_ba_zhuan_alias(self, rag_functions):
        _, _, aliases = rag_functions
        assert aliases.get("八專日") == "八專"

    def test_liu_xiu_alias(self, rag_functions):
        _, _, aliases = rag_functions
        assert aliases.get("六秀日") == "六秀"

    def test_yin_yang_alias(self, rag_functions):
        _, _, aliases = rag_functions
        assert aliases.get("陰差陽錯") == "陰陽差錯"

    def test_de_xiu_alias(self, rag_functions):
        _, _, aliases = rag_functions
        assert aliases.get("德秀貴人") == "天德貴人"
