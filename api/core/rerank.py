# -*- coding: utf-8 -*-
"""
api/core/rerank.py — 检索重排（落实计划书 4.1.3「检索器召回 → 重排」/ 4.5「可选重排提升 Top-5 命中率」）

为什么需要重排：
  FAISS 相似检索（双塔 bi-encoder）只编码「查询」和「文档」各自的语义，句级相似度
  粗放；cross-encoder 把 query+doc 拼在一起联合打分，精度高但算力昂贵。因此标准
  两阶段检索是「粗排召回 K' 条 → 重排取 Top-K」。本模块实现该重排阶段。

三种策略（按 RERANK_STRATEGY 选择，均可离线跑通）：
  - "lexical"（默认）：字符/词级重合度 + 领域先验加权。零外部依赖，确定性强，
    在中文短文本上对同领域素材的区分度优于纯向量内积，适合离线与评审复现。
  - "cross"：sentence-transformers CrossEncoder（BAAI/bge-reranker-large），
    精度最高，需下载权重（与 EMBEDDING_BACKEND=bge 同一套 HF 缓存）。
  - "off"：不重排，直接取粗排结果（用于消融对比，验证重排是否真的带来增益）。
"""
import math
import re
from collections import Counter

from ..config import (
    RERANK_CROSS_MODEL,
    RERANK_STRATEGY,
    RETRIEVAL_DOMAIN_FILTER,
)

_CJK = re.compile(r"[一-鿿]")
_TOKEN = re.compile(r"[一-鿿]|[A-Za-z0-9]+")

# 风格词表（与前端 DOMAINS 同级约定：素材/示例风格标签 = 专业/轻松/犀利）
_STYLES = ("专业", "轻松", "犀利")

_cross_model = None


def _tokens(text: str) -> list:
    """中文按字、英文数字按词切分（无外部分词依赖，够用且确定性强）。"""
    return _TOKEN.findall(text or "")


def _detect_style(text: str) -> str | None:
    """从查询/请求中识别风格词（「以「犀利」口吻」这类表述直接命中）。"""
    for s in _STYLES:
        if s in (text or ""):
            return s
    return None


def _lexical_score(query: str, doc: str, domain_hit: bool, style_hit: bool) -> float:
    """字符/词级重合度 + 领域/风格先验的确定性打分。"""
    q = _tokens(query)
    d = _tokens(doc)
    if not q or not d:
        return 0.0
    qc, dc = Counter(q), Counter(d)
    common = sum(min(c, dc.get(t, 0)) for t, c in qc.items())
    # 以查询侧覆盖率为主体（避免长文档天然占优），叠加 Jaccard 稳定性
    coverage = common / len(q)
    union = len(set(q) | set(d))
    jaccard = common / union if union else 0.0
    score = 0.7 * coverage + 0.3 * jaccard
    if domain_hit:
        score += 0.15  # 同领域先验加权（4.1.3「同领域范例」）
    if style_hit:
        score += 0.12  # 同风格先验加权（T5.3：修复「领域对、风格错」未命中）
    return score


def _get_cross_model():
    """惰性加载 cross-encoder；失败则回落 lexical，保证链路不中断。"""
    global _cross_model
    if _cross_model is None:
        from sentence_transformers import CrossEncoder  # 惰性导入
        _cross_model = CrossEncoder(RERANK_CROSS_MODEL)
    return _cross_model


def rerank(query: str, chunks: list, top_k: int = 5,
           domain: str | None = None, style: str | None = None) -> list:
    """对粗排结果重排，返回 top_k 条（chunk dict 列表，附 rerank_score）。

    chunks 需含 'content'/'styles' 字段（来自 VectorStore.fetch_chunks）。
    domain/style 为调用方显式提示（生成请求/检索请求的过滤条件）；
    style 缺省时从 query 文本自动识别（如「以「犀利」口吻」）。
    失败或策略关闭时原样返回前 top_k 条，不影响主链路可用性。
    """
    if not chunks:
        return []
    strategy = (RERANK_STRATEGY or "lexical").lower()

    if strategy == "off":
        return chunks[:top_k]

    style = style or _detect_style(query)

    if strategy == "cross":
        try:
            model = _get_cross_model()
            pairs = [(query, c.get("content", "")) for c in chunks]
            scores = model.predict(pairs)
            for c, s in zip(chunks, scores):
                c["rerank_score"] = float(s)
            return sorted(chunks, key=lambda c: -c["rerank_score"])[:top_k]
        except Exception:  # noqa: BLE001 —— 权重缺失/加载失败则降级 lexical
            strategy = "lexical"

    for c in chunks:
        dom_hit = bool(
            RETRIEVAL_DOMAIN_FILTER and domain and domain in (c.get("domains") or "")
        )
        style_hit = bool(style and style in (c.get("styles") or ""))
        c["rerank_score"] = round(
            _lexical_score(query, c.get("content", ""), dom_hit, style_hit), 6
        )
    return sorted(chunks, key=lambda c: -c["rerank_score"])[:top_k]
