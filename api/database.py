# -*- coding: utf-8 -*-
"""
api/database.py — SQLite 连接与字段解密助手（复用 M1 db/security.py）

materials.content / material_chunks.content 在 M2 已加密落库（enc: 前缀），
凡读取敏感明文均经 security.decrypt_field 解密，落实计划书 2.2.4 / 4.2。
"""
import sqlite3
import sys
from pathlib import Path

from .config import DB_PATH

# 复用项目根 db/ 下的安全模块（字段级加密 + 口令哈希）
_DB_DIR = str(Path(DB_PATH).resolve().parent)
if _DB_DIR not in sys.path:
    sys.path.insert(0, _DB_DIR)
import security  # noqa: E402


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def decrypt_field(value):
    """解密落库敏感字段；非密文原样返回。"""
    return security.decrypt_field(value)


def hash_password(password: str) -> str:
    return security.hash_password(password)


def verify_password(password: str, stored: str) -> bool:
    return security.verify_password(password, stored)


def encrypt_field(value):
    """加密敏感字段落库（materials/content、material_chunks/content）。"""
    return security.encrypt_field(value)
