# -*- coding: utf-8 -*-
"""api/deps.py — FastAPI 依赖（JWT 当前用户解析，落实 F6 鉴权）"""
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .core.auth import decode_access_token
from .database import get_conn

_bearer = HTTPBearer(auto_error=False)


def get_current_user(creds: HTTPAuthorizationCredentials = Depends(_bearer)) -> dict:
    if not creds or not creds.credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未提供认证令牌")
    payload = decode_access_token(creds.credentials)
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="令牌无效或已过期")
    conn = get_conn()
    row = conn.execute(
        "SELECT id, username, status FROM users WHERE id=?", (int(payload["sub"]),)
    ).fetchone()
    conn.close()
    if not row or row["status"] != 1:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或已停用")
    return {"id": row["id"], "username": row["username"]}


def get_current_user_optional(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
) -> dict | None:
    """读接口的可选鉴权：带 token 就识别用户（用于个性化排序），不带头像也照常返回。

    热点看板必须公开可读（api/_demo.py 与 api/_e2e_test.py 都无 token 调用），
    但 feed 视图需要知道"这个账号关注哪些领域"，所以这里不能强制 401。
    """
    if not creds or not creds.credentials:
        return None
    try:
        return get_current_user(creds)
    except HTTPException:
        return None
