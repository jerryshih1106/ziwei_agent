import os
import json
import logging
import threading
import pandas as pd
from langchain_core.tools import tool

from ziweidoushu.kernel import ZiweiChart
from ziweidoushu.base import ZiWeiConfig

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_CACHE_PATH = os.path.join(_PROJECT_ROOT, "basic_document", "horoscope_cache.json")
_DOMAIN_CSV = os.path.join(_PROJECT_ROOT, "basic_document", "star_palace_meaning.csv")
_cache_lock = threading.Lock()
_domain_df_cache = None  # Fix #4：CSV module-level 快取

# Fix: 限制快取最大筆數，防止 Render ephemeral disk 被耗盡
_CACHE_MAX_ENTRIES = 500


def _load_cache() -> dict:
    if os.path.exists(_CACHE_PATH):
        try:
            with open(_CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    # Fix: 超過上限時移除最舊的項目（dict 在 Python 3.7+ 保持插入順序）
    if len(cache) > _CACHE_MAX_ENTRIES:
        excess = len(cache) - _CACHE_MAX_ENTRIES
        for old_key in list(cache.keys())[:excess]:
            del cache[old_key]
        logger.info("generate_ziwei_chart: cache evicted %d old entries", excess)
    try:
        with open(_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except OSError:
        # Non-fatal: cache write failure (disk full / read-only FS on Render ephemeral).
        # Log and continue — the tool result is already computed and will be returned.
        logger.warning("generate_ziwei_chart: failed to write cache to %s", _CACHE_PATH, exc_info=True)


def _cache_key(year: int, month: int, day: int, hour: int, is_male: bool) -> str:
    return f"{year}-{month:02d}-{day:02d}-{hour:02d}-{'M' if is_male else 'F'}"


@tool
def generate_ziwei_chart(
    year: int,
    month: int,
    day: int,
    hour: int,
    is_male: bool,
    is_lunar: bool = False,
) -> str:
    """根據出生年月日時辰和性別，生成紫微斗數命盤並回傳完整解盤報告。

    呼叫此工具前，請先向使用者確認以下所有資訊都已提供：
    - year:     西元出生年份，例如 1995
    - month:    出生月份 1~12
    - day:      出生日期 1~31
    - hour:     出生時辰對應小時數（子=0, 丑=2, 寅=4, 卯=6, 辰=8, 巳=10,
                午=12, 未=14, 申=16, 酉=18, 戌=20, 亥=22）；
                若使用者給 24 小時制整數，直接使用該數字
    - is_male:  性別，男生為 True，女生為 False
    - is_lunar: 生日是否為農曆（陰曆），預設 False（國曆）

    若任何欄位尚未確認，請先詢問使用者補全，不要呼叫此工具。

    Returns:
        str: 命盤 Markdown 表格 + 逐宮解析報告（若已有快取則直接回傳，不重新計算）
    """
    key = _cache_key(year, month, day, hour, bool(is_male))

    with _cache_lock:
        cache = _load_cache()
        if key in cache:
            logger.info("generate_ziwei_chart: cache hit for key=%s", key)
            return cache[key]

    logger.info("generate_ziwei_chart: cache miss, computing for key=%s", key)

    # 延遲 import 避免循環依賴
    from .gen_ziwei import get_palace_information, transformed_llm_visualize

    config = ZiWeiConfig(year, month, day, hour, bool(is_male), is_lunar=bool(is_lunar))
    ziwei_inst = ZiweiChart(config)
    df = ziwei_inst.gen_chart()

    global _domain_df_cache
    if _domain_df_cache is None:
        _domain_df_cache = pd.read_csv(_DOMAIN_CSV)
    ziwei_domain_df = _domain_df_cache

    horoscope_report = get_palace_information(df, ziwei_domain_df)
    chart_md = transformed_llm_visualize(df.to_markdown())

    result = f"【命盤】\n{chart_md}\n\n【逐宮解析】\n{horoscope_report}"

    with _cache_lock:
        cache = _load_cache()  # re-read to avoid overwriting concurrent writes
        cache[key] = result
        _save_cache(cache)
    logger.info("generate_ziwei_chart: saved to cache, key=%s", key)

    return result
