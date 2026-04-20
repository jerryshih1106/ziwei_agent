import os

class GlobalConfig:
    MODEL: str = "gemini-2"  # gemini-2, gpt-4o, gpt-3.5
    IS_DEBUG: bool = True
    IS_ONLYCHAT: bool = False
    MAX_TOKENS: int = 4096
    TPM_TIME: int = 3  # 每次 LLM 呼叫前的等待秒數，用於避免超過 TPM 限制
    LOCAL_RAG_MODEL_PATH: str = "modern_bert_model"
    RAG_MODEL = None
    RAG_SIMILARITY_THRESHOLD: float = 0.7  # 語意搜尋最低相似度門檻
    LINE_ACCESS_KEY: str = os.environ.get("LINE_ACCESS_KEY", "")
    LINE_SECRET: str = os.environ.get("LINE_SECRET", "")
    USE_AGENT_SKILL: bool = False  # True: ReAct agent + skill 模式 / False: 固定 LangGraph 流程
