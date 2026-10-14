# -*- coding: utf-8 -*-
"""api/routers/hotspots.py — 热点看板（F1.4，计划书 2.1.1 / 4.3）"""
from fastapi import APIRouter

from ..database import get_conn

router = APIRouter(prefix="/hotspots", tags=["hotspots"])


@router.get("")
def list_hotspots(domain: str | None = None, limit: int = 50):
    conn = get_conn()
    if domain:
        rows = conn.execute(
            "SELECT id,title,summary,source,url,domain,heat,trend,collected_at "
            "FROM hotspots WHERE is_active=1 AND domain=? ORDER BY heat DESC LIMIT ?",
            (domain, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id,title,summary,source,url,domain,heat,trend,collected_at "
            "FROM hotspots WHERE is_active=1 ORDER BY heat DESC LIMIT ?",
            (limit,),
        ).fetchall()
    conn.close()
    return {"ok": True, "count": len(rows), "hotspots": [dict(r) for r in rows]}
