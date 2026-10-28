"""运行时配置 API（/api/settings）单测。

覆盖：元数据覆盖度、GET 掩码（secret 不泄漏明文）、PUT 校验与持久化、
清除覆盖还原、secret 无密钥时拒绝（fail-closed）、失效钩子触发、
管理员守卫。store 落盘指向 tmp_path，settings 全字段测试后还原。

注意：测试中的「密钥」均为无真实格式的占位字符串（不模仿任何
厂商密钥前缀），避免触发凭据扫描与误解。
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from clipwright.api import settings as settings_api
from clipwright.config import Settings, settings
from clipwright.config_schema import FIELD_META, MISSING_META
from clipwright.services import settings_store, settings_runtime, webhook_crypto

_FAKE_KEY = "test-fake-key-value-01"  # 占位值，非真实凭据


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """store 文件指向 tmp；settings 全字段测试后还原。"""
    monkeypatch.setattr(settings_store, "store_path", lambda: tmp_path / "settings.json")
    snapshot = settings.model_dump()
    yield
    for key, value in snapshot.items():
        try:
            setattr(settings, key, value)
        except Exception:
            pass


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(settings_api.router)
    return TestClient(app)


class TestCoverage:
    def test_every_settings_field_has_meta(self) -> None:
        assert MISSING_META == [], f"缺少元数据的字段: {MISSING_META}"

    def test_meta_references_valid_fields(self) -> None:
        assert set(FIELD_META) == set(Settings.model_fields)


class TestGetSettings:
    def test_groups_shape(self, client: TestClient) -> None:
        resp = client.get("/api/settings")
        assert resp.status_code == 200
        data = resp.json()
        ids = [g["id"] for g in data["groups"]]
        assert {"llm", "render", "security", "storage"} <= set(ids)
        all_keys = {f["key"] for g in data["groups"] for f in g["fields"]}
        assert all_keys == set(Settings.model_fields)

    def test_secret_never_leaks_plaintext(self, client: TestClient, monkeypatch, tmp_path) -> None:
        import base64

        def _enc(s: str) -> str:
            return base64.b64encode(s.encode("utf-8")).decode("ascii")

        def _dec(s: str) -> str:
            return base64.b64decode(s.encode("ascii")).decode("utf-8")

        monkeypatch.setattr(webhook_crypto, "has_key", lambda: True)
        monkeypatch.setattr(webhook_crypto, "encrypt_secret", _enc)
        monkeypatch.setattr(webhook_crypto, "decrypt_secret", _dec)
        settings.llm_api_key = _FAKE_KEY
        settings_store.save({"llm_api_key": _FAKE_KEY})
        data = client.get("/api/settings").json()
        assert _FAKE_KEY not in json.dumps(data)
        field = next(f for g in data["groups"] for f in g["fields"] if f["key"] == "llm_api_key")
        assert field["secret"] is True
        assert field["value"] is None
        assert field["has_value"] is True
        assert field["masked"]  # 前端占位符存在
        # 落盘文件里也只有密文
        raw = (tmp_path / "settings.json").read_text(encoding="utf-8")
        assert _FAKE_KEY not in raw

    def test_nonsecret_value_and_source(self, client: TestClient) -> None:
        data = client.get("/api/settings").json()
        field = next(f for g in data["groups"] for f in g["fields"] if f["key"] == "llm_model")
        assert field["value"] == settings.llm_model
        assert field["source"] in ("store", "env", "default")

    def test_admin_guard(self, client: TestClient, monkeypatch) -> None:
        import clipwright.authz as authz

        monkeypatch.setattr(authz, "current_user_id", lambda req: "u1")
        monkeypatch.setattr(authz, "is_admin", lambda req: False)
        assert client.get("/api/settings").status_code == 403
        assert (
            client.put("/api/settings", json={"values": {"rag_top_k": 9}}).status_code
            == 403
        )


class TestPutSettings:
    def test_put_int_applies_and_persists(self, client: TestClient) -> None:
        original = settings.rag_top_k
        try:
            resp = client.put("/api/settings", json={"values": {"rag_top_k": 9}})
            assert resp.status_code == 200
            body = resp.json()
            assert body["applied"] == ["rag_top_k"]
            assert settings.rag_top_k == 9
            store = settings_store.load()
            assert store["rag_top_k"] == 9
            assert settings_store.source_of("rag_top_k") == "store"
        finally:
            settings.rag_top_k = original

    def test_put_string_coercion_and_list_split(self, client: TestClient) -> None:
        original = settings.plugin_allowed_permissions
        try:
            resp = client.put(
                "/api/settings",
                json={"values": {"plugin_allowed_permissions": "network, fs_read"}},
            )
            assert resp.status_code == 200
            assert settings.plugin_allowed_permissions == ["network", "fs_read"]
        finally:
            settings.plugin_allowed_permissions = original

    def test_put_invalid_value_422(self, client: TestClient) -> None:
        resp = client.put("/api/settings", json={"values": {"rag_top_k": "not-a-number"}})
        assert resp.status_code == 422
        assert "rag_top_k" in resp.json()["detail"]

    def test_put_unknown_key_400(self, client: TestClient) -> None:
        resp = client.put("/api/settings", json={"values": {"no_such_key": 1}})
        assert resp.status_code == 400

    def test_put_masked_secret_keeps_current(self, client: TestClient, monkeypatch) -> None:
        monkeypatch.setattr(webhook_crypto, "has_key", lambda: True)
        monkeypatch.setattr(webhook_crypto, "encrypt_secret", lambda s: "enc::" + s)
        settings.llm_api_key = _FAKE_KEY
        resp = client.put(
            "/api/settings", json={"values": {"llm_api_key": "••••••••"}}
        )
        assert resp.status_code == 200
        assert resp.json()["applied"] == []
        assert settings.llm_api_key == _FAKE_KEY
        assert "llm_api_key" not in settings_store.load()

    def test_put_secret_without_key_409(self, client: TestClient, monkeypatch) -> None:
        monkeypatch.setattr(webhook_crypto, "has_key", lambda: False)
        resp = client.put("/api/settings", json={"values": {"llm_api_key": _FAKE_KEY}})
        assert resp.status_code == 409
        assert not settings_store.has_stored_overrides()

    def test_put_hot_triggers_hook(self, client: TestClient) -> None:
        fired: list[str] = []
        settings_runtime.register(("rag_top_k",), lambda: fired.append("rag"))
        original = settings.rag_top_k
        try:
            resp = client.put("/api/settings", json={"values": {"rag_top_k": 7}})
            assert resp.status_code == 200
            assert "rag" in fired
        finally:
            settings.rag_top_k = original

    def test_clear_value_restores_env_or_default(self, client: TestClient) -> None:
        original = settings.llm_model
        try:
            client.put("/api/settings", json={"values": {"llm_model": "test-model-x"}})
            assert settings.llm_model == "test-model-x"
            resp = client.put("/api/settings", json={"values": {"llm_model": None}})
            assert resp.status_code == 200
            assert "llm_model" in resp.json()["cleared"]
            # 还原为「无 store 覆盖」时的值（env 或默认）
            pristine = Settings()
            assert settings.llm_model == pristine.llm_model
        finally:
            settings.llm_model = original


class TestReset:
    def test_reset_specific_keys(self, client: TestClient) -> None:
        original = (settings.llm_model, settings.rag_top_k)
        try:
            client.put(
                "/api/settings",
                json={"values": {"llm_model": "m1", "rag_top_k": 11}},
            )
            resp = client.post("/api/settings/reset", json={"keys": ["llm_model"]})
            assert resp.status_code == 200
            assert resp.json()["cleared"] == ["llm_model"]
            store = settings_store.load()
            assert "llm_model" not in store
            assert store.get("rag_top_k") == 11
        finally:
            settings.llm_model, settings.rag_top_k = original

    def test_reset_all(self, client: TestClient) -> None:
        client.put("/api/settings", json={"values": {"rag_top_k": 12}})
        resp = client.post("/api/settings/reset", json={"keys": []})
        assert resp.status_code == 200
        assert not settings_store.has_stored_overrides()

    def test_reset_unknown_key_400(self, client: TestClient) -> None:
        resp = client.post("/api/settings/reset", json={"keys": ["bogus"]})
        assert resp.status_code == 400


class TestObjectFields:
    """对象型配置（模型单价表 / 路由表）必须能在设置页真的填进去。

    旧实现把它们推导成 `string` → 前端渲染单行文本框，粘 JSON 必然 422，
    于是"单价表从设置里配置"这句话其实做不到。
    """

    def test_kind_is_dict_so_ui_renders_json_editor(self) -> None:
        assert FIELD_META["model_prices"].kind == "dict"
        assert FIELD_META["model_routes"].kind == "dict"

    def test_put_model_prices_accepts_object(self, client: TestClient) -> None:
        prices = {"openai/gpt-4o": {"in": 0.005, "out": 0.015}}
        resp = client.put("/api/settings", json={"values": {"model_prices": prices}})
        assert resp.status_code == 200
        assert settings.model_prices == prices
        assert settings_store.load()["model_prices"] == prices

    def test_put_model_prices_accepts_json_string(self, client: TestClient) -> None:
        """前端文本域可能把 JSON 当字符串提交（宽容处理）。"""
        prices = {"deepseek-chat": {"in": 0.001, "out": 0.002}}
        resp = client.put(
            "/api/settings", json={"values": {"model_prices": json.dumps(prices)}}
        )
        assert resp.status_code == 200
        assert settings.model_prices == prices

    def test_put_invalid_json_string_is_422(self, client: TestClient) -> None:
        resp = client.put("/api/settings", json={"values": {"model_prices": "{not json"}})
        assert resp.status_code == 422

    def test_put_auto_spawn_and_hard_floor_switches(self, client: TestClient) -> None:
        """本轮新增的两个开关必须能在设置页改（否则等于写死）。"""
        resp = client.put("/api/settings", json={"values": {
            "supervisor_auto_spawn": False,
            "exec_hard_floor_never_authorizable": True,
        }})
        assert resp.status_code == 200
        assert settings.supervisor_auto_spawn is False
        assert settings.exec_hard_floor_never_authorizable is True

