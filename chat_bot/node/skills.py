import os
import logging
import pandas as pd
from langchain_core.tools import tool

from ziweidoushu.kernel import ZiweiChart
from ziweidoushu.base import ZiWeiConfig

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@tool
def generate_ziwei_chart(
    year: int,
    month: int,
    day: int,
    hour: int,
    is_male: bool,
) -> str:
    """根據出生年月日時辰和性別，生成紫微斗數命盤並回傳完整解盤報告。

    呼叫此工具前，請先向使用者確認以下所有資訊都已提供：
    - year:    西元出生年份，例如 1995
    - month:   出生月份 1~12
    - day:     出生日期 1~31
    - hour:    出生時辰對應小時數（子=0, 丑=2, 寅=4, 卯=6, 辰=8, 巳=10,
               午=12, 未=14, 申=16, 酉=18, 戌=20, 亥=22）；
               若使用者給 24 小時制整數，直接使用該數字
    - is_male: 性別，男生為 True，女生為 False

    若任何欄位尚未確認，請先詢問使用者補全，不要呼叫此工具。

    Returns:
        str: 命盤 Markdown 表格 + 逐宮解析報告
    """
    # 延遲 import 避免循環依賴
    from .gen_ziwei import get_palace_information, transformed_llm_visualize

    logger.debug(
        "generate_ziwei_chart called: year=%s month=%s day=%s hour=%s is_male=%s",
        year, month, day, hour, is_male,
    )

    config = ZiWeiConfig(year, month, day, hour, bool(is_male))
    ziwei_inst = ZiweiChart(config)
    df = ziwei_inst.gen_chart()

    csv_path = os.path.join(_PROJECT_ROOT, "basic_document", "star_palace_meaning.csv")
    ziwei_domain_df = pd.read_csv(csv_path)

    horoscope_report = get_palace_information(df, ziwei_domain_df)
    chart_md = transformed_llm_visualize(df.to_markdown())

    return f"【命盤】\n{chart_md}\n\n【逐宮解析】\n{horoscope_report}"
