import os
import logging
import pandas as pd
from concurrent.futures import ThreadPoolExecutor
from langchain_core.messages import AIMessage
from langchain_core.prompts import PromptTemplate

from .chat_state import ChatState
from ziweidoushu.kernel import ZiweiChart
from ziweidoushu.base import ZiWeiConfig
from ..utils.utils import build_llm, process_llm_output, sleep_for_tpm, langfuse_observe
from ..prompt import KEYWORD_PROMPT, ZIWEI_PROMPT
from ..global_config import GlobalConfig
from ..llm_client import is_transient_error

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DOMAIN_DF_CACHE: pd.DataFrame | None = None

logger = logging.getLogger(__name__)

_rag_processor = None

# Max parallel workers for palace analysis.
# 12 == one worker per palace; all palaces finish in a single parallel round.
# Reduce if you hit API rate-limit errors (e.g. set to 6).
_PALACE_WORKERS = 12


def _create_rag_processor(mode: str):
    """Factory — return the correct RAG backend for *mode* (RAG_MODE env var)."""
    if mode == "rag":
        from ..utils.rag_utils import RagProcessor
        logger.info("RAG mode: rag (ModernBERT semantic search)")
        return RagProcessor()
    if mode == "agent_skill":
        from ..utils.md_rag_utils import AgentSkillRagProcessor
        logger.info("RAG mode: agent_skill (LLM-guided lookup)")
        return AgentSkillRagProcessor()
    from ..utils.md_rag_utils import MdRagProcessor
    logger.info("RAG mode: matching (keyword lookup)")
    return MdRagProcessor()


def _get_rag_processor():
    global _rag_processor
    if _rag_processor is None:
        _rag_processor = _create_rag_processor(GlobalConfig.RAG_MODE)
    return _rag_processor


def _get_domain_df() -> pd.DataFrame:
    global _DOMAIN_DF_CACHE
    if _DOMAIN_DF_CACHE is None:
        _DOMAIN_DF_CACHE = pd.read_csv(
            os.path.join(_PROJECT_ROOT, "basic_document", "star_palace_meaning.csv")
        )
    return _DOMAIN_DF_CACHE


def generate_ziwei(state: ChatState):
    try:
        birth_info = state.birth_info
        config = ZiWeiConfig(
            birth_info["year"], birth_info["month"],
            birth_info["day"], birth_info["hour"],
            bool(birth_info["is_male"]),
            is_lunar=bool(birth_info.get("is_lunar", False)),
        )
        ziwei_inst = ZiweiChart(config)
        df = ziwei_inst.gen_chart()
        ziwei_report = get_palace_information(df, _get_domain_df())
        state.horoscope = ziwei_report
        chart_text = transformed_llm_visualize(df.to_markdown())
        state.chart_table = chart_text
        state.messages.append(
            AIMessage(content=f'我已經排好你的命盤了: \n 生日: {birth_info["year"]}年{birth_info["month"]}月{birth_info["day"]}日, {birth_info["hour"]}時\n\n\n {chart_text}\n\n\n 有什麼需要提問的嗎?')
        )
    except ValueError:
        # 資料錯誤（日期無效、月份超出範圍）— 清空出生資訊讓使用者重填
        logger.exception("generate_ziwei 資料錯誤")
        state.birth_info = {
            "year": None, "month": None, "day": None, "hour": None, "is_male": None
        }
        state.is_fortune = False
        state.messages.append(
            AIMessage(content="⚠️ 出生資料有誤，請確認年月日時辰是否正確（例如是否有閏月、日期是否存在）。請重新告訴我你的出生資訊。")
        )
    except Exception as exc:
        logger.exception("generate_ziwei 系統錯誤")
        state.is_fortune = False
        if is_transient_error(exc):
            # 暫時性錯誤（限流/API 繁忙）— 保留 birth_info，讓使用者不必重新輸入
            state.messages.append(
                AIMessage(content="⚠️ AI 服務目前繁忙，你的出生資料已保留。請稍後輸入任意文字重試排盤。")
            )
        else:
            # 其他未知系統錯誤 — 清空避免無限重試
            state.birth_info = {
                "year": None, "month": None, "day": None, "hour": None, "is_male": None
            }
            state.messages.append(
                AIMessage(content="⚠️ 系統發生錯誤，請稍後再試。如問題持續請聯絡客服。")
            )
    return state


