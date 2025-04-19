
import pandas as pd
from sentence_transformers import SentenceTransformer, util

class Rag_processor:
    """
    專門處理 RAG 的 processor
    """
    def __init__(self, domain_df:pd.DataFrame, model:str = 'joe32140/ModernBERT-large-msmarco'):
        self.model = SentenceTransformer(model)
        self.domain_df = domain_df
        self.object_embeddings = []

    def gen_embedding(self):
        """
        生成 rag 的 embedding
        """
        self.object_embeddings = self.model.encode(self.domain_df['object'].tolist(), convert_to_tensor=True)

    def retrieve_definitions(self, keywords:list, top_k:int=1) -> str:
        """
        根據輸入的關鍵詞列表，回傳每個詞對應的最佳定義
        """
        if len(self.object_embeddings) == 0:
            return "RAG failed, need to generate embedding first"
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
