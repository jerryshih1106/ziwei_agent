"""Unit tests for token-optimisation logic in chat_bot/node/chat.py.

chat.py is loaded directly via spec_from_file_location so the package
__init__.py cascade (detect_intent, gen_ziwei, …) is bypassed entirely.
External deps (langchain_core, tiktoken) and internal chat_bot leaf modules
are stubbed with minimal implementations so no LLM or DB is touched.
"""
import sys
import types
import importlib.util
import pathlib
import unittest.mock as mock
import pytest


_ROOT = pathlib.Path(__file__).parent.parent


# ── 1. External dependency stubs ─────────────────────────────────────────

def _stub(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    if name not in sys.modules:
        sys.modules[name] = m
    return m


# Real message classes so isinstance() checks inside chat.py work correctly
class HumanMessage:
    def __init__(self, content=""):
        self.content = content

class AIMessage:
    def __init__(self, content=""):
        self.content = content

class SystemMessage:
    def __init__(self, content=""):
        self.content = content

_msgs_stub = _stub("langchain_core.messages",
                   HumanMessage=HumanMessage,
                   AIMessage=AIMessage,
                   SystemMessage=SystemMessage)

_stub("langchain_core.prompts",
      ChatPromptTemplate=mock.MagicMock(),
      PromptTemplate=mock.MagicMock())

_lc_stub = _stub("langchain_core",
                 messages=_msgs_stub,
                 prompts=sys.modules["langchain_core.prompts"])


class _DummyEnc:
    """Stub tiktoken encoder: 1 token per character."""
    def encode(self, text):
        return list(text)

_stub("tiktoken",
      encoding_for_model=mock.MagicMock(side_effect=KeyError("unknown")),
      get_encoding=mock.MagicMock(return_value=_DummyEnc()))


# ── 2. Internal chat_bot leaf stubs ──────────────────────────────────────

_stub("chat_bot.node.chat_state", ChatState=mock.MagicMock())

_stub("chat_bot.global_config",
      GlobalConfig=type("GlobalConfig", (), {
          "MODEL": "gemini-2.5-flash-lite",
          "IS_DEBUG": False,
          "TOKEN_NUM": 2500,
          "TPM_TIME": 0,
      }))

_stub("chat_bot.prompt",
      CHAT_PROMPT=(
          "dummy {horoscope} {chart_table_section} "
          "{bazi_doc} {user_profile} {current_year}"
      ),
      MEMORY_PROMPT="dummy {hist_chat}")


# ── 3. Parent package stubs with __path__ so relative imports resolve ────
# chat.py uses:  from .chat_state   → looks in sys.modules["chat_bot.node.chat_state"]
#                from ..global_config → looks in sys.modules["chat_bot.global_config"]
#                from ..prompt        → looks in sys.modules["chat_bot.prompt"]
# We only need parent packages in sys.modules with __path__ set.

def _pkg_stub(name, path):
    m = types.ModuleType(name)
    m.__path__ = [path]
    m.__package__ = name
    if name not in sys.modules:
        sys.modules[name] = m
    return m

_pkg_stub("chat_bot",       str(_ROOT / "chat_bot"))
_pkg_stub("chat_bot.node",  str(_ROOT / "chat_bot" / "node"))


# ── 4. Load chat.py directly (bypass __init__.py cascade) ────────────────

_CHAT_PY = _ROOT / "chat_bot" / "node" / "chat.py"
_spec = importlib.util.spec_from_file_location(
    "chat_bot.node.chat",
    str(_CHAT_PY),
)
chat_mod = importlib.util.module_from_spec(_spec)
chat_mod.__package__ = "chat_bot.node"
sys.modules["chat_bot.node.chat"] = chat_mod
_spec.loader.exec_module(chat_mod)


# ── 5. Grab the functions under test ─────────────────────────────────────

_needs_chart_table          = chat_mod._needs_chart_table
_format_chart_table_section = chat_mod._format_chart_table_section
_compress_horoscope         = chat_mod._compress_horoscope
_filter_horoscope_by_topic  = chat_mod._filter_horoscope_by_topic
_horoscope_for_chat         = chat_mod._horoscope_for_chat
_is_chart_message           = chat_mod._is_chart_message
_format_history_string      = chat_mod._format_history_string
get_token_count             = chat_mod.get_token_count


# ═══════════════════════════════════════════════════════════════════════════
# 1. chart_table 條件注入
# ═══════════════════════════════════════════════════════════════════════════

class TestNeedsChartTable:
    def test_palace_keyword_matches(self):
        assert _needs_chart_table("夫妻宮有什麼星") is True

    def test_star_keyword_matches(self):
        assert _needs_chart_table("紫微星在哪個宮") is True

    def test_structure_keyword_matches(self):
        assert _needs_chart_table("幫我看命盤表格") is True

    def test_generic_question_no_match(self):
        assert _needs_chart_table("今年運勢如何") is False

    def test_love_question_no_match(self):
        assert _needs_chart_table("我的感情運如何") is False

    def test_empty_message(self):
        assert _needs_chart_table("") is False

    def test_multiple_keywords(self):
        assert _needs_chart_table("財帛宮和官祿宮各有哪顆星") is True


class TestFormatChartTableSection:
    TABLE = "宮位|星曜\n命宮|紫微"

    def test_returns_section_when_keyword_present(self):
        result = _format_chart_table_section("命宮有什麼", self.TABLE)
        assert "顧客的命盤表格" in result
        assert self.TABLE in result

    def test_returns_empty_when_no_keyword(self):
        result = _format_chart_table_section("今年運勢如何", self.TABLE)
        assert result == ""

    def test_returns_empty_when_no_table(self):
        result = _format_chart_table_section("命宮有什麼", "")
        assert result == ""

    def test_returns_empty_when_both_missing(self):
        assert _format_chart_table_section("", "") == ""


# ═══════════════════════════════════════════════════════════════════════════
# 2. Horoscope 壓縮
# ═══════════════════════════════════════════════════════════════════════════

_LONG_BLOCK  = "命宮：" + "紫微天府坐命，性格沉穩大器，" * 30
_SHORT_BLOCK = "夫妻宮：天機星，感情多變。"
_SAMPLE_HOROSCOPE = f"{_LONG_BLOCK}\n\n{_SHORT_BLOCK}"


class TestCompressHoroscope:
    def test_short_block_unchanged(self):
        assert _compress_horoscope(_SHORT_BLOCK) == _SHORT_BLOCK

    def test_long_block_truncated(self):
        result = _compress_horoscope(_LONG_BLOCK)
        assert len(result) <= 210

    def test_truncation_ends_at_sentence_boundary(self):
        block = "命宮：紫微坐命，性格沉穩。天府同度，財運豐厚。" + "X" * 300
        result = _compress_horoscope(block)
        assert result.endswith("。") or result.endswith("…")

    def test_empty_passthrough(self):
        assert _compress_horoscope("") == ""

    def test_multi_block_each_truncated(self):
        long = "A。" * 200
        horoscope = f"{long}\n\n{long}"
        blocks = _compress_horoscope(horoscope).split("\n\n")
        assert len(blocks) == 2
        for b in blocks:
            assert len(b) <= 210


# ═══════════════════════════════════════════════════════════════════════════
# 3. Topic-based horoscope filtering
# ═══════════════════════════════════════════════════════════════════════════

_LOVE_BLOCK   = "夫妻宮：天機星，感情多變，需要多溝通。"
_CAREER_BLOCK = "官祿宮：紫微星坐守，事業宏圖，領導力強。"
_WEALTH_BLOCK = "財帛宮：天府星，財庫穩固，適合理財。"
_MING_BLOCK   = "命宮：七殺星坐命，個性剛強，有開創精神。"
_HEALTH_BLOCK = "疾厄宮：廉貞星，注意心血管。"

_FULL = "\n\n".join([_MING_BLOCK, _LOVE_BLOCK, _CAREER_BLOCK, _WEALTH_BLOCK, _HEALTH_BLOCK])


class TestFilterHoroscopeByTopic:
    def test_love_returns_love_blocks(self):
        assert "夫妻宮" in _filter_horoscope_by_topic("我的感情如何", _FULL)

    def test_love_excludes_career(self):
        assert "官祿宮" not in _filter_horoscope_by_topic("我的感情如何", _FULL)

    def test_career_returns_career_blocks(self):
        assert "官祿宮" in _filter_horoscope_by_topic("工作運勢", _FULL)

    def test_always_includes_ming_block(self):
        assert "命宮" in _filter_horoscope_by_topic("我的感情如何", _FULL)

    def test_unmatched_returns_something(self):
        assert len(_filter_horoscope_by_topic("你好嗎", _FULL)) > 0

    def test_health_question(self):
        assert "疾厄宮" in _filter_horoscope_by_topic("我的身體狀況", _FULL)

    def test_empty_horoscope(self):
        assert _filter_horoscope_by_topic("感情", "") == ""

    def test_wealth_question(self):
        assert "財帛宮" in _filter_horoscope_by_topic("財運如何", _FULL)


# ═══════════════════════════════════════════════════════════════════════════
# 4. _horoscope_for_chat priority
# ═══════════════════════════════════════════════════════════════════════════

class TestHoroscopeForChat:
    def test_empty_passthrough(self):
        assert _horoscope_for_chat("感情如何", "") == ""

    def test_detail_keyword_returns_full(self):
        assert _horoscope_for_chat("請詳細分析", _FULL) == _FULL

    def test_palace_keyword_returns_full(self):
        assert _horoscope_for_chat("命宮有什麼星", _FULL) == _FULL

    def test_topic_returns_subset(self):
        result = _horoscope_for_chat("感情如何", _FULL)
        assert len(result) <= len(_FULL)
        assert "夫妻宮" in result

    def test_generic_returns_compressed(self):
        result = _horoscope_for_chat("你好", _FULL)
        assert len(result) <= len(_FULL)


# ═══════════════════════════════════════════════════════════════════════════
# 5. _is_chart_message
# ═══════════════════════════════════════════════════════════════════════════

class TestIsChartMessage:
    def test_chart_message_detected(self):
        assert _is_chart_message(AIMessage(content="我已經排好你的命盤了，以下分析…")) is True

    def test_normal_message_not_detected(self):
        assert _is_chart_message(AIMessage(content="你的感情運不錯")) is False

    def test_human_message_not_detected(self):
        assert _is_chart_message(HumanMessage(content="請幫我算命")) is False

    def test_empty_content(self):
        assert _is_chart_message(AIMessage(content="")) is False


# ═══════════════════════════════════════════════════════════════════════════
# 6. _format_history_string
# ═══════════════════════════════════════════════════════════════════════════

class TestFormatHistoryString:
    def test_roles_labeled(self):
        result = _format_history_string([
            HumanMessage(content="你好"),
            AIMessage(content="你好！"),
        ])
        assert "用戶: 你好" in result
        assert "助手: 你好" in result

    def test_empty_list(self):
        assert _format_history_string([]) == ""

    def test_system_labeled(self):
        result = _format_history_string([SystemMessage(content="sys")])
        assert "系統: sys" in result

    def test_order_preserved(self):
        lines = _format_history_string([
            HumanMessage(content="A"),
            AIMessage(content="B"),
            HumanMessage(content="C"),
        ]).split("\n")
        assert lines[0].startswith("用戶")
        assert lines[1].startswith("助手")
        assert lines[2].startswith("用戶")


# ═══════════════════════════════════════════════════════════════════════════
# 7. get_token_count
# ═══════════════════════════════════════════════════════════════════════════

class TestGetTokenCount:
    def test_non_zero_for_text(self):
        assert get_token_count(["hello world"], model="gemini") > 0

    def test_empty_list(self):
        assert get_token_count([]) == 0

    def test_empty_string(self):
        assert get_token_count([""]) == 0

    def test_two_identical_items_double(self):
        s = get_token_count(["abc"], model="x")
        assert get_token_count(["abc", "abc"], model="x") == s * 2
