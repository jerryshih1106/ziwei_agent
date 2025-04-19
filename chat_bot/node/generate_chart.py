import pandas as pd
from langchain.prompts import PromptTemplate
from .chat_state import ChatState
from ziweidoushu.kernel import ZiweiChart
from ziweidoushu.base import ZiWeiConfig
from ..utils.utils import build_llm, process_llm_output
from ..utils.rag_utils import Rag_processor
from ..prompt import KEYWORD_PROMPT, ZIWEI_PROMPT, REPORT_PROMPT

MODEL = "gpt-3.5"

def generate_ziwei(state:ChatState):
    birth_info = state.birth_info
    config = ZiWeiConfig(birth_info["year"], birth_info["month"], birth_info["day"], birth_info["hour"])
    ziwei_inst = ZiweiChart(config)
    df = ziwei_inst.gen_chart()
    ziwei_domain_df = pd.read_csv(("basic_document/star_palace_meaning.csv"))
    ziwei_report = get_palace_information(df, ziwei_domain_df)
    state.horoscope = ziwei_report
    state.messages.append({"content": "我已經排好你的命盤了: \n" + transformed_llm_visualize(df.to_markdown()) + "\n\n\n 有什麼需要提問的嗎?"})
    return state #state.copy(update={"horoscope": ziwei_report})


def transformed_llm_visualize(context:str, model:str = "gpt-3.5") -> list:
    llm = build_llm(model)
    prompt = PromptTemplate.from_template("請排版好並顯示這個 dataframe: {df}")
    chain = prompt | llm
    return chain.invoke({"df": context}).content

def split_keywords(context:str, model:str = MODEL) -> list:
    llm = build_llm(model)
    keyword_prompt = PromptTemplate.from_template(KEYWORD_PROMPT)
    chain = keyword_prompt | llm
    return process_llm_output(chain.invoke({"sentence": context}).content).split(",")

def analysis_ziwei(context:str, rag_document:str, model:str = MODEL) -> str:
    llm = build_llm(model)
    ziwei_prompt = PromptTemplate.from_template(ZIWEI_PROMPT)
    chain = ziwei_prompt | llm
    return process_llm_output(chain.invoke({"sentence": context, "rag_report": rag_document}).content)

def build_ziwei_report(ziwei_summary:str, model:str = MODEL) -> str:
    llm = build_llm(model)
    report_prompt = PromptTemplate.from_template(REPORT_PROMPT)
    chain = report_prompt | llm
    return chain.invoke({"ziwei_summary": ziwei_summary}).content

def format_row(row):
    # 處理星
    if row["星"]:
        stars = [f"{star.strip()}星" for star in row["星"].split(",")]
        star_str = ", ".join(stars)
        # 組成句子
        sentence = f"{star_str}入{row['宮']}位於{row['天干']}{row['地支']}"
    else:
        sentence = f"沒有星落入{row['宮']}位於{row['天干']}{row['地支']}"
    # 加入化
    if row["化"]:
        sentence += f"，化{row['化']}"
    else:
        sentence += "，沒有化星"
    # 加入位置
    return sentence

def get_palace_information(df:pd.DataFrame, ziwei_domain_df:pd.DataFrame):
    result_string = ""
    for _, row in df.iterrows():
        sentence = format_row(row)
        keyword_list = split_keywords(sentence)
        rag_inst = Rag_processor(ziwei_domain_df)
        rag_inst.gen_embedding()
        rag_document = rag_inst.retrieve_definitions(keyword_list)
        result_string += analysis_ziwei(sentence, rag_document)
    return result_string
