# -*- coding: utf-8 -*-
"""offline 哈希 vs BGE 语义：同一批查询下的召回质量实测（只读，不改索引/DB）。

目的：判断「接 BGE」是否值得——本机 sentence_transformers 已装、BAAI/bge-large-zh
权重已在 ~/.cache/huggingface 里，代价是一次 20s+ 加载与 ~1GB 内存。
指标同 eval_retrieval_offline_ab.py：kw（查询二元组被 Top-5 覆盖比例）/ dom / score。
用法：python 项目汇报/_smoke/eval_bge_vs_offline.py
"""
import json
import os
import sqlite3
import sys
import time

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, ROOT)

from api.config import RECALL_TOP_K, RETRIEVAL_DOMAIN_FILTER  # noqa: E402
from api.core.embeddings import EmbeddingService  # noqa: E402
from api.core.rerank import rerank  # noqa: E402
from api.core.vector_store import get_store  # noqa: E402

K = 5
MODEL = os.environ.get("BGE_MODEL", "BAAI/bge-large-zh")


def bigrams(text):
    t = "".join(ch for ch in (text or "") if "\u4e00" <= ch <= "\u9fff" or ch.isalnum())
    return {t[i:i + 2] for i in range(len(t) - 1)} or set()


def metrics(q, domain, chunks):
    if not chunks:
        return 0.0, 0.0, 0.0
    qb, hit = bigrams(q), set()
    for c in chunks:
        hit |= (qb & bigrams(c.get("content", "")))
    return (len(hit) / len(qb) if qb else 0.0,
            sum(1 for c in chunks if domain and domain in (c.get("domains") or "")) / len(chunks),
            sum(c.get("rerank_score", 0.0) for c in chunks) / len(chunks))


def main():
    store = get_store()
    store.ensure_ready()
    chunks = store.fetch_chunks(list(store.chunk_ids))
    print("语料 %d 块" % len(chunks))

    conn = sqlite3.connect(os.path.join(ROOT, "db", "app.db"))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT t.title, COALESCE(h.domain,'综合') AS domain FROM topics t "
        "LEFT JOIN hotspots h ON h.id=t.hotspot_id ORDER BY t.id DESC LIMIT 8").fetchall()
    live = [(r["title"], None if r["domain"] == "综合" else r["domain"]) for r in rows]
    es = json.load(open(os.path.join(ROOT, "data", "eval_set.json"), encoding="utf-8"))
    evals = [(t["query"], t["expected_domain"]) for t in es["tasks"][:8]]
    conn.close()

    # 当前口径（offline 全量召回 → 重排）
    def offline(q, dom):
        ids = list(store.chunk_ids)
        doms = getattr(store, "domains", [])
        if RETRIEVAL_DOMAIN_FILTER and dom:
            same = [i for i, d in zip(ids, doms) if dom in (d or "")]
            ids = same or ids
        m = {c["chunk_id"]: c for c in chunks}
        return rerank(q, [m[i] for i in ids if i in m], top_k=K, domain=dom)

    # BGE 口径（余弦 Top-RECALL_TOP_K → 重排）
    t0 = time.time()
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(MODEL)
    load_s = time.time() - t0
    t0 = time.time()
    doc_vecs = np.asarray(model.encode([c["content"] for c in chunks],
                                       normalize_embeddings=True,
                                       show_progress_bar=False), dtype="float32")
    enc_s = time.time() - t0
    print("BGE 加载 %.1fs | 编码 %d 块 %.1fs | dim=%d"
          % (load_s, len(chunks), enc_s, doc_vecs.shape[1]))

    def bge(q, dom):
        qv = np.asarray(model.encode([q], normalize_embeddings=True,
                                     show_progress_bar=False)[0], dtype="float32")
        sims = doc_vecs @ qv
        order = np.argsort(-sims)
        picked = []
        for i in order:
            if dom and RETRIEVAL_DOMAIN_FILTER and dom not in (chunks[i].get("domains") or ""):
                continue
            picked.append(chunks[i])
            if len(picked) >= max(RECALL_TOP_K, K):
                break
        return rerank(q, picked, top_k=K, domain=dom)

    for label, cases in (("真实选题标题", live), ("评测集查询", evals)):
        print("\n" + "=" * 88)
        print(label)
        print("%-38s | %-18s | %-18s" % ("查询", "offline 全量召回", "BGE 粗排40"))
        print("%-38s | %-5s %-5s %-6s | %-5s %-5s %-6s"
              % ("", "kw", "dom", "score", "kw", "dom", "score"))
        sa, sb = [0, 0, 0], [0, 0, 0]
        for q, dom in cases:
            ma, mb = metrics(q, dom, offline(q, dom)), metrics(q, dom, bge(q, dom))
            for i in range(3):
                sa[i] += ma[i]
                sb[i] += mb[i]
            print("%-38s | %-5.2f %-5.2f %-6.3f | %-5.2f %-5.2f %-6.3f"
                  % (q[:36], *ma, *mb))
        n = max(1, len(cases))
        print("%-38s | %-5.2f %-5.2f %-6.3f | %-5.2f %-5.2f %-6.3f"
              % ("平均", *(v / n for v in sa), *(v / n for v in sb)))


if __name__ == "__main__":
    main()
