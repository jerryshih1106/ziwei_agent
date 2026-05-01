import os
import logging
import pandas as pd
from langchain_core.messages import AIMessage
from langchain_core.prompts import PromptTemplate

from .chat_state import ChatState
from ziweidoushu.kernel import ZiweiChart
from ziweidoushu.base import ZiWeiConfig
from ..utils.utils import build_llm, process_llm_output, sleep_for_tpm
from ..prompt import KEYWORD_PROMPT, ZIWEI_PROMPT
from ..global_config import GlobalConfig

# Fix #6：_PROJECT_ROOT 放在所有 import 之後
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DOMAIN_DF_CACHE: pd.DataFrame | None = None  # Fix #4：CSV module-level 快取

logger = logging.getLogger(__name__)

# Fix #8：module-level singleton，避免每次 get_palace_information 重新建立（含模型載入）
_rag_processor = None


def _get_rag_processor():
    global _rag_processor
    if _rag_processor is None:
        mode = GlobalConfig.RAG_MODE
        if mode == "rag":
            from ..utils.rag_utils import RagProcessor
            _rag_processor = RagProcessor()
            logger.info("RAG mode: rag (ModernBERT semantic search)")
        elif mode == "agent_skill":
            from ..utils.md_rag_utils import AgentSkillRagProcessor
            _rag_processor = AgentSkillRagProcessor()
            logger.info("RAG mode: agent_skill (LLM-guided lookup)")
        else:  # default: matching
            from ..utils.md_rag_utils import MdRagProcessor
            _rag_processor = MdRagProcessor()
            logger.info("RAG mode: matching (keyword lookup)")
    return _rag_processor


def _get_domain_df() -> pd.DataFrame:
    """Fix #4：首次使用時載入 CSV，之後直接回傳快取。"""
    global _DOMAIN_DF_CACHE
    if _DOMAIN_DF_CACHE is None:
        _DOMAIN_DF_CACHE = pd.read_csv(
            os.path.join(_PROJECT_ROOT, "basic_document", "star_palace_meaning.csv")
        )
    return _DOMAIN_DF_CACHE


def generate_ziwei(state: ChatState):
    """Fix #5：包裝 try/except，無效生日不 crash 整個 graph。"""
    try:
        birth_info = state.birth_info
        config = ZiWeiConfig(
            birth_info["year"], birth_info["month"],
            birth_info["day"], birth_info["hour"],
            bool(birth_info["is_male"])
        )
        ziwei_inst = ZiweiChart(config)
        df = ziwei_inst.gen_chart()
        ziwei_report = get_palace_information(df, _get_domain_df())
        state.horoscope = ziwei_report
        chart_text = transformed_llm_visualize(df.to_markdown())
        state.messages.append(
            AIMessage(content=f"我已經排好你的命盤了: \n{chart_text}\n\n\n 有什麼需要提問的嗎?")
        )
    except Exception:
        logger.exception("generate_ziwei 發生錯誤")
        state.messages.append(
            AIMessage(content="⚠️ 排盤時發生錯誤，請確認出生年月日時辰是否正確，或稍後再試。")
        )
    return state


@sleep_for_tpm
def transformed_llm_visualize(context: str, model: str = GlobalConfig.MODEL) -> str:
    llm = build_llm(model)
    prompt = PromptTemplate.from_template("請排版好並顯示這個 dataframe: {df}")
    chain = prompt | llm
    return chain.invoke({"df": context}).content


@sleep_for_tpm
def split_keywords(context: str, model: str = GlobalConfig.MODEL) -> list:
    llm = build_llm(model)
    keyword_prompt = PromptTemplate.from_template(KEYWORD_PROMPT)
    chain = keyword_prompt | llm
    raw = process_llm_output(chain.invoke({"sentence": context}).content)
    # Fix #7：過濾空字串，避免空 keyword 浪費 RAG embedding 計算
    return [kw.strip() for kw in raw.split(",") if kw.strip()]


@sleep_for_tpm
def analysis_ziwei(context: str, rag_document: str, model: str = GlobalConfig.MODEL) -> str:
    llm = build_llm(model)
    ziwei_prompt = PromptTemplate.from_template(ZIWEI_PROMPT)
    chain = ziwei_prompt | llm
    return process_llm_output(chain.invoke({"sentence": context, "rag_report": rag_document}).content)


def format_row(row) -> str:
    # Fix #4：NaN 值在 bool 判斷時拋 ValueError，改用 pd.notna
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


def get_palace_information(df: pd.DataFrame, ziwei_domain_df: pd.DataFrame) -> str:
    analyses = []
    rag_inst = _get_rag_processor()  # Fix #8：使用 module-level singleton
    for _, row in df.iterrows():
        sentence = format_row(row)
        keyword_list = split_keywords(sentence)
        rag_document = rag_inst.retrieve_definitions(ziwei_domain_df, keyword_list)
        result = analysis_ziwei(sentence, rag_document)
        if result:
            analyses.append(result)
    # Fix #8：各宮解析之間加雙換行，報告結構清晰
    return "\n\n".join(analyses)
