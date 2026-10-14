# -*- coding: utf-8 -*-
"""
db/security.py — 字段级加密与口令哈希（落实计划书 2.2.4 / 4.2）

字段加密：Fernet（AES-128-CBC + HMAC-SHA256），密钥来自环境变量 DB_FIELD_KEY
           （Fernet 密钥，base64 urlsafe 编码的 32 字节）。
           - 设置后：所有敏感字段以密文落库，密钥不入库（计划书 2.2.4 / 4.2）。
           - 未设置：生成一次性内存密钥并告警，仅限离线演示；重启后历史密文无法解密。
口令哈希：pbkdf2_hmac('sha256', 260000 轮)，格式
           $pbkdf2$<iter>$<salt_b64>$<hash_b64>，绝不明文/不可逆。
           对齐 schema.sql 注释中的「bcrypt/argon2 强哈希」安全基线。

依赖：标准库 + cryptography（已安装于隔离 venv）。
"""
import os
import base64
import hashlib
import hmac
import secrets
import warnings
from pathlib import Path

from cryptography.fernet import Fernet

FIELD_KEY_ENV = "DB_FIELD_KEY"
FIELD_PREFIX = "enc:"          # 加密字段标记，便于区分明文/密文并兼容历史数据
PBKDF2_ITER = 260_000

_fernet = None


def _resolve_field_key() -> str | None:
    """解析字段密钥：优先环境变量 DB_FIELD_KEY；否则项目根 .env 中的 DB_FIELD_KEY（离线演示用）。"""
    raw = os.environ.get(FIELD_KEY_ENV)
    if raw:
        return raw.strip()
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(FIELD_KEY_ENV + "="):
                val = line.split("=", 1)[1].strip().strip('"').strip("'")
                if val:
                    return val
    return None


def _get_fernet() -> Fernet:
    """惰性获取 Fernet 实例；优先环境变量/项目根 .env 密钥，否则一次性内存密钥（演示）。"""
    global _fernet
    if _fernet is not None:
        return _fernet
    raw = _resolve_field_key()
    if raw:
        try:
            Fernet(raw.encode("utf-8"))   # 校验合法 Fernet 密钥
        except Exception as e:
            raise RuntimeError(f"{FIELD_KEY_ENV} 不是合法 Fernet 密钥：{e}")
        _fernet = Fernet(raw.encode("utf-8"))
        return _fernet
    # 离线演示且无 .env：一次性密钥，重启即失效（仅限临时验证）
    _fernet = Fernet(Fernet.generate_key())
    warnings.warn(
        "⚠️ 未设置环境变量 DB_FIELD_KEY 且项目根无 .env，使用一次性内存密钥；"
        "重启后已落库密文将无法解密。生产环境请设置 DB_FIELD_KEY 且密钥不入库。",
        stacklevel=2,
    )
    return _fernet


def encrypt_field(plain: str | None) -> str | None:
    """对敏感字段加密落库；None 透传。"""
    if plain is None:
        return None
    token = _get_fernet().encrypt(plain.encode("utf-8")).decode("utf-8")
    return FIELD_PREFIX + token


def decrypt_field(value: str | None) -> str | None:
    """读取时解密；非密文（无 enc: 前缀）原样返回，兼容未加密数据。"""
    if value is None:
        return None
    if not value.startswith(FIELD_PREFIX):
        return value
    token = value[len(FIELD_PREFIX):]
    return _get_fernet().decrypt(token.encode("utf-8")).decode("utf-8")


def hash_password(password: str, *, iterations: int = PBKDF2_ITER) -> str:
    """生成 pbkdf2_sha256 加盐哈希，格式 $pbkdf2$iter$salt_b64$hash_b64。"""
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "$pbkdf2$%d$%s$%s" % (
        iterations,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(dk).decode("ascii"),
    )


def verify_password(password: str, stored: str | None) -> bool:
    """校验口令；格式不符或异常一律返回 False。"""
    if not stored or not stored.startswith("$pbkdf2$"):
        return False
    try:
        parts = stored[1:].split("$")
        if len(parts) != 4:
            return False
        _, it_s, salt_b64, hash_b64 = parts
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, int(it_s)
        )
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


if __name__ == "__main__":
    # 快速自检（以下口令为示意占位值，非真实凭证）
    h = hash_password("Example@2026")
    assert verify_password("Example@2026", h)
    assert not verify_password("wrong", h)
    ct = encrypt_field("示例敏感正文")
    assert ct.startswith(FIELD_PREFIX)
    assert decrypt_field(ct) == "示例敏感正文"
    print("security module self-check OK")
