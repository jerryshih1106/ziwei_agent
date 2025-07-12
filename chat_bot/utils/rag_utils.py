
import pandas as pd
from sentence_transformers import SentenceTransformer, util
from ..global_config import GlobalConfig

class RagProcessor:
    """
    專門處理 RAG 的 processor
    """
    def __init__(self, model_name:str = 'joe32140/ModernBERT-large-msmarco'):
        # self.model = None
        self.object_embeddings = []
        self.model_name = model_name

    def _gen_embedding(self, domain_df):
        """
        生成 rag 的 embedding
        """
        self.object_embeddings = GlobalConfig.RAG_MODEL.encode(domain_df['object'].tolist(), convert_to_tensor=True)

    def gen_model(self):
        """
        生成 model
        """
        # if not self.model:
        if not GlobalConfig.RAG_MODEL:
            try:
                GlobalConfig.RAG_MODEL = SentenceTransformer(GlobalConfig.LOCAL_RAG_MODEL_PATH)
                print("load local rag model success")
            except Exception:
                GlobalConfig.RAG_MODEL = SentenceTransformer(self.model_name)

    def retrieve_definitions(self, domain_df:pd.DataFrame, keywords:list, top_k:int=1) -> str:
        """
        根據輸入的關鍵詞列表，回傳每個詞對應的最佳定義
        """
        if not GlobalConfig.RAG_MODEL:
            self.gen_model()
        if len(self.object_embeddings) == 0:
            self._gen_embedding(domain_df)
        results = {"object":[], "definition":[]}
        for keyword in keywords:
            keyword_embedding = GlobalConfig.RAG_MODEL.encode(keyword, convert_to_tensor=True)
            hits = util.semantic_search(keyword_embedding, self.object_embeddings, top_k=top_k)[0]
            for hit in hits:
                if hit['score'] < 0.7:
                    continue
                results["object"].append(domain_df.iloc[hit['corpus_id']]['object'])
                results["definition"].append(domain_df.iloc[hit['corpus_id']]['definition'])
        return pd.DataFrame(results).to_markdown()
