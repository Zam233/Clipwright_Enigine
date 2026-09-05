"""P10: 插件治理（P1-5 回滚 / P1-7 密钥加密）与 Persona 知识库 API 测试。"""

from __future__ import annotations

import pytest

from clipwright.plugins.config_types import (
    encrypt_field_value,
    decrypt_field_value,
    mask_secret_value,
)
from clipwright.config import settings


class TestPluginSecretFields:
    def test_encrypt_decrypt_roundtrip(self, monkeypatch) -> None:
        """配置 Fernet key 时：secret 字段加密落盘、解密还原、掩码显示。"""
        import base64
        from cryptography.fernet import Fernet
        monkeypatch.setattr(settings, "webhook_secret_key", Fernet.generate_key().decode())
        import clipwright.services.webhook_crypto as wc
        wc._FERNET = None

        field = {"type": "string", "value": "sk-abc123", "label": "API Key", "secret": True}
        enc = encrypt_field_value(field, "sk-abc123")
        assert enc != "sk-abc123"
        assert decrypt_field_value(field, enc) == "sk-abc123"
        assert mask_secret_value(decrypt_field_value(field, enc)).startswith("sk-a")

    def test_non_secret_field_unchanged(self) -> None:
        field = {"type": "string", "value": "hello", "label": "名称"}
        assert encrypt_field_value(field, "hello") == "hello"
        assert mask_secret_value("hello") == "hell****"

    def test_mask_short_value(self) -> None:
        assert mask_secret_value("ab") == "****"


class TestReloadRollback:
    def test_reload_failure_restores_old_plugin(self, tmp_path) -> None:
        """P1-5: reload 失败 → 旧插件实例回滚，不消失。"""
        from clipwright.plugins.loader import PluginLoader
        loader = PluginLoader(plugin_dir=tmp_path / "plugins", data_dir=tmp_path / "data")
        old = object()
        loader._plugins["p1"] = old  # type: ignore[assignment]
        loader._metadatas["p1"] = {"plugin_id": "p1"}  # type: ignore[assignment]

        # load() 抛错（模拟加载失败）
        def boom(_id):
            from clipwright.plugins.loader import PluginLoadError
            raise PluginLoadError("load failed")
        loader.load = boom  # type: ignore[method-assign]

        loader.reload("p1")
        assert loader._plugins.get("p1") is old
        assert loader._metadatas.get("p1") == {"plugin_id": "p1"}

    def test_reload_generic_exception_rolls_back(self, tmp_path) -> None:
        """P8: 非.PluginLoadError 的任意异常同样回滚，插件不静默消失。"""
        from clipwright.plugins.loader import PluginLoader
        loader = PluginLoader(plugin_dir=tmp_path / "plugins", data_dir=tmp_path / "data")
        old = object()
        loader._plugins["p1"] = old  # type: ignore[assignment]
        loader._metadatas["p1"] = {"plugin_id": "p1"}  # type: ignore[assignment]

        def boom(_id):
            raise RuntimeError("disk exploded")
        loader.load = boom  # type: ignore[method-assign]

        loader.reload("p1")
        assert loader._plugins.get("p1") is old

    def test_load_none_rolls_back_and_reinitializes(self, tmp_path) -> None:
        """P8: load 返回 None（禁用/目录缺失）→ 回滚 + 旧实例重新 initialize。"""
        from types import SimpleNamespace
        from clipwright.plugins.loader import PluginLoader
        loader = PluginLoader(plugin_dir=tmp_path / "plugins", data_dir=tmp_path / "data")
        init_calls: list[str] = []
        old = SimpleNamespace(initialize=lambda: init_calls.append("init"))
        loader._plugins["p1"] = old
        loader._metadatas["p1"] = {"plugin_id": "p1"}

        loader.load = lambda _id: None  # type: ignore[method-assign,assignment]

        loader.reload("p1")
        assert loader._plugins.get("p1") is old
        assert init_calls == ["init"], "shutdown 后旧实例应重新 initialize"


class TestPluginHealthEndpoint:
    def test_health_aggregates_states(self, tmp_path, monkeypatch) -> None:
        """P10: 健康聚合视图——ok/degraded/未加载 分类与计数。"""
        from types import SimpleNamespace
        from fastapi.testclient import TestClient
        from clipwright.main import app
        from clipwright.api import plugin as plugin_api

        fake_manifest = SimpleNamespace(id="p_loaded", name="已加载插件", version="1.0")
        fake_meta = SimpleNamespace(
            manifest=fake_manifest, enabled=True, signed=True, verified=True,
            dependency_ok=False, missing_dependencies=["dep_x"],
        )

        class FakeLoader:
            def list_loaded(self):
                return [fake_meta]
            def discover(self):
                return ["p_loaded", "p_ghost"]
            def is_enabled(self, pid):
                return True

        plugin_api.set_loader(FakeLoader())
        client = TestClient(app)
        resp = client.get("/api/plugin/health")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["overall"] == "degraded"
        by_id = {p["plugin_id"]: p for p in body["plugins"]}
        assert by_id["p_loaded"]["status"] == "degraded"
        assert "缺依赖" in by_id["p_loaded"]["issues"][0]
        assert by_id["p_ghost"]["issues"] == ["未加载"]

    def test_health_without_loader(self) -> None:
        from fastapi.testclient import TestClient
        from clipwright.main import app
        from clipwright.api import plugin as plugin_api

        saved = plugin_api._loader
        plugin_api._loader = None
        try:
            client = TestClient(app)
            body = client.get("/api/plugin/health").json()
            assert body["overall"] == "ok"
            assert body["plugins"] == []
        finally:
            plugin_api._loader = saved


class TestPersonaKnowledgeApi:
    def test_knowledge_put_delete_endpoints(self, tmp_path, monkeypatch) -> None:
        """B10: 知识库 PUT/DELETE API 端点。"""
        from fastapi.testclient import TestClient
        from clipwright.main import app
        from clipwright.persona.repository import PersonaRepository
        from clipwright.schema.persona import PersonaManifest
        from clipwright.api import persona as persona_api

        monkeypatch.setattr(settings, "persona_dir", tmp_path / "personas")
        persona_api._repo = PersonaRepository(tmp_path / "personas")
        repo = persona_api._repo
        pid = "persona_kapi"
        repo.save_manifest(PersonaManifest(persona_id=pid, persona_name="知识API",
                                           parameter={"persona_id": pid, "identity": {"tone": "neutral"}}))

        client = TestClient(app)
        # 添加
        add = client.post(f"/api/persona/{pid}/knowledge", json={
            "title": "文档", "content": "内容", "source": "t",
        })
        assert add.status_code == 200, add.text
        doc_id = add.json()["doc_id"]
        # PUT 更新
        upd = client.put(f"/api/persona/{pid}/knowledge/{doc_id}", json={
            "id": doc_id, "title": "新标题", "content": "新内容", "source": "t",
        })
        assert upd.status_code == 200, upd.text
        # DELETE
        assert client.delete(f"/api/persona/{pid}/knowledge/{doc_id}").status_code == 200
        # 再删 → 404
        assert client.delete(f"/api/persona/{pid}/knowledge/{doc_id}").status_code == 404
