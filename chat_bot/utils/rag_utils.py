import logging
import pandas as pd
from sentence_transformers import SentenceTransformer, util
from ..global_config import GlobalConfig

logger = logging.getLogger(__name__)

# Fix #2：module-level cache，key 為 DataFrame 的 tuple(columns)+len，避免 id() 每次不同導致 miss
_domain_embeddings: dict = {}   # key: (tuple(columns), nrows), value: tensor


class RagProcessor:
    """
    專門處理 RAG 的 processor
    """
    def __init__(self, model_name: str = 'joe32140/ModernBERT-large-msmarco'):
        self.model_name = model_name

    @staticmethod
    def _df_cache_key(domain_df: pd.DataFrame) -> tuple:
        """Fix #2：穩定 cache key，不依賴 id()。"""
        return (tuple(domain_df.columns), len(domain_df))

    def _get_embeddings(self, domain_df: pd.DataFrame):
        key = self._df_cache_key(domain_df)
        if key not in _domain_embeddings:
            if not GlobalConfig.RAG_MODEL:
                self.gen_model()
            _domain_embeddings[key] = GlobalConfig.RAG_MODEL.encode(
                domain_df['object'].tolist(), convert_to_tensor=True
            )
        return _domain_embeddings[key]

    def gen_model(self):
        if not GlobalConfig.RAG_MODEL:
            try:
                GlobalConfig.RAG_MODEL = SentenceTransformer(GlobalConfig.LOCAL_RAG_MODEL_PATH)
                logger.info("載入本地 RAG 模型成功: %s", GlobalConfig.LOCAL_RAG_MODEL_PATH)
            except Exception:
                logger.warning("本地 RAG 模型載入失敗，改用遠端模型: %s", self.model_name, exc_info=True)
                GlobalConfig.RAG_MODEL = SentenceTransformer(self.model_name)

    def retrieve_definitions(self, domain_df: pd.DataFrame, keywords: list, top_k: int = 1) -> str:
        """
        根據輸入的關鍵詞列表，回傳每個詞對應的最佳定義。
        Fix #2：無命中時回傳空字串而非空 markdown 表格。
        """
        if not GlobalConfig.RAG_MODEL:
            self.gen_model()

        object_embeddings = self._get_embeddings(domain_df)

        results = {"object": [], "definition": []}
        for keyword in keywords:
            keyword_embedding = GlobalConfig.RAG_MODEL.encode(keyword, convert_to_tensor=True)
            hits = util.semantic_search(keyword_embedding, object_embeddings, top_k=top_k)[0]
            for hit in hits:
                if hit['score'] < GlobalConfig.RAG_SIMILARITY_THRESHOLD:
                    continue
                results["object"].append(domain_df.iloc[hit['corpus_id']]['object'])
                results["definition"].append(domain_df.iloc[hit['corpus_id']]['definition'])

        # Fix #2：無結果時回傳空字串，避免把空 markdown 表格傳給 LLM
        if not results["object"]:
            return ""
        return pd.DataFrame(results).to_markdown()
