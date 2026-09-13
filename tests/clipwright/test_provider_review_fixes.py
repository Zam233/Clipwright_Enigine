"""Provider 评审（第二轮）修复项的回归测试。

覆盖：
- P1 传输参数按签名过滤（存量插件 provider 不再 TypeError）
- P2 角色↔源类型校验 + 已绑定源不允许改 kind
- P3 模型被停用/删除 → 引用它的角色自动解绑（并回传 unbound_roles）
- P4 `/api/test/embed` 不再改写全局 settings
- P5 `/api/test/*` 报告真实生效的 provider/model 与来源
- B3 `POST /api/providers/{id}/test` 源级探测
- B5 模型拉取提示语区分「不支持」与「失败」
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from clipwright.api import model_probe
from clipwright.api import providers as providers_api
from clipwright.plugins.contribution import (
    Contribution,
    ContributionKind,
    ContributionRegistry,
)
from clipwright.providers.base import supported_transport_params
from clipwright.services import provider_sources as ps

PID = "plug_provider_review"


class _LegacyProvider:
    """B4 之前的签名（没有 timeout_sec/proxy/headers）。"""
    name = "review_legacy"

    def capabilities(self, model):
        from clipwright.providers.base import ProviderCapabilities
        return ProviderCapabilities(tool_calls=True)

    def normalize_messages(self, user, system):
        return [{"role": "user", "content": user}]

    def uses_system_kwarg(self):
        return False

    def default_model(self):
        return "m"

    def build_client(self, *, api_key, base_url, model, instructions, max_tokens, flash):
        return {"legacy": True, "model": model}


class _ModernProvider(_LegacyProvider):
    name = "review_modern"

    def build_client(self, *, api_key, base_url, model, instructions, max_tokens, flash,
                     timeout_sec=0.0, proxy="", headers=None):
        return {"timeout": timeout_sec, "proxy": proxy, "headers": headers}


@pytest.fixture(autouse=True)
def _isolate(tmp_path):
    ps.reset_for_tests(tmp_path / "provider_sources.json")
    ContributionRegistry.register(PID, [
        Contribution(ContributionKind.LLM_PROVIDER, _LegacyProvider.name, _LegacyProvider()),
        Contribution(ContributionKind.LLM_PROVIDER, _ModernProvider.name, _ModernProvider()),
    ], dispatch=False)
    yield
    ContributionRegistry.unregister_plugin(PID)
    ps.reset_for_tests(None)


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(providers_api.router)
    app.include_router(model_probe.router)

    @app.middleware("http")
    async def _inject_role(request, call_next):
        request.state.user_id = None  # off/token 语义（视为管理员）
        request.state.user_role = None
        return await call_next(request)

    return TestClient(app)


class TestP1TransportSignature:
    def test_signature_introspection(self):
        assert supported_transport_params(_LegacyProvider()) == set()
        assert supported_transport_params(_ModernProvider()) == {
            "timeout_sec", "proxy", "headers"}

    def test_legacy_provider_builds_without_transport_kwargs(self):
        from clipwright.services import llm as llm_mod
        from clipwright.services.llm import LLMService

        src = ps.create_source({
            "name": "legacy", "kind": "llm", "provider": _LegacyProvider.name,
            "models": ["m"], "api_keys": ["sk-legacy-0001"],
            "timeout_sec": 30, "proxy": "http://127.0.0.1:7890",
        })
        ps.set_role("main", src["id"], "m")
        llm_mod._client_cache.clear()
        client = LLMService.build_client(role="main")
        assert client == {"legacy": True, "model": "m"}

    def test_modern_provider_still_receives_transport(self):
        from clipwright.services import llm as llm_mod
        from clipwright.services.llm import LLMService

        src = ps.create_source({
            "name": "modern", "kind": "llm", "provider": _ModernProvider.name,
            "models": ["m"], "api_keys": ["sk-modern-0001"],
            "timeout_sec": 45, "proxy": "http://127.0.0.1:7890",
        })
        ps.set_role("main", src["id"], "m")
        llm_mod._client_cache.clear()
        client = LLMService.build_client(role="main")
        assert client["timeout"] == 45
        assert client["proxy"] == "http://127.0.0.1:7890"


class TestP2RoleKindValidation:
    def test_wrong_kind_rejected(self, client):
        tts = client.post("/api/providers", json={
            "name": "T", "kind": "tts", "provider": "qwen-tts", "models": ["qm"],
        }).json()
        resp = client.put("/api/providers/roles", json={
            "role": "flash", "source_id": tts["id"], "model": "qm",
        })
        assert resp.status_code == 400
        assert "llm" in resp.json()["detail"]

    def test_kind_change_blocked_while_bound(self, client):
        sid = client.post("/api/providers", json={
            "name": "L", "kind": "llm", "provider": "openai",
            "models": ["m1"], "api_keys": ["sk-k-0001"],
        }).json()["id"]
        client.put("/api/providers/roles", json={"role": "main", "source_id": sid, "model": "m1"})
        resp = client.put(f"/api/providers/{sid}", json={"kind": "tts"})
        assert resp.status_code == 400
        assert "不能改类型" in resp.json()["detail"]


class TestP3DisabledModelUnbinds:
    def test_disabling_bound_model_unbinds_role(self, client):
        sid = client.post("/api/providers", json={
            "name": "L", "kind": "llm", "provider": "openai",
            "models": ["m1", "m2"], "api_keys": ["sk-k-0001"],
        }).json()["id"]
        client.put("/api/providers/roles", json={"role": "main", "source_id": sid, "model": "m1"})
        out = client.post(f"/api/providers/{sid}/models/upsert",
                          json={"id": "m1", "enabled": False}).json()
        assert out["unbound_roles"] == ["main"]
        assert ps.get_roles() == {}

    def test_other_roles_survive(self, client):
        sid = client.post("/api/providers", json={
            "name": "L", "kind": "llm", "provider": "openai",
            "models": ["m1", "m2"], "api_keys": ["sk-k-0001"],
        }).json()["id"]
        client.put("/api/providers/roles", json={"role": "main", "source_id": sid, "model": "m1"})
        client.put("/api/providers/roles", json={"role": "flash", "source_id": sid, "model": "m2"})
        out = client.post(f"/api/providers/{sid}/models/upsert",
                          json={"id": "m1", "enabled": False}).json()
        assert out["unbound_roles"] == ["main"]
        assert set(ps.get_roles()) == {"flash"}


class TestP4P5ModelProbe:
    def test_embed_no_global_mutation(self, client, monkeypatch):
        from clipwright.config import settings
        before = settings.rag_embed_provider
        # 造一个必然失败的 provider 覆盖（只要不抛在断言之前即可）
        resp = client.post("/api/test/embed", json={"text": "x", "provider": "no-such-provider"})
        assert resp.status_code == 200
        assert resp.json()["success"] is False
        assert settings.rag_embed_provider == before, "P4：测试端点不得改写全局 settings"

    def test_llm_reports_bound_source(self, client):
        sid = ps.create_source({
            "name": "Deepseek", "kind": "llm", "provider": "openai",
            "models": ["deepseek-chat"], "api_keys": ["sk-k-0001"],
        })["id"]
        ps.set_role("main", sid, "deepseek-chat")
        body = client.post("/api/test/llm", json={"prompt": "hi"}).json()
        assert body["effective"] == "provider_source"
        assert body["source"] == "Deepseek"
        assert body["model"] == "deepseek-chat"

    def test_config_reports_effective(self, client):
        sid = ps.create_source({
            "name": "Embed", "kind": "embedding", "provider": "openai",
            "models": ["text-embedding-v3"], "api_keys": ["sk-k-0001"],
        })["id"]
        ps.set_role("embed", sid, "text-embedding-v3")
        cfg = client.get("/api/test/config").json()
        assert cfg["embed"]["effective"] == "provider_source"
        assert cfg["embed"]["source"] == "Embed"
        assert cfg["llm"]["effective"] == "settings"


class TestB3SourceTestEndpoint:
    def test_unknown_source_404(self, client):
        assert client.post("/api/providers/src_nope/test").status_code == 404

    def test_tts_reports_unsupported(self, client):
        sid = client.post("/api/providers", json={
            "name": "T", "kind": "tts", "provider": "qwen-tts", "models": ["qm"],
        }).json()["id"]
        body = client.post(f"/api/providers/{sid}/test").json()
        assert body["ok"] is False
        assert "不支持探测" in body["detail"]

    def test_llm_probe_returns_detail(self, client):
        sid = client.post("/api/providers", json={
            "name": "L", "kind": "llm", "provider": "openai",
            "base_url": "http://127.0.0.1:9", "models": ["m1"], "api_keys": ["sk-k-0001"],
        }).json()["id"]
        body = client.post(f"/api/providers/{sid}/test").json()
        assert body["ok"] is False  # 打不通
        assert body["detail"]


class TestB5FetchNoteWording:
    def test_unsupported_provider_note(self, client):
        sid = client.post("/api/providers", json={
            "name": "Local", "kind": "embedding", "provider": "local", "models": ["m"],
        }).json()["id"]
        body = client.post(f"/api/providers/{sid}/models/fetch").json()
        assert body["models"] == []
        assert "不支持自动获取模型列表" in body["note"]
