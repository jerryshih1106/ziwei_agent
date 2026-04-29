import logging
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph
from functools import partial

from .node import chat, get_birth_info, generate_ziwei, check_horoscope, start
from .node.chat_state import ChatState
from .utils.utils import build_llm
from .global_config import GlobalConfig

logger = logging.getLogger(__name__)

class LLMProcessor:
    def __init__(self, model: str = GlobalConfig.MODEL):  # Fix #9：跟隨 GlobalConfig
        self.llm = build_llm(model)

    def set_kernel_pipeline(self):
        """
        lang graph pipeline
        """
        workflow = StateGraph(ChatState)

        # 節點設定
        chat_node = partial(chat, llm=self.llm)
        get_birth_info_node = partial(get_birth_info, llm=self.llm)

        workflow.add_node("start", start)  # Fix #4：partial(start) 無額外參數，直接用 start

        # workflow.add_node("detect_intent", detect_intent_node)
        # workflow.add_node("skip_detect_intent", lambda state: state)  # 空節點，原樣傳回 state
        workflow.add_node("chat", chat_node)
        workflow.add_node("get_birth_info", get_birth_info_node)  # 可以在 node 內部互動直到資料完整
        workflow.add_node("generate_chart", generate_ziwei)

        # 起點
        workflow.set_entry_point("start")

        workflow.add_conditional_edges("start", check_horoscope)

        # 如果生日資訊沒有湊齊, 就會請 user 補充
        workflow.add_conditional_edges(
            "get_birth_info",
            lambda state: "generate_chart" if state.is_fortune else "end",
            {
                "end": "__end__",
                "generate_chart": "generate_chart"
            }
        )

        # 算命流程
        workflow.add_edge("generate_chart", "__end__")
        workflow.add_edge("chat", "__end__")

        return workflow.compile(checkpointer=MemorySaver())

    def set_pipeline(self):
        """
        根據 GlobalConfig.USE_AGENT_SKILL 選擇 pipeline：
        - True  → AgentProcessor (create_react_agent + skill 模式)
        - False → set_kernel_pipeline (固定 LangGraph 流程)

        兩者都回傳可呼叫 .invoke({"messages": [...]}, config=...) 的 compiled graph。
        """
        if GlobalConfig.USE_AGENT_SKILL:
            from .agent_processor import AgentProcessor  # 本地 import 避免循環依賴
            logger.info("pipeline 模式: Agent Skill (create_react_agent)")
            return AgentProcessor(model=GlobalConfig.MODEL).set_pipeline()
        logger.info("pipeline 模式: LangGraph 固定流程")
        return self.set_kernel_pipeline()
