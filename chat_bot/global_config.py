class GlobalConfig:
    MODEL:str = "gemini-2" # gemini-2, gpt-4o, gpt-3.5
    IS_DEBUG:bool = True
    IS_ONLYCHAT:bool = False
    MAX_TOKENS:int = 4096
    TPM_TIME:int = 3
    LOCAL_RAG_MODEL_PATH = "modern_bert_model"
    RAG_MODEL = None
    LINE_ACCESS_KEY:str = 'pSs41TgQNbSZRggB5Kaq0srhy0SFsSCwddQfiy19k2ELOSRld57XcoeTa2Hj4VNiirIMQXHYaEYNyLsbdbIgDnctN5tvn/NLbkkw3neo+cUvzmYe3vpQdaUffIOurLtxeTJGcm47Vo94CBHIQx1xIwdB04t89/1O/w1cDnyilFU='
    LINE_SECRET:str = '56d4833afa488f9c715deb788de129a0'
