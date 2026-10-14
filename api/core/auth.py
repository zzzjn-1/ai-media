# -*- coding: utf-8 -*-
"""
api/core/auth.py — JWT 签发与校验（落实 F6 用户认证，计划书 2.1.6 / 4.3）
"""
import datetime
import jwt

from ..config import JWT_ALGORITHM, JWT_EXPIRE_HOURS, JWT_SECRET


def create_access_token(user_id: int, username: str) -> str:
    now = datetime.datetime.utcnow()
    payload = {
        "sub": str(user_id),
        "username": username,
        "iat": now,
        "exp": now + datetime.timedelta(hours=JWT_EXPIRE_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str):
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None
