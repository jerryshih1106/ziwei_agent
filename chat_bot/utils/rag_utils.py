
import pandas as pd
from sentence_transformers import SentenceTransformer, util
from ..global_config import GlobalConfig

class RagProcessor:
    """
    專門處理 RAG 的 processor
    """
    def __init__(self, domain_df:pd.DataFrame, model_name:str = 'joe32140/ModernBERT-large-msmarco'):
        self.model = None
        self.domain_df = domain_df
        self.object_embeddings = []
        self.model_name = model_name

    def gen_embedding(self):
        """
        生成 rag 的 embedding
        """
        if not self.model:
            print("you need to load the model first")
            return
        self.object_embeddings = self.model.encode(self.domain_df['object'].tolist(), convert_to_tensor=True)

    def retrieve_definitions(self, keywords:list, top_k:int=1) -> str:
        """
        根據輸入的關鍵詞列表，回傳每個詞對應的最佳定義
        """
        if len(self.object_embeddings) == 0:
            return "RAG failed, need to generate embedding first"
        if not self.model:
            try:
                self.model = SentenceTransformer(GlobalConfig.LOCAL_RAG_MODEL_PATH)
                print("load local rag model success")
            except Exception:
                self.model = SentenceTransformer(self.model_name)
        self.gen_embedding()
        results = {"object":[], "definition":[]}
        for keyword in keywords:
            keyword_embedding = self.model.encode(keyword, convert_to_tensor=True)
            hits = util.semantic_search(keyword_embedding, self.object_embeddings, top_k=top_k)[0]
            for hit in hits:
                if hit['score'] < 0.7:
                    continue
                results["object"].append(self.domain_df.iloc[hit['corpus_id']]['object'])
                results["definition"].append(self.domain_df.iloc[hit['corpus_id']]['definition'])
        return pd.DataFrame(results).to_markdown()
