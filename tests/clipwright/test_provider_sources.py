"""B2 测试：ProviderSource 存储 + 角色绑定 + 模型清单 + API 契约。"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from clipwright.api import providers as providers_api
from clipwright.services import provider_sources as ps


@pytest.fixture(autouse=True)
def _isolate(tmp_path):
    ps.reset_for_tests(tmp_path / "provider_sources.json")
    yield
    ps.reset_for_tests(None)


class TestSourceCrud:
    def test_create_masks_keys_and_persists_encrypted(self):
        src = ps.create_source({
            "name": "Deepseek", "kind": "llm", "provider": "openai",
            "base_url": "https://api.deepseek.com",
            "api_keys": ["sk-abcdef123456"],
            "models": ["deepseek-chat"],
        })
        assert src["has_keys"] and src["api_keys_masked"] == ["sk-a****"]
        assert "api_keys" not in src  # 明文绝不外泄
        raw = ps.get_source(src["id"], public=False)
        from clipwright.services.webhook_crypto import has_key
        if has_key():
            # 配了密钥 → 密文落盘，但展示/解析必须还原为明文（不得回显密文）
            assert raw["api_keys"][0] != "sk-abcdef123456"
            assert ps._plain_keys(raw) == ["sk-abcdef123456"]
        else:
            # 未配置 CLIPWRIGHT_WEBHOOK_SECRET_KEY 时明文兼容（与插件 secret 同语义）
            assert raw["api_keys"][0] == "sk-abcdef123456"

    def test_ciphertext_never_leaks_as_key(self, monkeypatch):
        """B3-fix：加密令牌形态不匹配旧 is_encrypted 启发式时也不得把密文当密钥。"""
        monkeypatch.setattr(ps, "has_key", lambda: True)
        # 故意用不含 "==" 且不以 sk_/whsec_ 开头的假密文（复现旧启发式误判）
        monkeypatch.setattr(ps, "encrypt_secret", lambda s: f"ENC[{s}]")
        monkeypatch.setattr(ps, "decrypt_secret",
                            lambda s: s[4:-1] if s.startswith("ENC[") else s)
        src = ps.create_source({"name": "aliyun", "api_keys": ["sk-aliyun-secret-1"]})
        assert src["api_keys_masked"] == ["sk-a****"]
        bound = ps.create_source({
            "name": "b", "provider": "openai", "models": ["qwen-max"],
            "base_url": "https://x.example.com", "api_keys": ["sk-bound-secret"],
        })
        ps.set_role("main", bound["id"], "qwen-max")
        resolved = ps.resolve("main")
        assert resolved["api_key"] == "sk-bound-secret"
        assert ps.resolve_for_source(bound["id"])["api_key"] == "sk-bound-secret"

    def test_masked_key_keeps_old_value(self):
        src = ps.create_source({"name": "S", "api_keys": ["sk-original-xxxx"]})
        ps.update_source(src["id"], {"name": "S2", "api_keys": ["sk-o****"]})
        raw = ps.get_source(src["id"], public=False)
        from clipwright.services.webhook_crypto import decrypt_secret
        assert decrypt_secret(raw["api_keys"][0]) == "sk-original-xxxx"
        assert ps.get_source(src["id"])["name"] == "S2"

    def test_new_plaintext_key_replaces(self):
        src = ps.create_source({"name": "S", "api_keys": ["old-key-1111"]})
        ps.update_source(src["id"], {"api_keys": ["new-key-2222"]})
        assert ps.resolve_for_source(src["id"])["api_key"] == "new-key-2222"

    def test_invalid_kind_rejected(self):
        with pytest.raises(ValueError, match="未知的源类型"):
            ps.create_source({"name": "X", "kind": "video"})

    def test_duplicate_id_rejected(self):
        ps.create_source({"id": "dup", "name": "A"})
        with pytest.raises(ValueError, match="已存在"):
            ps.create_source({"id": "dup", "name": "B"})

    def test_delete_unbinds_roles(self):
        src = ps.create_source({"id": "s1", "name": "S", "models": ["m1"]})
        ps.set_role("main", "s1", "m1")
        out = ps.delete_source("s1")
        assert out["unbound_roles"] == ["main"]
        assert ps.get_roles() == {}


class TestModels:
    def test_upsert_and_dedupe(self):
        src = ps.create_source({"id": "s", "name": "S", "models": ["m1", "m1", {"id": "m2", "enabled": False}]})
        assert [m["id"] for m in ps.get_source("s")["models"]] == ["m1", "m2"]
        ps.upsert_model("s", {"id": "m3", "name": "自定义"})
        ids = [m["id"] for m in ps.get_source("s")["models"]]
        assert ids == ["m1", "m2", "m3"]

    def test_delete_model_unbinds_role(self):
        ps.create_source({"id": "s", "name": "S", "models": ["m1", "m2"]})
        ps.set_role("main", "s", "m1")
        ps.delete_model("s", "m1")
        assert ps.get_roles() == {}


class TestRoles:
    def test_bind_requires_model_in_list(self):
        ps.create_source({"id": "s", "name": "S", "models": ["m1"]})
        with pytest.raises(ValueError, match="不在源的清单中"):
            ps.set_role("main", "s", "m-unknown")

    def test_disabled_model_cannot_bind(self):
        ps.create_source({"id": "s", "name": "S", "models": [{"id": "m1", "enabled": False}]})
        with pytest.raises(ValueError, match="已停用"):
            ps.set_role("main", "s", "m1")

    def test_unknown_role_rejected(self):
        with pytest.raises(ValueError, match="未知角色"):
            ps.set_role("banana", None)

    def test_resolve_returns_plaintext_credentials(self):
        ps.create_source({
            "id": "s", "name": "D", "provider": "openai",
            "base_url": "https://api.deepseek.com", "api_keys": ["sk-live-9999"],
            "timeout_sec": 30, "proxy": "http://127.0.0.1:7890",
            "headers": {"X-Trace": "1"}, "models": ["deepseek-chat"],
        })
        ps.set_role("main", "s", "deepseek-chat")
        got = ps.resolve("main")
        assert got["api_key"] == "sk-live-9999" and got["model"] == "deepseek-chat"
        assert got["timeout_sec"] == 30 and got["proxy"].endswith("7890")
        assert got["headers"] == {"X-Trace": "1"}

    def test_resolve_without_binding_returns_none(self):
        assert ps.resolve("main") is None
        assert ps.resolve("nonexistent-role") is None

    def test_role_falls_back_to_first_enabled_model(self):
        ps.create_source({"id": "s", "name": "S",
                          "models": [{"id": "off", "enabled": False}, "on"]})
        ps.set_role("main", "s", "")  # 不指定模型 → 取第一个启用模型
        assert ps.resolve("main")["model"] == "on"


class TestMigration:
    def test_migrate_from_flat_settings(self, monkeypatch):
        from clipwright.config import settings

        monkeypatch.setattr(settings, "llm_provider", "openai")
        monkeypatch.setattr(settings, "llm_api_key", "sk-legacy-777")
        monkeypatch.setattr(settings, "llm_base_url", "https://api.deepseek.com")
        monkeypatch.setattr(settings, "llm_model", "deepseek-chat")
        sid = ps.ensure_migrated()
        assert sid == "default"
        assert ps.resolve("main")["api_key"] == "sk-legacy-777"
        assert ps.resolve("vision")["model"] == "deepseek-chat"
        # 幂等：已有源不再迁移
        assert ps.ensure_migrated() is None

    def test_migrate_skips_when_unconfigured(self, monkeypatch):
        from clipwright.config import settings

        monkeypatch.setattr(settings, "llm_api_key", "")
        monkeypatch.setattr(settings, "llm_base_url", "")
        assert ps.ensure_migrated() is None


class TestApi:
    @pytest.fixture
    def client(self):
        app = FastAPI()
        app.include_router(providers_api.router)
        return TestClient(app)

    def test_crud_roundtrip_and_masking(self, client):
        r = client.post("/api/providers", json={
            "name": "aliyun", "kind": "llm", "provider": "openai",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_keys": ["sk-aliyun-secret-1"], "models": ["qwen-max"],
        })
        assert r.status_code == 200, r.text
        sid = r.json()["id"]
        assert r.json()["api_keys_masked"] == ["sk-a****"]

        lst = client.get("/api/providers").json()
        assert [s["id"] for s in lst["sources"]] == [sid]

        # 绑定角色
        r2 = client.put("/api/providers/roles", json={"role": "main", "source_id": sid, "model": "qwen-max"})
        assert r2.status_code == 200 and r2.json()["bound"] is True
        assert client.get("/api/providers/roles").json()["roles"]["main"]["model"] == "qwen-max"

        # 模型清单：自定义 + 删除
        r3 = client.post(f"/api/providers/{sid}/models/upsert", json={"id": "qwen-plus"})
        assert [m["id"] for m in r3.json()["models"]] == ["qwen-max", "qwen-plus"]
        r4 = client.delete(f"/api/providers/{sid}/models/qwen-plus")
        assert [m["id"] for m in r4.json()["models"]] == ["qwen-max"]

        # 删除源 → 解绑
        r5 = client.delete(f"/api/providers/{sid}")
        assert r5.json()["unbound_roles"] == ["main"]
        assert client.get("/api/providers").json()["sources"] == []

    def test_bad_payloads(self, client):
        assert client.post("/api/providers", json={"name": ""}).status_code == 400
        assert client.post("/api/providers", json={"name": "x", "kind": "video"}).status_code == 400
        assert client.get("/api/providers/nope").status_code == 404
        assert client.put("/api/providers/roles", json={"role": "banana"}).status_code == 400
