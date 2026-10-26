# -*- coding: utf-8 -*-
"""
api/core/embeddings.py — 可插拔文本向量化（落实 T3.1 BGE 向量化）

两种后端：
  - "bge"   ：sentence_transformers 加载 BAAI/bge-large-zh（经 HF_ENDPOINT 镜像）
  - "offline"：确定性字符 n-gram 哈希向量（零下载），保证无网络/无权重时
               FAISS 检索与 Top-5 命中率评测仍可端到端跑通。

设计：EmbeddingService 单例，按 config.EMBEDDING_BACKEND 惰性初始化，dim 对外暴露。
"""
import hashlib
import math
import re

from ..config import EMBEDDING_BACKEND, EMBEDDING_DIM, EMBEDDING_MODEL, HF_ENDPOINT

_NGRAM = 2  # 二元字符 n-gram


def _char_ngrams(text: str, n: int = _NGRAM):
    text = re.sub(r"\s+", "", text or "")
    if len(text) < n:
        text = text + " " * (n - len(text))
    return [text[i:i + n] for i in range(len(text) - n + 1)] or [text]


class _OfflineEmbedding:
    """确定性哈希向量：对字符 n-gram 做哈希散列并累加，L2 归一化。"""

    def __init__(self, dim: int):
        self.dim = dim

    def encode(self, texts):
        out = []
        for t in texts:
            vec = [0.0] * self.dim
            grams = _char_ngrams(t)
            for g in grams:
                h = int(hashlib.md5(g.encode("utf-8")).hexdigest(), 16)
                idx = h % self.dim
                sign = 1.0 if (h >> 8) & 1 else -1.0
                vec[idx] += sign
            norm = math.sqrt(sum(v * v for v in vec))
            if norm > 0:
                vec = [v / norm for v in vec]
            out.append(vec)
        return out


class _BgeEmbedding:
    """真实 BGE 向量（sentence_transformers）。首次调用惰性加载权重。"""

    def __init__(self, model_name: str):
        if HF_ENDPOINT:
            import os
            os.environ.setdefault("HF_ENDPOINT", HF_ENDPOINT)
        from sentence_transformers import SentenceTransformer  # 惰性导入
        self._model = SentenceTransformer(model_name)
        self.dim = int(self._model.get_sentence_embedding_dimension())

    def encode(self, texts):
        vecs = self._model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return [list(map(float, v)) for v in vecs]


class EmbeddingService:
    _instance = None

    def __init__(self):
        self.backend = EMBEDDING_BACKEND
        self._impl = None
        if EMBEDDING_BACKEND == "bge":
            try:
                self._impl = _BgeEmbedding(EMBEDDING_MODEL)
            except Exception as e:  # noqa: BLE001
                # 权重未下载 / 出网被代理挡住时不许整个服务起不来：
                # 回落到 offline 哈希向量，并把真实后端如实报给 /api/health。
                # 已缓存权重时加 HF_HUB_OFFLINE=1 可跳过联网校验（实测不加会被代理打成 502）。
                print(f"[向量化] BGE 加载失败（{type(e).__name__}: {e}）→ 回落 offline 哈希向量")
        if self._impl is None:
            self._impl = _OfflineEmbedding(EMBEDDING_DIM)
            self.backend = "offline"
        self.dim = self._impl.dim

    @classmethod
    def get(cls) -> "EmbeddingService":
        if cls._instance is None:
            cls._instance = EmbeddingService()
        return cls._instance

    def embed(self, texts):
        return self._impl.encode(texts)

    def embed_one(self, text):
        return self.embed([text])[0]


def embed_texts(texts):
    return EmbeddingService.get().embed(texts)


def embed_one(text):
    return EmbeddingService.get().embed_one(text)
