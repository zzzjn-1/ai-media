# -*- coding: utf-8 -*-
"""
M3 · 检索评测（Top-5 命中率）— 落实计划书 6.2「素材检索Top-5命中率≥90%」

用法（项目根目录）:
  python api/eval_retrieval.py                 # 全量（当前 180 条）
  python api/eval_retrieval.py --split test    # 仅测试集
  python api/eval_retrieval.py --k 5
  python api/eval_retrieval.py --no-rerank     # 消融：关闭重排，仅纯向量粗排

说明:
  - 命中定义: 查询 query 经 embedding 后检索 Top-K 个 chunk，若其中任一 chunk
    所属 material_id 落在该任务的 gold_material_ids 中，即记一次命中。
  - 默认走「两阶段检索」：FAISS 粗排 RECALL_TOP_K 条 → 重排取 Top-K（计划书 4.1.3/4.5）。
    --no-rerank 可做消融，量化重排带来的增益。
  - T5.3 起，重排阶段透传任务的 domain/style 提示（与生产 generate 链路同参数），
    评测口径 = 生产口径；style 同时支持从 query 文本自动识别（见 core/rerank.py）。
  - embedding 后端由环境变量 EMBEDDING_BACKEND 控制：
      offline（默认，确定性字符 n-gram 哈希，无需模型权重，可离线跑评测）
      bge    （sentence-transformers BGE 中文向量，语义更强，命中率更高）
  - 该脚本只读数据库与 FAISS 索引，不修改任何数据。
"""
import argparse
import json
import os
import sqlite3
from pathlib import Path

from .config import RECALL_TOP_K
from .core.embeddings import EmbeddingService
from .core.rag_pipeline import retrieve
from .core.vector_store import get_store

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
EVAL_PATH = ROOT / "data" / "eval_set.json"


def material_ids_of_chunks(conn, chunk_ids):
    if not chunk_ids:
        return {}
    ph = ",".join("?" * len(chunk_ids))
    rows = conn.execute(
        f"SELECT id, material_id FROM material_chunks WHERE id IN ({ph})", chunk_ids
    ).fetchall()
    return {r["id"]: r["material_id"] for r in rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="all", choices=["all", "train", "val", "test"])
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--no-rerank", action="store_true",
                    help="消融：关闭重排，仅用纯向量粗排 Top-K")
    args = ap.parse_args()

    emb = EmbeddingService.get()
    store = get_store()
    n_vec = store.ensure_ready(verbose=True)
    # 与生产同源：config.RECALL_TOP_K（T5.3 起评测不再内嵌兜底值，避免口径漂移）
    recall_k = max(RECALL_TOP_K, args.k)
    use_rerank = not args.no_rerank
    print(f"  embedding backend = {emb.backend}  (dim={emb.dim})")
    if not use_rerank:
        mode = f"off (纯向量Top-{args.k})"
    elif emb.backend == "bge":
        mode = f"on (FAISS 粗排{recall_k}→重排{args.k})"
    else:
        # offline 哈希向量不做粗排（见 rag_pipeline.retrieve 注释）
        mode = f"on (offline 全量召回→重排{args.k})"
    print(f"  检索口径 = {mode}")

    eval_set = json.loads(EVAL_PATH.read_text(encoding="utf-8"))
    tasks = eval_set["tasks"]
    if args.split != "all":
        tasks = [t for t in tasks if t["split"] == args.split]

    conn = sqlite3.connect(str(ROOT / "db" / "app.db"))
    conn.row_factory = sqlite3.Row

    # 评测集漂移自检：eval_set.json 里的 gold_material_ids 是快照 id，
    # 库一旦重建（素材 id 从 1 重排）就全部对不上 → 命中率恒为 0%，
    # 看起来像「检索彻底坏了」，实际是评测集过期（2026-10-26 实际踩到）。
    db_mids = {r[0] for r in conn.execute("SELECT id FROM materials")}
    stale = [t["id"] for t in tasks
             if not (set(t["gold_material_ids"]) & db_mids)]
    if stale:
        print(f"\n⚠️  评测集与当前库不一致：{len(stale)}/{len(tasks)} 条任务的 gold 素材在库中不存在"
              f"（例：{stale[:5]}）")
        print("    原因：库被重建后素材 id 变了，而 eval_set.json 仍是旧快照。")
        print("    处理：先跑 data/build_eval_set.py 重建评测集，再评测。")
        print("    否则命中率恒为 0%，会被误判成检索算法故障。\n")

    hits = 0
    total = 0
    miss = []
    by_domain = {}
    for t in tasks:
        q = t["query"]
        if use_rerank:
            # 生产同路径：粗排(RECALL_TOP_K) → 重排 Top-K，透传 domain/style 提示
            top = retrieve(q, domain=t.get("domain"), style=t.get("style"),
                           k=args.k)
            top_ids = [c["chunk_id"] for c in top]
        else:
            vec = emb.embed([q])[0]
            raw = store.search(vec, k=recall_k)
            top_ids = [h[0] for h in raw[:args.k]]
        mid_map = material_ids_of_chunks(conn, top_ids)
        retrieved_mids = set(mid_map.values())
        gold = set(t["gold_material_ids"])
        hit = len(retrieved_mids & gold) > 0
        total += 1
        if hit:
            hits += 1
        else:
            miss.append(t["id"])
        d = t["domain"]
        by_domain.setdefault(d, [0, 0])
        by_domain[d][1] += 1
        if hit:
            by_domain[d][0] += 1

    conn.close()
    rate = hits / total if total else 0.0
    print(f"\n=== Top-{args.k} 命中率（split={args.split}）===")
    print(f"  样本数: {total} | 命中: {hits} | 命中率: {rate*100:.1f}%")
    print("  分领域命中率:")
    for d, (h, tot) in sorted(by_domain.items()):
        print(f"    {d}: {h}/{tot} = {h/tot*100:.1f}%")
    if miss:
        print(f"  未命中任务: {miss}")
    print(f"\n  FAISS 向量总数: {n_vec}")
    print(f"  阈值(计划书 6.2): >=90%  |  实测: {rate*100:.1f}%")
    if emb.backend == "offline":
        print("  注: offline 为字符 n-gram 哈希向量（非语义）；语义口径请以")
        print("      EMBEDDING_BACKEND=bge 重新运行本脚本度量。")
    if use_rerank:
        print(f"  重排策略: RERANK_STRATEGY={os.environ.get('RERANK_STRATEGY', 'lexical')}"
              f"（--no-rerank 可做消融对比）")


if __name__ == "__main__":
    main()
