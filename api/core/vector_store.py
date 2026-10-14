# -*- coding: utf-8 -*-
"""
api/core/vector_store.py — FAISS 向量主存储（落实 T3.1 / 计划书 3.1 / 4.5）

设计要点：
  - 向量主存储由 FAISS 承载（sqlite 仅存文本与元数据），二者经 chunk_id 对齐。
  - 索引与 chunk_id 映射持久化到 db/faiss.index + db/faiss_meta.json。
  - 离线（无 BGE）也可构建与检索，支撑 Top-5 命中率评测。
"""
import json
import os

import faiss
import numpy as np

from ..config import FAISS_INDEX_PATH, FAISS_META_PATH
from ..database import decrypt_field, get_conn
from .embeddings import EmbeddingService


class VectorStore:
    def __init__(self):
        self.index = None
        self.chunk_ids = []
        self.domains = []
        self.meta_sig = None
        self.dim = EmbeddingService.get().dim
        self.backend = EmbeddingService.get().backend

    # ---------------- 构建 / 持久化 ----------------
    def build_from_db(self, verbose: bool = False):
        emb = EmbeddingService.get()
        conn = get_conn()
        rows = conn.execute(
            "SELECT c.id, c.content, c.material_id, m.domains "
            "FROM material_chunks c JOIN materials m ON m.id=c.material_id "
            "ORDER BY c.id"
        ).fetchall()
        conn.close()
        if not rows:
            raise RuntimeError("material_chunks 为空，请先运行 data/clean_chunk.py 入库素材")
        texts, ids, domains = [], [], []
        for r in rows:
            texts.append(decrypt_field(r["content"]) or "")
            ids.append(r["id"])
            domains.append(r["domains"] or "")
        vecs = np.asarray(emb.embed(texts), dtype="float32")
        index = faiss.IndexFlatL2(self.dim)
        index.add(vecs)
        self.index = index
        self.chunk_ids = ids
        self.domains = domains
        self._save()
        if verbose:
            print(f"  [FAISS] 构建完成：{index.ntotal} 向量，dim={self.dim}，backend={self.backend}")
        return index.ntotal

    def _save(self):
        # 用 serialize/deserialize 二进制写入，规避 FAISS 原生文件 IO 在含非 ASCII
        # 路径（如中文目录名）的 Windows 上 fopen 失败的问题
        data = faiss.serialize_index(self.index).tobytes()
        with open(FAISS_INDEX_PATH, "wb") as f:
            f.write(data)
        conn = get_conn()
        row = conn.execute(
            "SELECT COUNT(*), COALESCE(MAX(id), 0) FROM material_chunks"
        ).fetchone()
        conn.close()
        meta = {
            "dim": self.dim,
            "backend": self.backend,
            "chunk_ids": self.chunk_ids,
            "domains": getattr(self, "domains", []),
            # 索引签名：与 DB 现状比对，素材重入库后陈旧索引必须重建，
            # 否则 chunk_id 错位会导致检索命中率异常（T5.3 bad case 修复）
            "chunk_count": int(row[0]),
            "max_chunk_id": int(row[1]),
        }
        with open(FAISS_META_PATH, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)

    def load(self):
        if not (os.path.exists(FAISS_INDEX_PATH) and os.path.exists(FAISS_META_PATH)):
            return False
        with open(FAISS_META_PATH, "r", encoding="utf-8") as f:
            meta = json.load(f)
        # 维度或后端变化则需重建
        if meta.get("dim") != self.dim or meta.get("backend") != self.backend:
            return False
        with open(FAISS_INDEX_PATH, "rb") as f:
            data = f.read()
        self.index = faiss.deserialize_index(np.frombuffer(data, dtype=np.uint8))
        self.chunk_ids = meta.get("chunk_ids", [])
        self.domains = meta.get("domains", [])
        self.meta_sig = {
            "chunk_count": meta.get("chunk_count"),
            "max_chunk_id": meta.get("max_chunk_id"),
        }
        return True

    def _is_fresh(self) -> bool:
        """索引签名是否与当前 DB 一致（缺签名的旧索引一律判陈旧，触发重建）。"""
        sig = getattr(self, "meta_sig", None)
        if not sig or sig.get("chunk_count") is None:
            return False
        try:
            conn = get_conn()
            row = conn.execute(
                "SELECT COUNT(*), COALESCE(MAX(id), 0) FROM material_chunks"
            ).fetchone()
            conn.close()
            return sig == {"chunk_count": int(row[0]), "max_chunk_id": int(row[1])}
        except Exception:  # noqa: BLE001
            return False

    def ensure_ready(self, verbose: bool = False) -> int:
        """保证索引就绪：已加载且与 DB 一致则复用，否则重建。返回向量数。"""
        if self.load() and self._is_fresh():
            return self.index.ntotal
        if verbose:
            print("  [FAISS] 索引缺失或与 DB 不一致（素材可能已重入库），重建中...")
        return self.build_from_db(verbose=verbose)

    # ---------------- 检索 ----------------
    def search(self, query_vector, k: int = 5, domain: str | None = None):
        """语义检索 Top-K；可选按领域过滤后补足。返回 [(chunk_id, distance)]。"""
        if self.index is None:
            self.ensure_ready()
        q = np.asarray([query_vector], dtype="float32")
        n = min(k * 4, self.index.ntotal)
        dists, idxs = self.index.search(q, n)
        results = []
        for d, i in zip(dists[0], idxs[0]):
            if i < 0:
                continue
            cid = self.chunk_ids[i]
            dom = self.domains[i] if i < len(self.domains) else ""
            if domain and domain not in (dom or ""):
                continue
            results.append((cid, float(d)))
            if len(results) >= k:
                break
        return results

    # ---------------- 上下文回填 ----------------
    def fetch_chunks(self, chunk_ids):
        if not chunk_ids:
            return []
        conn = get_conn()
        placeholders = ",".join("?" * len(chunk_ids))
        rows = conn.execute(
            f"SELECT c.id, c.content, c.material_id, m.domains, m.styles, m.platforms "
            f"FROM material_chunks c JOIN materials m ON m.id=c.material_id "
            f"WHERE c.id IN ({placeholders})",
            chunk_ids,
        ).fetchall()
        conn.close()
        by_id = {r["id"]: r for r in rows}
        out = []
        for cid in chunk_ids:
            r = by_id.get(cid)
            if not r:
                continue
            out.append({
                "chunk_id": r["id"],
                "content": decrypt_field(r["content"]) or "",
                "material_id": r["material_id"],
                "domains": r["domains"] or "",
                "styles": r["styles"] or "",
                "platforms": r["platforms"] or "",
            })
        return out


# 模块级单例（供 router 直接调用）
_store = VectorStore()


def get_store() -> VectorStore:
    return _store
