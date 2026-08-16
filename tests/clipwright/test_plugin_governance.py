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
