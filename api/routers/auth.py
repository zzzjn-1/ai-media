# -*- coding: utf-8 -*-
"""api/routers/auth.py — 用户注册 / 登录（F6，计划书 2.1.6 / 4.3）"""
from fastapi import APIRouter
from pydantic import BaseModel

from ..core.auth import create_access_token
from ..database import get_conn, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterReq(BaseModel):
    username: str
    password: str
    email: str | None = None


class LoginReq(BaseModel):
    username: str
    password: str


@router.post("/register")
def register(req: RegisterReq):
    conn = get_conn()
    exists = conn.execute(
        "SELECT id FROM users WHERE username=?", (req.username,)
    ).fetchone()
    if exists:
        conn.close()
        return {"ok": False, "msg": "用户名已存在"}
    cur = conn.execute(
        "INSERT INTO users(username, password_hash, email, status) VALUES(?,?,?,1)",
        (req.username, hash_password(req.password), req.email),
    )
    uid = cur.lastrowid
    conn.commit()
    conn.close()
    token = create_access_token(uid, req.username)
    return {"ok": True, "access_token": token, "token_type": "bearer", "user_id": uid}


@router.post("/login")
def login(req: LoginReq):
    conn = get_conn()
    row = conn.execute(
        "SELECT id, username, password_hash, status FROM users WHERE username=?",
        (req.username,),
    ).fetchone()
    conn.close()
    if not row or row["status"] != 1 or not verify_password(req.password, row["password_hash"]):
        return {"ok": False, "msg": "用户名或密码错误"}
    token = create_access_token(row["id"], row["username"])
    return {"ok": True, "access_token": token, "token_type": "bearer", "user_id": row["id"]}
