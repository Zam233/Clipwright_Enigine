"""B3 测试：LLM 客户端构建按角色绑定取源凭据（无绑定回退旧 settings）。"""
from __future__ import annotations

import pytest

from clipwright.services import provider_sources as ps
from clipwright.services.llm import LLMService, _resolve_role_binding


@pytest.fixture(autouse=True)
def _isolate(tmp_path):
    ps.reset_for_tests(tmp_path / "provider_sources.json")
    yield
    ps.reset_for_tests(None)


@pytest.fixture
def captured(monkeypatch):
    """捕获 `_construct_client` 的入参，避免真实构建客户端。"""
    calls: list[dict] = []

    def fake(cls_or_self, provider, *, api_key, base_url, model, instructions,
             max_tokens=8192, flash=False):
        calls.append({
            "provider": provider, "api_key": api_key,
            "base_url": base_url, "model": model, "flash": flash,
        })
        return {"provider": provider, "model": model}

    monkeypatch.setattr(LLMService, "_construct_client", classmethod(fake))
    return calls


def _make_bound_source(role: str, provider: str, model: str) -> str:
    src = ps.create_source({
        "name": f"src-{role}", "kind": "llm", "provider": provider,
        "base_url": f"https://{provider}.example.com",
        "api_keys": ["sk-bound-key-0001"], "models": [model],
    })
    ps.set_role(role, src["id"], model)
    return src["id"]


class TestResolveRoleBinding:
    def test_no_role_or_no_binding_returns_none(self):
        assert _resolve_role_binding("") is None
        assert _resolve_role_binding("vision") is None  # 未绑定

    def test_bound_role_returns_plain_credentials(self):
        _make_bound_source("vision", "anthropic", "claude-3-haiku")
        bound = _resolve_role_binding("vision")
        assert bound is not None
        assert bound["provider"] == "anthropic"
        assert bound["model"] == "claude-3-haiku"
        assert bound["api_key"] == "sk-bound-key-0001"  # 解析给运行时用明文


class TestBuildClientRoleBinding:
    def test_static_build_client_uses_binding(self, captured):
        _make_bound_source("vision", "anthropic", "claude-3-haiku")
        LLMService.build_client(provider="openai", api_key="sk-old", model="old-model",
                                base_url="https://old.example.com", role="vision")
        call = captured[-1]
        assert call["provider"] == "anthropic"
        assert call["model"] == "claude-3-haiku"
        assert call["api_key"] == "sk-bound-key-0001"
        assert call["base_url"] == "https://anthropic.example.com"

    def test_static_build_client_without_binding_keeps_args(self, captured):
        LLMService.build_client(provider="openai", api_key="sk-old", model="old-model",
                                base_url="https://old.example.com", role="vision")
        call = captured[-1]
        assert call["provider"] == "openai"
        assert call["model"] == "old-model"
        assert call["api_key"] == "sk-old"

    def test_static_build_client_no_role_ignores_binding(self, captured):
        _make_bound_source("main", "anthropic", "claude-3-5-sonnet")
        LLMService.build_client(provider="openai", api_key="sk-old", model="old-model")
        call = captured[-1]
        assert call["provider"] == "openai"
        assert call["model"] == "old-model"

    def test_instance_main_and_flash_use_binding(self, captured, monkeypatch):
        from clipwright import config as cfg
        _make_bound_source("main", "anthropic", "claude-main")
        _make_bound_source("flash", "openai", "gpt-flash")
        monkeypatch.setattr(cfg.settings, "llm_provider", "openai", raising=False)
        monkeypatch.setattr(cfg.settings, "llm_model", "legacy-main", raising=False)
        monkeypatch.setattr(cfg.settings, "llm_flash_model", "legacy-flash", raising=False)

        svc = LLMService()
        svc._build_client(flash=False)
        assert captured[-1]["model"] == "claude-main"
        assert captured[-1]["provider"] == "anthropic"
        assert captured[-1]["flash"] is False

        svc._build_client(flash=True)
        assert captured[-1]["model"] == "gpt-flash"
        assert captured[-1]["provider"] == "openai"
        assert captured[-1]["flash"] is True

    def test_unbound_role_falls_back_to_settings(self, captured, monkeypatch):
        from clipwright import config as cfg
        monkeypatch.setattr(cfg.settings, "llm_provider", "openai", raising=False)
        monkeypatch.setattr(cfg.settings, "llm_model", "legacy-main", raising=False)
        LLMService()._build_client(flash=False)
        assert captured[-1]["model"] == "legacy-main"
        assert captured[-1]["provider"] == "openai"
