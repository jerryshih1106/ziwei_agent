# from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph
import tiktoken
from functools import partial
from .node import chat, get_birth_info, generate_ziwei, check_horoscope, start
from .node.chat_state import ChatState
from .utils.utils import build_llm

MODEL_MAP_DICT = {"gpt-3.5":"gpt-3.5-turbo", "gpt-4o": "gpt-4o"}

class LLMProcessor:
    def __init__(self, model: str = "gpt-3.5"):
        self.llm = build_llm(model)
        self.chat_memory = []
        self.max_token = 4096
        self.is_debug = True

    @staticmethod
    def get_token_count(text_list, model="gpt-3.5-turbo") -> int:
        enc = tiktoken.encoding_for_model(model)
        return sum(len(enc.encode(item.content)) for item in text_list)

    def set_kernel_pipeline(self):
        """
        lang graph pipeline
        """
        workflow = StateGraph(ChatState)

        # 節點設定
        # detect_intent_node = partial(detect_intent, llm=self.llm)
        start_node = partial(start)
        chat_node = partial(chat, llm=self.llm)
        get_birth_info_node = partial(get_birth_info, llm=self.llm)

        workflow.add_node("start", start_node)

        # workflow.add_node("detect_intent", detect_intent_node)
        # workflow.add_node("skip_detect_intent", lambda state: state)  # 空節點，原樣傳回 state
        workflow.add_node("chat", chat_node)
        workflow.add_node("get_birth_info", get_birth_info_node)  # 可以在 node 內部互動直到資料完整
        workflow.add_node("generate_chart", generate_ziwei)

        # 起點
        workflow.set_entry_point("start")

        workflow.add_conditional_edges("start", check_horoscope)

        # # 如果已經有命盤, 不需要再 detect 是不是需要算命了
        # workflow.add_conditional_edges(
        #     "chat",
        #     should_detect_intent,
        #     {
        #         "detect_intent": "detect_intent",
        #         "end": "__end__"
        #     }
        # )
        # workflow.add_edge("detect_intent", "get_birth_info")
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
