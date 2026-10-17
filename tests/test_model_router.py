"""B4：模型路由（白名单 / 优先级 / 失败隔离）+ 熔断键 (stage, model)。

对应 PRD F8 与验收 A9（留痕）、A18（跨 provider 生效且记账不阻断）。
"""

from __future__ import annotations

import pytest

from clipwright.config import settings
from clipwright.services import model_router as mr
from clipwright.services.model_router import (
    ModelRoute,
    active_route,
    allowed_providers,
    choose_route,
    is_route_allowed,
    resolve_provider_credentials,
    risk_judge_route,
    route_table,
    use_route,
)


@pytest.fixture(autouse=True)
def _clean_routes(monkeypatch):
    monkeypatch.setattr(settings, "model_routes", {}, raising=False)
    monkeypatch.setattr(settings, "model_whitelist", [], raising=False)
    monkeypatch.setattr(settings, "risk_judge_model", "", raising=False)
    yield


def _prov() -> str:
    return str(settings.llm_provider)


def _routes(**by_stage):
    monkeypatch_target = {}
    for stage, items in by_stage.items():
        monkeypatch_target[stage] = items
    return monkeypatch_target


# ── 路由表解析 ──────────────────────────────────────────────


def test_empty_table_means_no_routing(monkeypatch):
    monkeypatch.setattr(settings, "model_routes", {}, raising=False)
    assert route_table() == []
    assert choose_route("edit") is None


def test_route_table_parses_and_drops_invalid(monkeypatch):
    monkeypatch.setattr(settings, "model_routes", {
        "edit": [
            {"provider": _prov(), "model": "claude-x", "tier": "std", "price_in": 1.0},
            {"provider": _prov()},          # 缺 model → 丢弃
            {"model": "bare"},                   # 缺 provider → 用默认 provider
        ],
    }, raising=False)
    routes = route_table("edit")
    assert [r.model for r in routes] == ["claude-x", "bare"]
    assert routes[1].provider == settings.llm_provider


def test_tier_preference_per_stage(monkeypatch):
    monkeypatch.setattr(settings, "model_routes", {
        "material": [
            {"provider": _prov(), "model": "strong-m", "tier": "strong", "price_in": 9},
            {"provider": _prov(), "model": "cheap-m", "tier": "cheap", "price_in": 1},
        ],
        "structure": [
            {"provider": _prov(), "model": "strong-s", "tier": "strong", "price_in": 9},
            {"provider": _prov(), "model": "cheap-s", "tier": "cheap", "price_in": 1},
        ],
    }, raising=False)
    # 选材偏便宜，结构偏标准/强
    assert choose_route("material").model == "cheap-m"
    assert choose_route("structure").model == "strong-s"


def test_budget_pressure_blocks_upgrade(monkeypatch):
    monkeypatch.setattr(settings, "model_routes", {
        "edit": [
            {"provider": _prov(), "model": "strong-e", "tier": "strong", "price_in": 9},
            {"provider": _prov(), "model": "cheap-e", "tier": "cheap", "price_in": 1},
        ],
    }, raising=False)
    assert choose_route("edit").model == "strong-e"
    assert choose_route("edit", budget_ratio=0.85).model == "cheap-e"


def test_failed_model_is_skipped(monkeypatch):
    monkeypatch.setattr(settings, "model_routes", {
        "edit": [
            {"provider": _prov(), "model": "a", "tier": "std"},
            {"provider": _prov(), "model": "b", "tier": "std"},
        ],
    }, raising=False)
    route = choose_route("edit", failed_pairs=[f"{_prov()}/a"])
    assert route.model == "b"
    assert choose_route("edit", failed_pairs=[f"{_prov()}/a", f"{_prov()}/b", "a", "b"]) is None


# ── 白名单 ─────────────────────────────────────────────────


def test_explicit_whitelist_enforced(monkeypatch):
    monkeypatch.setattr(settings, "model_whitelist", [f"{_prov()}/only-this"], raising=False)
    assert is_route_allowed(ModelRoute(provider=_prov(), model="only-this"))
    assert not is_route_allowed(ModelRoute(provider="openai", model="gpt-x"))


def test_whitelist_accepts_bare_model(monkeypatch):
    monkeypatch.setattr(settings, "model_whitelist", ["bare-model"], raising=False)
    assert is_route_allowed(ModelRoute(provider="openai", model="bare-model"))


def test_allowed_providers_includes_defaults():
    providers = allowed_providers()
    assert settings.llm_provider in providers


def test_choose_route_respects_whitelist(monkeypatch):
    monkeypatch.setattr(settings, "model_whitelist", [f"{_prov()}/ok"], raising=False)
    monkeypatch.setattr(settings, "model_routes", {
        "edit": [
            {"provider": "openai", "model": "blocked", "tier": "std"},
            {"provider": _prov(), "model": "ok", "tier": "std"},
        ],
    }, raising=False)
    assert choose_route("edit").model == "ok"


# ── risk_judge 共用白名单 ─────────────────────────────────


def test_risk_judge_uses_cheapest_cheap_route(monkeypatch):
    monkeypatch.setattr(settings, "model_routes", {
        "quality": [{"provider": _prov(), "model": "j-cheap", "tier": "cheap", "price_in": 1}],
        "edit": [{"provider": _prov(), "model": "j-cheap2", "tier": "cheap", "price_in": 3}],
    }, raising=False)
    assert risk_judge_route().model == "j-cheap"


