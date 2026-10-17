"""端到端验收（B8）：AstronBot 式 provider 配置在真实 app 上跑通。

覆盖用户需求的完整链路：
1. 顶部 tab（源类型）× 源列表 → 建源（多条密钥 / Base URL / 超时 / 代理 / 自定义头）
2. 模型清单（获取失败可自定义 / 启用开关）
3. 角色绑定（main/flash/vision 都在 LLM 页配置，工程按角色消费）
4. 插件注册的 provider 出现在下拉（设置页 + /api/providers/available），禁用即消失
5. 角色解析出的凭据真正进入 LLM 客户端构造（模型/密钥/传输参数）
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from clipwright.api import providers as providers_api
from clipwright.api import settings as settings_api
from clipwright.plugins.contribution import (
    Contribution,
    ContributionKind,
    ContributionRegistry,
)
from clipwright.services import provider_sources as ps
from clipwright.services.llm import LLMService, _resolve_role_binding


@pytest.fixture(autouse=True)
def _isolate(tmp_path):
    ps.reset_for_tests(tmp_path / "provider_sources.json")
    yield
    ps.reset_for_tests(None)
    ContributionRegistry.unregister_plugin("accept_plug")


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(providers_api.router)
    app.include_router(settings_api.router)
    return TestClient(app)


def _choices(client, key: str) -> list:
    for g in client.get("/api/settings").json()["groups"]:
        for f in g["fields"]:
            if f["key"] == key:
                return f["choices"]
    raise AssertionError(key)


class TestProviderConfigAcceptance:
    def test_full_flow_source_models_roles(self, client):
        # ① 顶部 tab = 源类型；LLM 页建源
        created = client.post("/api/providers", json={
            "name": "Deepseek", "kind": "llm", "provider": "openai",
            "base_url": "https://api.deepseek.com",
            "api_keys": ["sk-deepseek-1", "sk-deepseek-2"],  # 多条密钥
            "timeout_sec": 45, "proxy": "http://127.0.0.1:7890",
            "headers": {"X-Tenant": "clipwright"},
        })
        assert created.status_code == 200, created.text
        src = created.json()
        sid = src["id"]
        assert src["key_count"] == 2 and src["has_keys"] is True
        assert src["api_keys_masked"] == ["sk-d****", "sk-d****"]
        assert "api_keys" not in src  # 明文不外泄

        # ② 模型清单：获取失败给出可读 note；自定义模型 + 启用开关
        fetched = client.post(f"/api/providers/{sid}/models/fetch").json()
        assert fetched["models"] == [] and "note" in fetched
        assert client.post(f"/api/providers/{sid}/models/upsert", json={
            "id": "deepseek-chat", "name": "DeepSeek Chat", "enabled": True,
        }).status_code == 200
        assert client.post(f"/api/providers/{sid}/models/upsert", json={
            "id": "deepseek-reasoner", "name": "R1", "enabled": False,
        }).status_code == 200
        models = client.get(f"/api/providers/{sid}").json()["models"]
        assert {m["id"]: m["enabled"] for m in models} == {
            "deepseek-chat": True, "deepseek-reasoner": False,
        }

        # ③ 角色绑定：main 绑本源（其他角色保持未绑定）
        bound = client.put("/api/providers/roles", json={
            "role": "main", "source_id": sid, "model": "deepseek-chat",
        })
        assert bound.status_code == 200, bound.text
        roles = client.get("/api/providers/roles").json()
        assert roles["roles"]["main"]["model"] == "deepseek-chat"
        assert roles["available"] == ["main", "flash", "vision", "embed", "rerank", "tts"]

        # ④ 运行时解析：角色 → 明文凭据 + 传输参数
        resolved = _resolve_role_binding("main")
        assert resolved["provider"] == "openai"
        assert resolved["model"] == "deepseek-chat"
        assert resolved["api_key"] == "sk-deepseek-1"  # 第一条
        assert resolved["base_url"] == "https://api.deepseek.com"
        assert resolved["timeout_sec"] == 45.0
        assert resolved["proxy"] == "http://127.0.0.1:7890"
        assert resolved["headers"] == {"X-Tenant": "clipwright"}
        # 未绑定角色 → None（调用方回退 settings 旧字段）
        assert _resolve_role_binding("flash") is None

        # ⑤ 传输参数真正进入客户端构造（不真连网）
        seen: dict = {}

        class _Capture:
            @staticmethod
            def _construct_client(provider, *, api_key, base_url, model, instructions,
                                  max_tokens=8192, flash=False,
                                  timeout_sec=0.0, proxy="", headers=None):
                seen.update(provider=provider, api_key=api_key, base_url=base_url,
                            model=model, timeout_sec=timeout_sec, proxy=proxy,
                            headers=headers)
                return object()

        original = LLMService._construct_client
        LLMService._construct_client = _Capture._construct_client
        try:
            LLMService.build_client(role="main")
        finally:
            LLMService._construct_client = original
        assert seen["model"] == "deepseek-chat"
        assert seen["api_key"] == "sk-deepseek-1"
        assert seen["timeout_sec"] == 45.0 and seen["proxy"] == "http://127.0.0.1:7890"
        assert seen["headers"] == {"X-Tenant": "clipwright"}

    def test_llm_tab_holds_main_flash_vision(self, client):
        """用户在 LLM 页配置三种 LLM，角色在工程文件中分配。"""
        body = client.get("/api/providers/available").json()
        assert body["role_kinds"]["main"] == "llm"
        assert body["role_kinds"]["flash"] == "llm"
        assert body["role_kinds"]["vision"] == "llm"
        # 三类角色同属 llm 类型 → 前端把角色分配渲染在 LLM tab
        assert body["role_kinds"]["embed"] == "embedding"
        assert body["role_kinds"]["rerank"] == "rerank"
        assert body["role_kinds"]["tts"] == "tts"

    def test_plugin_provider_appears_and_desists(self, client):
        ContributionRegistry.register("accept_plug", [
            Contribution(ContributionKind.LLM_PROVIDER, "plugin_llm_accept", object()),
        ], dispatch=False)
        assert "plugin_llm_accept" in client.get(
            "/api/providers/available"
        ).json()["llm_provider"]
        assert "plugin_llm_accept" in _choices(client, "llm_provider")

        ContributionRegistry.unregister_plugin("accept_plug")
        assert "plugin_llm_accept" not in client.get(
            "/api/providers/available"
        ).json()["llm_provider"]
        assert "plugin_llm_accept" not in _choices(client, "llm_provider")

    def test_delete_source_unbinds_roles(self, client):
        sid = client.post("/api/providers", json={
            "name": "temp", "kind": "llm", "provider": "openai",
            "models": ["m1"], "api_keys": ["sk-temp-0001"],
        }).json()["id"]
        client.put("/api/providers/roles", json={
            "role": "vision", "source_id": sid, "model": "m1",
        })
        assert _resolve_role_binding("vision") is not None
        assert client.delete(f"/api/providers/{sid}").status_code == 200
        assert _resolve_role_binding("vision") is None

    def test_disabled_model_cannot_be_bound(self, client):
        sid = client.post("/api/providers", json={
            "name": "s", "kind": "llm", "provider": "openai",
            "models": [{"id": "off_model", "enabled": False}],
        }).json()["id"]
        bad = client.put("/api/providers/roles", json={
            "role": "main", "source_id": sid, "model": "off_model",
        })
        assert bad.status_code == 400
        assert "停用" in bad.json()["detail"]  # 可读原因：模型已停用，无法绑定
