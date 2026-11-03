"""B5：预算（可选闸）+ 人工改派/接受降级。

对应 PRD F6（预算可选、未配置=无上限）、F7（人工接管）与验收 A6。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from clipwright.config import settings
from clipwright.services import budget_guard as bg


def _call(model: str = "m1", provider: str = "p", tin: int = 1000, tout: int = 0,
          agent: str = "edit") -> dict:
    return {
        "pipeline_id": "pl_b",
        "agent_name": agent,
        "model": model,
        "provider": provider,
        "input_tokens": tin,
        "output_tokens": tout,
    }


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(settings, "pipeline_budget_usd", None, raising=False)
    monkeypatch.setattr(settings, "model_prices", {}, raising=False)
    from clipwright.api import pipeline as pipeline_api

    pipeline_api._pipeline_results.pop("pl_b", None)
    yield
    pipeline_api._pipeline_results.pop("pl_b", None)


def _client():
    from clipwright.api import supervisor as supervisor_api

    app = FastAPI()
    app.include_router(supervisor_api.router)
    return TestClient(app)


# ── 未配置预算 = 无上限 ────────────────────────────────────


def test_unconfigured_budget_is_unlimited_and_never_blocks(monkeypatch):
    monkeypatch.setattr(bg, "_calls", lambda pid: [_call(tin=10_000_000)])
    snap = bg.snapshot("pl_b")
    assert snap["budgetEnabled"] is False
    assert snap["level"] == "unlimited"
    assert snap["ratio"] is None
    assert snap["budget_usd"] is None
    assert bg.is_blocked("pl_b") is False
    assert bg.budget_ratio("pl_b") is None


# ── 配置预算后的三段行为 ───────────────────────────────────


def test_cost_is_computed_from_price_table(monkeypatch):
    monkeypatch.setattr(settings, "pipeline_budget_usd", 10.0, raising=False)
    monkeypatch.setattr(settings, "model_prices", {"p/m1": {"in": 2.0, "out": 0.0}}, raising=False)
    monkeypatch.setattr(bg, "_calls", lambda pid: [_call(tin=1000, tout=0)])
    snap = bg.snapshot("pl_b")
    assert snap["spent_usd"] == pytest.approx(2.0)
    assert snap["ratio"] == pytest.approx(0.2)
    assert snap["level"] == "ok"


def test_warn_at_seventy_percent(monkeypatch):
    monkeypatch.setattr(settings, "pipeline_budget_usd", 2.5, raising=False)
    monkeypatch.setattr(settings, "model_prices", {"p/m1": {"in": 2.0}}, raising=False)
    monkeypatch.setattr(bg, "_calls", lambda pid: [_call(tin=1000)])
    assert bg.snapshot("pl_b")["level"] == "warn"
    assert bg.is_blocked("pl_b") is False


def test_blocked_at_hundred_percent(monkeypatch):
    monkeypatch.setattr(settings, "pipeline_budget_usd", 1.5, raising=False)
    monkeypatch.setattr(settings, "model_prices", {"p/m1": {"in": 2.0}}, raising=False)
    monkeypatch.setattr(bg, "_calls", lambda pid: [_call(tin=1000)])
    snap = bg.snapshot("pl_b")
    assert snap["level"] == "blocked"
    assert bg.is_blocked("pl_b") is True


def test_unknown_model_price_costs_zero_but_tokens_count(monkeypatch):
    monkeypatch.setattr(settings, "pipeline_budget_usd", 1.0, raising=False)
    monkeypatch.setattr(bg, "_calls", lambda pid: [_call(model="unknown", tin=500, tout=500)])
    snap = bg.snapshot("pl_b")
    assert snap["spent_usd"] == 0.0
    assert snap["tokens"] == 1000
    assert snap["level"] == "ok"


def test_subagent_tokens_are_separated(monkeypatch):
    monkeypatch.setattr(settings, "pipeline_budget_usd", 10.0, raising=False)
    monkeypatch.setattr(settings, "model_prices", {"p/m1": {"in": 1.0}}, raising=False)
    monkeypatch.setattr(bg, "_calls", lambda pid: [
        _call(tin=1000, agent="edit"),
        _call(tin=2000, agent="edit:核查员"),
    ])
    snap = bg.snapshot("pl_b")
    assert snap["tokens"] == 3000
    assert snap["subagent_tokens"] == 2000
    assert snap["subagentSpentUsd"] == pytest.approx(2.0)


def test_price_falls_back_to_route_table(monkeypatch):
    monkeypatch.setattr(settings, "pipeline_budget_usd", 10.0, raising=False)
    monkeypatch.setattr(settings, "model_routes", {
        "edit": [{"provider": "p", "model": "routed", "tier": "std", "price_in": 3.0}],
    }, raising=False)
    monkeypatch.setattr(bg, "_calls", lambda pid: [_call(model="routed", tin=1000)])
    assert bg.snapshot("pl_b")["spent_usd"] == pytest.approx(3.0)


# ── 端点 ──────────────────────────────────────────────────


def test_budget_endpoint_shape(monkeypatch):
    monkeypatch.setattr(bg, "_calls", lambda pid: [_call(tin=100)])
    body = _client().get("/api/pipeline/pl_b/budget").json()
    assert body["level"] == "unlimited"
    assert body["budgetEnabled"] is False
    assert body["tokens"] == 100


def test_dispatch_rejects_non_core_stage():
    resp = _client().post("/api/pipeline/pl_b/dispatch", json={"target_stage": "plugin_x"})
    assert resp.status_code == 400


def test_dispatch_records_order_and_requires_retry():
    from clipwright.api import pipeline as pipeline_api

    pipeline_api._pipeline_results["pl_b"] = {"shared_data": {}}
    resp = _client().post(
        "/api/pipeline/pl_b/dispatch",
        json={"target_stage": "edit", "reason": "人工判断剪辑阶段要重做"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted"] is True
    # B1：派工不再触发执行（retired），所以不"需要重试"，但要如实告知替代做法
    assert body["requires_retry"] is False
    assert body["execution_status"] == "retired"
    assert body["order"]["target_stage"] == "edit"
    assert pipeline_api._pipeline_results["pl_b"]["shared_data"]["manual_orders"][0]["reason"]

    recorded = _client().get("/api/pipeline/pl_b/decisions")
    assert recorded.status_code == 200


def test_dispatch_blocked_by_budget(monkeypatch):
    monkeypatch.setattr(bg, "is_blocked", lambda pid: True)
    resp = _client().post("/api/pipeline/pl_b/dispatch", json={"target_stage": "edit"})
    assert resp.status_code == 409


def test_accept_suggestion_marks_degraded():
    from clipwright.api import pipeline as pipeline_api

    pipeline_api._pipeline_results["pl_b"] = {"shared_data": {}}
    resp = _client().post("/api/pipeline/pl_b/accept-suggestion?suggestion_id=s1")
    assert resp.status_code == 200
    assert resp.json()["accepted_degraded"] is True
    shared = pipeline_api._pipeline_results["pl_b"]["shared_data"]
    assert shared["accepted_degraded"] is True
    assert shared["decisions"][0]["strategy"] == "S4_accept_degraded"


# ── 派工闸 ────────────────────────────────────────────────


