# -*- coding: utf-8 -*-
"""api/routers/materials.py — 素材导入/打标/向量化/检索（F1，计划书 2.1.1 / 4.3）"""
import re

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..database import decrypt_field, encrypt_field, get_conn
from ..deps import get_current_user
from ..core.vector_store import get_store

router = APIRouter(prefix="/materials", tags=["materials"])

MAX_CHUNK_CHARS = 300
_SENT_SPLIT = re.compile(r"(?<=[。！？!?；;])")


def split_chunks(text: str):
    """段落语义分块（对齐 M2 chunk_spec.md：≤300 字、句边界再切、过碎合并）。"""
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks = []
    for para in paras:
        if len(para) <= MAX_CHUNK_CHARS:
            chunks.append(para)
            continue
        buf = ""
        for sent in _SENT_SPLIT.split(para):
            sent = sent.strip()
            if not sent:
                continue
            if buf and len(buf) + len(sent) > MAX_CHUNK_CHARS:
                chunks.append(buf.strip())
                buf = ""
            buf = (buf + sent).strip()
        if buf:
            chunks.append(buf.strip())
    merged = []
    for c in chunks:
        if merged and len(c) < 15 and len(merged[-1]) + len(c) <= MAX_CHUNK_CHARS:
            merged[-1] = merged[-1] + " " + c
        else:
            merged.append(c)
    return merged


class ImportReq(BaseModel):
    title: str
    content: str
    source_type: str = "text"
    platforms: str = ""
    domains: str = ""
    styles: str = ""
    custom_tags: str = ""


class TagReq(BaseModel):
    platforms: str | None = None
    domains: str | None = None
    styles: str | None = None
    custom_tags: str | None = None


@router.post("/import")
def import_material(req: ImportReq, user=Depends(get_current_user)):
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO materials(user_id,title,source_type,content,platforms,domains,styles,custom_tags,char_count)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        (user["id"], req.title, req.source_type,
         encrypt_field(req.content), req.platforms, req.domains, req.styles,
         req.custom_tags, len(req.content)),
    )
    mid = cur.lastrowid
    chunks = split_chunks(req.content)
    for i, ck in enumerate(chunks):
        conn.execute(
            "INSERT INTO material_chunks(material_id,chunk_index,content,char_count) VALUES(?,?,?,?)",
            (mid, i, encrypt_field(ck), len(ck)),
        )
    conn.execute("UPDATE materials SET chunk_count=? WHERE id=?", (len(chunks), mid))
    conn.commit()
    conn.close()
    return {"ok": True, "material_id": mid, "chunk_count": len(chunks)}


@router.post("/{material_id}/tag")
def tag_material(material_id: int, req: TagReq, user=Depends(get_current_user)):
    conn = get_conn()
    row = conn.execute("SELECT id FROM materials WHERE id=? AND user_id=?",
                       (material_id, user["id"])).fetchone()
    if not row:
        conn.close()
        return {"ok": False, "msg": "素材不存在或无权限"}
    updates, params = [], []
    for col, val in [("platforms", req.platforms), ("domains", req.domains),
                     ("styles", req.styles), ("custom_tags", req.custom_tags)]:
        if val is not None:
            updates.append(f"{col}=?")
            params.append(val)
    if updates:
        conn.execute(f"UPDATE materials SET {','.join(updates)} WHERE id=?",
                     (*params, material_id))
        conn.commit()
    conn.close()
    return {"ok": True, "material_id": material_id}


@router.post("/{material_id}/embed")
def embed_material(material_id: int, user=Depends(get_current_user)):
    conn = get_conn()
    row = conn.execute("SELECT id FROM materials WHERE id=? AND user_id=?",
                       (material_id, user["id"])).fetchone()
    conn.close()
    if not row:
        return {"ok": False, "msg": "素材不存在或无权限"}
    # FAISS 主存储：重建全量索引（保证一致性；离线 162 块重建毫秒级）
    n = get_store().build_from_db(verbose=False)
    return {"ok": True, "rebuilt_vectors": n}


@router.get("/{material_id}/chunks")
def list_chunks(material_id: int, user=Depends(get_current_user)):
    """分块预览（只读）：返回素材解密后的分块文本与字数（M4 素材库「分块预览」需要）。"""
    conn = get_conn()
    row = conn.execute("SELECT id FROM materials WHERE id=? AND user_id=?", (material_id, user["id"])).fetchone()
    if not row:
        conn.close()
        return {"ok": False, "msg": "素材不存在或无权限"}
    rows = conn.execute(
        "SELECT chunk_index, content, char_count FROM material_chunks "
        "WHERE material_id=? ORDER BY chunk_index", (material_id,)
    ).fetchall()
    conn.close()
    return {
        "ok": True,
        "material_id": material_id,
        "chunks": [
            {"index": r["chunk_index"], "content": decrypt_field(r["content"]), "char_count": r["char_count"]}
            for r in rows
        ],
    }


@router.get("")
def list_or_search(q: str | None = None, limit: int = 50, user=Depends(get_current_user)):
    conn = get_conn()
    if q:
        # 语义检索：与生成链路同一条两阶段 retrieve()（粗排 RECALL_TOP_K → 重排 Top-K），
        # 口径一致（T5.3 修复：此前此处绕过重排直接 FAISS k=5，与 M3 文档宣称不符）。
        from ..core.rag_pipeline import retrieve
        chunks = retrieve(q, k=min(max(limit, 5), 20))
        mats = {}
        for c in chunks:
            mid = c["material_id"]
            if mid in mats:
                continue
            row = conn.execute(
                "SELECT id,title,domains,platforms,styles,chunk_count,created_at "
                "FROM materials "
                "WHERE id=? AND user_id=?",
                (mid, user["id"]),
            ).fetchone()
            if row:
                mats[mid] = dict(row)
        # 关键词检索（LIKE，对齐计划书 F1.5）。content 为加密字段无法 LIKE，
        # 故仅匹配标题；命中结果追加在语义检索之后，不覆盖其排序。
        like_rows = conn.execute(
            "SELECT id,title,domains,platforms,styles,chunk_count,created_at "
            "FROM materials "
            "WHERE user_id=? AND title LIKE ? LIMIT ?",
            (user["id"], f"%{q}%", limit),
        ).fetchall()
        for r in like_rows:
            mats.setdefault(r["id"], dict(r))
        conn.close()
        return {"ok": True, "query": q, "results": list(mats.values()), "mode": "search"}
    rows = conn.execute(
        "SELECT id,title,domains,platforms,styles,chunk_count,created_at "
        "FROM materials WHERE user_id=? ORDER BY id DESC LIMIT ?",
        (user["id"], limit),
    ).fetchall()
    conn.close()
    return {"ok": True, "results": [dict(r) for r in rows], "mode": "list"}