def test_risk_judge_explicit_model_must_pass_whitelist(monkeypatch):
    monkeypatch.setattr(settings, "model_whitelist", [f"{_prov()}/allowed"], raising=False)
    monkeypatch.setattr(settings, "risk_judge_model", "openai/not-allowed", raising=False)
    assert risk_judge_route() is None

    monkeypatch.setattr(settings, "risk_judge_model", f"{_prov()}/allowed", raising=False)
    assert risk_judge_route().model == "allowed"


def test_risk_judge_without_config_returns_none():
    assert risk_judge_route() is None


# ── 运行时上下文 ───────────────────────────────────────────


def test_use_route_scope_is_isolated():
    assert active_route() is None
    with use_route(ModelRoute(provider=_prov(), model="x")):
        assert active_route().model == "x"
    assert active_route() is None


# ── 凭据解析 ───────────────────────────────────────────────


def test_resolve_credentials_falls_back_to_settings_for_default_provider():
    creds = resolve_provider_credentials(settings.llm_provider)
    assert creds is not None
    assert creds["provider"] == settings.llm_provider


def test_resolve_credentials_unknown_provider_returns_none():
    assert resolve_provider_credentials("provider-that-does-not-exist") is None


# ── 熔断键 (stage, model) ──────────────────────────────────


def test_breaker_key_separates_models():
    from clipwright.services.pipeline_v2 import PipelineOrchestratorV2 as O

    assert O._breaker_key("edit") == "edit"
    assert O._breaker_key("edit", "anthropic/a") == "edit::anthropic/a"
    assert O._breaker_key("edit", "anthropic/a") != O._breaker_key("edit", "anthropic/b")


def test_failed_models_for_collects_only_open_breakers(monkeypatch):
    from clipwright.services.pipeline_v2 import PipelineOrchestratorV2 as O

    orch = object.__new__(O)
    orch._circuit_breakers = {
        "edit::anthropic/a": {"fail_count": 3},
        "edit::anthropic/b": {"fail_count": 1},
        "material::anthropic/a": {"fail_count": 5},
    }
    assert orch._failed_models_for("edit") == ["anthropic/a"]

# ── LLMService 的覆盖应用（单点埋设）─────────────────────────
# 说明：这里只验「分支选择」，不构建真实客户端——真实构造依赖 isobase 与凭据，
# 在全量跑批里会受其它用例影响（环境相关），不该混进路由逻辑的单测。


def _stub_default_client(monkeypatch):
    """把默认 client 打桩成常量，避免依赖 isobase / 凭据。"""
    from clipwright.services import llm as llm_mod

    monkeypatch.setattr(llm_mod.LLMService, "client",
                        property(lambda self: "DEFAULT-CLIENT"))
    monkeypatch.setattr(llm_mod.LLMService, "flash_client",
                        property(lambda self: "DEFAULT-CLIENT"))


def test_llm_service_applies_same_provider_model_override(monkeypatch):
    _stub_default_client(monkeypatch)
    from clipwright.services import llm as llm_mod

    svc = llm_mod.LLMService()
    with use_route(ModelRoute(provider=_prov(), model="routed-model")):
        client, model = svc._routed_client_and_model(use_flash=False)
    assert model == "routed-model"
    assert client == "DEFAULT-CLIENT"


def test_llm_service_without_route_keeps_defaults(monkeypatch):
    _stub_default_client(monkeypatch)
    from clipwright.services import llm as llm_mod

    svc = llm_mod.LLMService()
    client, model = svc._routed_client_and_model(use_flash=False)
    assert model is None
    assert client == "DEFAULT-CLIENT"


def test_llm_service_cross_provider_without_credentials_falls_back(monkeypatch):
    """目标 provider 没有凭据时：沿用默认 client，只把 model 透传下去。"""
    _stub_default_client(monkeypatch)
    from clipwright.services import llm as llm_mod

    svc = llm_mod.LLMService()
    monkeypatch.setattr(mr, "resolve_provider_credentials", lambda provider: None)
    with use_route(ModelRoute(provider="some-unconfigured-provider", model="x")):
        client, model = svc._routed_client_and_model(use_flash=False)
    assert model == "x"
    assert client == "DEFAULT-CLIENT"


def test_llm_service_cross_provider_builds_client_with_credentials(monkeypatch):
    _stub_default_client(monkeypatch)
    from clipwright.services import llm as llm_mod

    svc = llm_mod.LLMService()
    built: dict[str, object] = {}

    def fake_resolve(provider: str):
        built["provider"] = provider
        return {"provider": provider, "api_key": "k", "base_url": "http://x", "model": "m"}

    def fake_construct(provider, **kwargs):  # noqa: ANN001
        built["constructed"] = provider
        built["model"] = kwargs.get("model")
        return "NEW-CLIENT"

    monkeypatch.setattr(mr, "resolve_provider_credentials", fake_resolve)
    monkeypatch.setattr(llm_mod.LLMService, "_construct_client", staticmethod(fake_construct))

    with use_route(ModelRoute(provider="other-provider", model="their-model")):
        client, model = svc._routed_client_and_model(use_flash=False)

    assert client == "NEW-CLIENT"
    assert model == "their-model"
    assert built["constructed"] == "other-provider"
    assert built["model"] == "their-model"