"""Webhook secret 加密（P8/P2-7）— 落盘不存明文，投递时解密。

密钥优先级：CLIPWRIGHT_WEBHOOK_SECRET_KEY（Fernet urlsafe base64, 32 字节）
> CLIPWRIGHT_ACCOUNT_JWT_SECRET 派生（sha256 → urlsafe b64）。
既无密钥也无 jwt secret 时退化为明文兼容（本地开发，日志告警）。
"""

from __future__ import annotations

import base64
import hashlib

from clipwright.config import logger, settings

_FERNET = None


def _get_fernet():
    global _FERNET
    if _FERNET is not None:
        return _FERNET
    from cryptography.fernet import Fernet, InvalidToken

    raw = (settings.webhook_secret_key or "").strip()
    if not raw and settings.account_jwt_secret:
        digest = hashlib.sha256(settings.account_jwt_secret.encode("utf-8")).digest()
        raw = base64.urlsafe_b64encode(digest).decode("ascii")
    if not raw:
        logger.warning("webhook_secret_key 未配置，secret 将以明文落盘（建议设置 CLIPWRIGHT_WEBHOOK_SECRET_KEY）")
        return None
    try:
        _FERNET = Fernet(raw.encode("ascii"))
    except Exception as e:
        logger.warning("webhook Fernet 初始化失败，回退明文: %s", e)
        return None
    return _FERNET


def encrypt_secret(secret: str) -> str:
    """加密 webhook secret；未配置密钥时返回明文（兼容模式）。"""
    if not secret:
        return ""
    f = _get_fernet()
    if f is None:
        return secret
    try:
        return f.encrypt(secret.encode("utf-8")).decode("ascii")
    except Exception:
        return secret


def decrypt_secret(stored: str) -> str:
    """解密 webhook secret；明文/损坏时原样返回（兼容旧数据）。"""
    if not stored:
        return ""
    f = _get_fernet()
    if f is None:
        return stored
    try:
        return f.decrypt(stored.encode("ascii")).decode("utf-8")
    except Exception:
        return stored


def is_encrypted(stored: str) -> bool:
    return bool(stored) and not stored.startswith(("whsec_", "sk_")) and "==" in stored
