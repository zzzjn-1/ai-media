# -*- coding: utf-8 -*-
"""api/routers/topics.py — 选题策划与一键选用（F2，计划书 2.1.2 / 4.3）"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..database import get_conn
from ..deps import get_current_user
from ..core.rag_pipeline import generate_topics

router = APIRouter(prefix="/topics", tags=["topics"])


class GenerateReq(BaseModel):
    domain: str | None = None
    count: int = 3


@router.post("/generate")
def generate(req: GenerateReq, user=Depends(get_current_user)):
    conn = get_conn()
    persona = conn.execute(
        "SELECT positioning, tone, domains FROM users WHERE id=?", (user["id"],)
    ).fetchone()
    hs_rows = conn.execute(
        "SELECT id,title,domain,heat FROM hotspots WHERE is_active=1 "
        + ("AND domain=? " if req.domain else "")
        + "ORDER BY heat DESC LIMIT 15",
        ((req.domain,) if req.domain else ()),
    ).fetchall()
    hotspots = [dict(r) for r in hs_rows]
    conn.close()

    persona_d = dict(persona) if persona else {}
    topics = generate_topics(hotspots, persona_d, req.domain, req.count)

    conn = get_conn()
    created = []
    for t in topics:
        cur = conn.execute(
            "INSERT INTO topics(user_id,hotspot_id,title,angle,reason,target_audience,status) "
            "VALUES(?,?,?,?,?,?,0)",
            (user["id"], None, t.get("title", ""), t.get("angle", ""),
             t.get("reason", ""), t.get("target_audience", "")),
        )
        created.append({"id": cur.lastrowid, **t})
    conn.commit()
    conn.close()
    return {"ok": True, "topics": created}


@router.post("/{topic_id}/select")
def select_topic(topic_id: int, user=Depends(get_current_user)):
    conn = get_conn()
    row = conn.execute("SELECT id,title,angle,reason,target_audience FROM topics "
                       "WHERE id=? AND user_id=?", (topic_id, user["id"])).fetchone()
    if not row:
        conn.close()
        return {"ok": False, "msg": "选题不存在或无权限"}
    conn.execute("UPDATE topics SET status=1 WHERE id=?", (topic_id,))
    conn.commit()
    conn.close()
    return {"ok": True, "topic": dict(row)}
