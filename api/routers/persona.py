# -*- coding: utf-8 -*-
"""api/routers/persona.py — 账号人设配置（F6.2，计划书 2.1.6 / 4.3）"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..database import get_conn
from ..deps import get_current_user

router = APIRouter(prefix="/persona", tags=["persona"])


class PersonaReq(BaseModel):
    positioning: str | None = None
    tone: str | None = None
    domains: str | None = None
    display_name: str | None = None


@router.get("")
def get_persona(user=Depends(get_current_user)):
    conn = get_conn()
    row = conn.execute(
        "SELECT display_name,positioning,tone,domains FROM users WHERE id=?",
        (user["id"],),
    ).fetchone()
    conn.close()
    return {"ok": True, "persona": dict(row) if row else {}}


@router.post("")
def update_persona(req: PersonaReq, user=Depends(get_current_user)):
    conn = get_conn()
    updates, params = [], []
    for col, val in [("positioning", req.positioning), ("tone", req.tone),
                     ("domains", req.domains), ("display_name", req.display_name)]:
        if val is not None:
            updates.append(f"{col}=?")
            params.append(val)
    if updates:
        conn.execute(f"UPDATE users SET {','.join(updates)} WHERE id=?",
                     (*params, user["id"]))
        conn.commit()
    row = conn.execute(
        "SELECT display_name,positioning,tone,domains FROM users WHERE id=?",
        (user["id"],),
    ).fetchone()
    conn.close()
    return {"ok": True, "persona": dict(row)}
