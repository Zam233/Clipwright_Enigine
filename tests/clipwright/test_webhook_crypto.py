"""P8/P2-7: Webhook secret 加密落盘测试。"""

from __future__ import annotations

from clipwright.services.webhook_crypto import (
    decrypt_secret,
    encrypt_secret,
)
from clipwright.config import settings


def test_encrypt_decrypt_roundtrip(monkeypatch) -> None:
    """配置 Fernet key 时：secret 加密落盘、可解密还原、不是明文。"""
    import base64
    from cryptography.fernet import Fernet
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "webhook_secret_key", key)
    # 重置模块级 Fernet 缓存
    import clipwright.services.webhook_crypto as wc
    wc._FERNET = None

    stored = encrypt_secret("whsec_my_secret_123")
    assert stored != "whsec_my_secret_123"  # 已加密
    assert "whsec_my_secret_123" not in stored
    assert decrypt_secret(stored) == "whsec_my_secret_123"


def test_plaintext_fallback_without_key(monkeypatch) -> None:
    """未配置密钥且无 jwt secret → 明文兼容（本地开发）。"""
    monkeypatch.setattr(settings, "webhook_secret_key", "")
    monkeypatch.setattr(settings, "account_jwt_secret", "")
    import clipwright.services.webhook_crypto as wc
    wc._FERNET = None
    assert encrypt_secret("whsec_x") == "whsec_x"
    assert decrypt_secret("whsec_x") == "whsec_x"


def test_empty_secret_noop() -> None:
    assert encrypt_secret("") == ""
    assert decrypt_secret("") == ""