def transformed_llm_visualize(context: str, model: str | None = None) -> str:
    # Bug #8 Fix: 移除不必要的 LLM 呼叫（只為排版表格）。pandas to_markdown() 已是合法 Markdown，
    # 直接回傳即可，省一次 API call 與延遲。
    return context


@langfuse_observe(name="split_keywords")
@sleep_for_tpm
def split_keywords(context: str, model: str | None = None) -> list:
    # Bug #2 Fix: use None sentinel instead of GlobalConfig.MODEL as default so the value
    # is read at call time (not frozen at import time when .env may not yet be loaded).
    llm = build_llm(model or GlobalConfig.MODEL)
    prompt = PromptTemplate.from_template(KEYWORD_PROMPT)
    raw = process_llm_output((prompt | llm).invoke({"sentence": context}).content)
    return [kw.strip() for kw in raw.split(",") if kw.strip()]


@langfuse_observe(name="analysis_ziwei", as_type="generation")
@sleep_for_tpm
def analysis_ziwei(context: str, rag_document: str, model: str | None = None) -> str:
    # Bug #2 Fix: use None sentinel instead of GlobalConfig.MODEL as default.
    llm = build_llm(model or GlobalConfig.MODEL)
    prompt = PromptTemplate.from_template(ZIWEI_PROMPT)
    return process_llm_output((prompt | llm).invoke({"sentence": context, "rag_report": rag_document}).content)


def format_row(row) -> str:
    star_val = row["星"]
    if pd.notna(star_val) and star_val:
        stars = [f"{s.strip()}星" for s in str(star_val).split(",")]
        sentence = f"{', '.join(stars)}入{row['宮']}位於{row['天干']}{row['地支']}"
    else:
        sentence = f"沒有星落入{row['宮']}位於{row['天干']}{row['地支']}"

    hua_val = row["化"]
    if pd.notna(hua_val) and hua_val:
        sentence += f"，化{hua_val}"
    else:
        sentence += "，沒有化星"

    if row.get("大限") or row.get("小限"):
        limits = []
        if row.get("大限"):
            limits.append(f"大限在{row['大限']}")
        if row.get("小限"):
            limits.append(f"小限在{row['小限']}")
        sentence += "，" + "，".join(limits)

    return sentence


def _extract_keywords_from_row(row) -> list[str]:
    """
    For RAG_MODE=matching: extract star and palace names directly from the
    row data without an LLM call, saving one API round-trip per palace.
    """
    keywords = []
    star_val = row["星"]
    if pd.notna(star_val) and star_val:
        for s in str(star_val).split(","):
            s = s.strip()
            if s:
                keywords.append(s)  # e.g. "紫薇"
    palace = row["宮"]
    if pd.notna(palace) and palace:
        keywords.append(str(palace).strip())  # e.g. "命宮"
    return keywords


def _analyze_row(row, rag_inst, ziwei_domain_df: pd.DataFrame) -> str:
    """Analyze a single palace row — called in parallel across all palaces."""
    raw_palace = row.get("宮", "")
    palace = str(raw_palace).strip() if pd.notna(raw_palace) and raw_palace not in (None, "None", "nan", "") else ""
    sentence = format_row(row)
    if GlobalConfig.RAG_MODE == "matching":
        keyword_list = _extract_keywords_from_row(row)
    else:
        keyword_list = split_keywords(sentence)
    rag_document = rag_inst.retrieve_definitions(ziwei_domain_df, keyword_list)
    analysis = analysis_ziwei(sentence, rag_document)
    return f"## {palace}\n\n{analysis}" if palace and analysis else analysis


def get_palace_information(df: pd.DataFrame, ziwei_domain_df: pd.DataFrame) -> str:
    """
    Analyze every palace in parallel using a thread pool.
    For RAG_MODE=matching, keyword extraction is done locally (no LLM call),
    so each palace only needs one LLM call (analysis_ziwei).
    """
    rag_inst = _get_rag_processor()
    rows = [row for _, row in df.iterrows()]

    logger.info(f"Analyzing {len(rows)} palaces with {_PALACE_WORKERS} workers "
                f"(RAG_MODE={GlobalConfig.RAG_MODE})")

    with ThreadPoolExecutor(max_workers=_PALACE_WORKERS) as executor:
        analyses = list(executor.map(
            lambda row: _analyze_row(row, rag_inst, ziwei_domain_df),
            rows,
        ))

    return "\n\n".join(a for a in analyses if a)
