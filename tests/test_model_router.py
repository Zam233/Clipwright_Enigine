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


@pytest.fixture(autouse=True)
def _isolate_provider_sources(tmp_path):
    """B3：显式 route 测试会真的创建 provider 源——必须隔离，不能写进用户配置。"""
    from clipwright.services import provider_sources as ps

    ps.reset_for_tests(tmp_path / "provider_sources.json")
    yield
    ps.reset_for_tests(None)


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
    from clipwright.services import circuit_breaker as cb

    assert cb.key_of("edit") == "edit"
    assert cb.key_of("edit", "anthropic/a") == "edit::anthropic/a"
    assert cb.key_of("edit", "anthropic/a") != cb.key_of("edit", "anthropic/b")


def test_failed_models_for_collects_only_open_breakers(monkeypatch):
    from clipwright.services import circuit_breaker as cb

    cb.reset()
    cb._BREAKERS["edit::anthropic/a"] = {"fail_count": 3, "last_fail_at": None}
    cb._BREAKERS["edit::anthropic/b"] = {"fail_count": 1, "last_fail_at": None}
    cb._BREAKERS["material::anthropic/a"] = {"fail_count": 5, "last_fail_at": None}
    try:
        assert cb.failed_models_for("edit") == ["anthropic/a"]
    finally:
        cb.reset()

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


# ── B3：显式 route（子 agent 指定 provider/model）────────────────


def _make_source(provider: str, models: list[str], *, kind: str = "llm") -> str:
    from clipwright.services import provider_sources as ps

    src = ps.create_source({
        "name": f"src-{provider}", "kind": kind, "provider": provider,
        "base_url": f"https://{provider}.example.com",
        "api_keys": ["sk-explicit-0001"], "models": list(models),
    })
    return str(src["id"])


def test_resolve_explicit_route_unknown_provider(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openai", raising=False)
    res = mr.resolve_explicit_route("从没配过的源", "any-model")
    assert res.ok is False
    assert res.reason == "provider_not_configured"
    assert res.route is None
    assert "未配置" in res.message


def test_resolve_explicit_route_configured_model(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openai", raising=False)
    _make_source("anthropic", ["claude-3-haiku"])
    res = mr.resolve_explicit_route("anthropic", "claude-3-haiku")
    assert res.ok is True
    assert res.route is not None
    assert res.route.key == "anthropic/claude-3-haiku"


def test_resolve_explicit_route_model_not_in_source(monkeypatch):
    """来源有清单、模型不在其中、adapter 又不许任意模型 → 如实拒绝。"""
    monkeypatch.setattr(settings, "llm_provider", "openai", raising=False)
    _make_source("anthropic", ["claude-3-haiku"])
    res = mr.resolve_explicit_route("anthropic", "claude-3-opus")
    assert res.ok is False
    assert res.reason == "model_not_configured"
    assert "不在" in res.message


def test_resolve_explicit_route_empty_list_requires_adapter_declaration(monkeypatch):
    """来源没有模型清单时：只有声明了 arbitrary_models 的 provider 才放行。"""
    monkeypatch.setattr(settings, "llm_provider", "openai", raising=False)
    _make_source("anthropic", [])  # 有源、有密钥、无模型清单
    _make_source("openai", [])     # openai adapter 声明 arbitrary_models=True

    anthropic_res = mr.resolve_explicit_route("anthropic", "claude-3-haiku")
    assert anthropic_res.ok is False
    assert anthropic_res.reason == "model_not_configured"

    openai_res = mr.resolve_explicit_route("openai", "gpt-whatever-2026")
    assert openai_res.ok is True
    assert openai_res.route is not None
    assert openai_res.route.model == "gpt-whatever-2026"


def test_resolve_explicit_route_ignores_model_whitelist(monkeypatch):
    """白名单非空也不能挡住已配置 provider + 可用模型的显式 route。"""
    monkeypatch.setattr(settings, "llm_provider", "openai", raising=False)
    monkeypatch.setattr(settings, "model_whitelist", ["openai/gpt-x"], raising=False)
    _make_source("anthropic", ["claude-3-haiku"])
    # 自动选路仍然受白名单约束
    assert mr.is_route_allowed(mr.ModelRoute(provider="anthropic", model="claude-3-haiku")) is False
    # 显式 route 不看白名单，只看 provider 是否已配置 + 模型是否可用
    assert mr.resolve_explicit_route("anthropic", "claude-3-haiku").ok is True


def test_resolve_explicit_route_falls_back_to_bound_model(monkeypatch):
    """模型留空时用该来源的绑定/默认模型，而不是直接报错。"""
    monkeypatch.setattr(settings, "llm_provider", "openai", raising=False)
    from clipwright.services import provider_sources as ps

    sid = _make_source("anthropic", ["claude-3-haiku"])
    ps.set_role("main", sid, "claude-3-haiku")
    res = mr.resolve_explicit_route("anthropic", "")
    assert res.ok is True
    assert res.route is not None
    assert res.route.model == "claude-3-haiku"