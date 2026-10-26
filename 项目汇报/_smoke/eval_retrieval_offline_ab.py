# -*- coding: utf-8 -*-
"""offline 检索 A/B：FAISS 哈希粗排（现状） vs 全量召回（候选方案）。

offline 后端的向量是「字符 2-gram 哈希 + 随机符号」，dim=256、语料 270 块，
碰撞严重 → 粗排 Top-40 基本是半个随机抽样（只覆盖语料 15%），
真正相关的块常常进不了候选池，重排再准也救不回。

指标（每条查询）：
  kw   = 查询的字符二元组中，有多少出现在 Top-5 正文里（关键词覆盖）
  dom  = Top-5 中同领域块占比
  score= 重排打分均值
用法：python 项目汇报/_smoke/eval_retrieval_offline_ab.py
"""
import json
import os
import sqlite3
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, ROOT)

from api.config import RECALL_TOP_K, RETRIEVAL_DOMAIN_FILTER  # noqa: E402
from api.core.embeddings import EmbeddingService  # noqa: E402
from api.core.rerank import rerank  # noqa: E402
from api.core.vector_store import get_store  # noqa: E402

K = 5


def bigrams(text):
    t = "".join(ch for ch in (text or "") if "\u4e00" <= ch <= "\u9fff" or ch.isalnum())
    return {t[i:i + 2] for i in range(len(t) - 1)} or set()


def metrics(q, domain, chunks):
    if not chunks:
        return 0.0, 0.0, 0.0
    qb = bigrams(q)
    hit = set()
    for c in chunks:
        hit |= (qb & bigrams(c.get("content", "")))
    kw = len(hit) / len(qb) if qb else 0.0
    dom = sum(1 for c in chunks if domain and domain in (c.get("domains") or "")) / len(chunks)
    score = sum(c.get("rerank_score", 0.0) for c in chunks) / len(chunks)
    return kw, dom, score


def path_faiss(q, domain):
    store = get_store()
    store.ensure_ready()
    qv = EmbeddingService.get().embed_one(q)
    recall = max(RECALL_TOP_K, K)
    hits = store.search(qv, k=recall, domain=domain if RETRIEVAL_DOMAIN_FILTER else None)
    if not hits and domain:
        hits = store.search(qv, k=recall)
    return rerank(q, store.fetch_chunks([h[0] for h in hits]), top_k=K, domain=domain)


def path_full(q, domain):
    store = get_store()
    store.ensure_ready()
    ids = list(store.chunk_ids)
    doms = getattr(store, "domains", [])
    if RETRIEVAL_DOMAIN_FILTER and domain:
        same = [cid for cid, d in zip(ids, doms) if domain in (d or "")]
        ids = same or ids
    return rerank(q, store.fetch_chunks(ids), top_k=K, domain=domain)


def main():
    conn = sqlite3.connect(os.path.join(ROOT, "db", "app.db"))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT t.title, COALESCE(h.domain,'综合') AS domain FROM topics t "
        "LEFT JOIN hotspots h ON h.id=t.hotspot_id ORDER BY t.id DESC LIMIT 8").fetchall()
    live = [(r["title"], None if r["domain"] == "综合" else r["domain"]) for r in rows]
    es = json.load(open(os.path.join(ROOT, "data", "eval_set.json"), encoding="utf-8"))
    evals = [(t["query"], t["expected_domain"]) for t in es["tasks"][:8]]
    conn.close()

    get_store().ensure_ready()
    print("embedding backend = %s (dim=%d) | 语料 %d 块 | 粗排 %d\n"
          % (EmbeddingService.get().backend, EmbeddingService.get().dim,
             len(get_store().chunk_ids), RECALL_TOP_K))

    for label, cases in (("真实选题标题（线上口径）", live), ("评测集查询（离线口径）", evals)):
        print("=" * 92)
        print(label)
        print("%-42s | %-22s | %-22s" % ("查询", "A 现状(FAISS粗排)", "B 全量召回"))
        print("%-42s | %-6s %-5s %-6s | %-6s %-5s %-6s"
              % ("", "kw", "dom", "score", "kw", "dom", "score"))
        sums = {"a": [0, 0, 0], "b": [0, 0, 0]}
        for q, dom in cases:
            ma = metrics(q, dom, path_faiss(q, dom))
            mb = metrics(q, dom, path_full(q, dom))
            for i in range(3):
                sums["a"][i] += ma[i]
                sums["b"][i] += mb[i]
            print("%-42s | %-6.2f %-5.2f %-6.3f | %-6.2f %-5.2f %-6.3f"
                  % (q[:40], ma[0], ma[1], ma[2], mb[0], mb[1], mb[2]))
        n = max(1, len(cases))
        print("%-42s | %-6.2f %-5.2f %-6.3f | %-6.2f %-5.2f %-6.3f"
              % ("平均", *(sums["a"][i] / n for i in range(3)),
                 *(sums["b"][i] / n for i in range(3))))
        print()


if __name__ == "__main__":
    main()
