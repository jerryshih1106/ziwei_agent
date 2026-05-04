import os


class GlobalConfig:
    MODEL: str = os.environ.get("LLM_MODEL", "gemini-2.5-flash-lite")  # gemini-2, gpt-4o, gpt-3.5
    IS_DEBUG: bool = os.environ.get("IS_DEBUG", "").lower() in ("1", "true", "yes")  # Fix #3：預設 False
    # Bug R4#9 Fix: 改由環境變數控制，原先硬編碼 False 導致此模式完全無法在部署時啟用
    IS_ONLYCHAT: bool = os.environ.get("IS_ONLYCHAT", "").lower() in ("1", "true", "yes")
    MAX_TOKENS: int = 4096
    TPM_TIME: int = int(os.environ.get("TPM_TIME", "3"))  # Fix #9：可透過環境變數調整
    LOCAL_RAG_MODEL_PATH: str = "modern_bert_model"
    RAG_MODEL = None
    RAG_SIMILARITY_THRESHOLD: float = 0.7  # 語意搜尋最低相似度門檻
    # RAG 模式：matching | rag | agent_skill
    #   matching    — keyword 直查 knowledge/*.md（預設，輕量，無需 ML 模型）
    #   rag         — ModernBERT 語意搜尋（需載入大模型）
    #   agent_skill — 先給 LLM 看可用條目清單，LLM 自選後再查詳細內容
    RAG_MODE: str = os.environ.get("RAG_MODE", "matching")
    USE_AGENT_SKILL: bool = False  # True: ReAct agent + skill 模式 / False: 固定 LangGraph 流程

    # Fix #9：這些 os.environ.get() 在 main.py 呼叫 load_dotenv() 後才被求值（Fix #1 確保順序）
    LINE_ACCESS_KEY: str = os.environ.get("LINE_ACCESS_KEY", "")
    LINE_SECRET: str = os.environ.get("LINE_SECRET", "")
    LINE_BOT_ID: str = os.environ.get("LINE_BOT_ID", "@ziwei_ai")
    API_KEY: str = os.environ.get("API_KEY", "")
