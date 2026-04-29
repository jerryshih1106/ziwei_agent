import logging
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver

from .utils.utils import build_llm
from .node.skills import generate_ziwei_chart

logger = logging.getLogger(__name__)

AGENT_SYSTEM_PROMPT = """\
你是一位紫微斗數算命大師，擁有豐富的紫微斗數知識。

你有一個工具 `generate_ziwei_chart` 可以幫使用者排盤並解盤。

## 呼叫工具的規則
在呼叫工具之前，你必須先向使用者確認以下所有資訊：
1. 西元出生年份（例如：1995）
2. 出生月份（1~12）
3. 出生日期（1~31）
4. 出生時辰（地支如：子、丑、寅… 或 24 小時制的整數）
5. 性別（男 / 女）

如果有任何一項缺失，請一次性詢問使用者補充所有缺少的資訊，語氣友善自然。
例如：「請問你的出生年月日和時辰是什麼呢？還有你的性別？」

## 命盤完成後
拿到命盤結果後，請：
1. 展示命盤的 Markdown 表格
2. 呈現逐宮解析
3. 主動詢問使用者是否有想深入了解的面向（感情、事業、財運等）

## 後續聊天
命盤生成後，請結合命盤內容回答問題，並給出有根據的紫微斗數建議。
例如：「根據命盤，XX 星落入夫妻宮，代表 XX，因此你 XXX…」
"""


class AgentProcessor:
    """
    使用 create_react_agent 建立 skill-based agent pipeline。
    對外介面（.set_pipeline()）與 LLMProcessor.set_kernel_pipeline() 相同：
    回傳一個可呼叫 .invoke({"messages": [...]}, config=...) 的 compiled graph。
    """

    def __init__(self, model: str = "gemini-2"):
        self.llm = build_llm(model)
        # MemorySaver 在 __init__ 建立，跨 request 持續存活（LINE Bot 需要）
        self.memory = MemorySaver()

    def set_pipeline(self):
        """回傳 compiled ReAct agent graph。"""
        logger.info("AgentProcessor: 建立 skill-based agent pipeline")
        return create_react_agent(
            model=self.llm,
            tools=[generate_ziwei_chart],
            prompt=AGENT_SYSTEM_PROMPT,
            checkpointer=self.memory,
        )
