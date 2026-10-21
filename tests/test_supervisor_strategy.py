"""B3：策略选择（S1–S6 + 同策略禁用）+ 派工执行门禁。

对应 PRD F3/F4 与验收 A3（同策略连续重试 ≤1 次）、A13（上游申报触发正确重做）。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from clipwright.schema.supervisor import Attribution, SupervisorDecision
from clipwright.services.supervisor import (
    STRATEGIES,
    choose_strategy,
    record_decision,
)


def _attr(stage: str = "edit", *, confidence: str = "high", unrepairable: bool = False,
          upstream=None, source: str = "rule") -> Attribution:
    return Attribution(
        primary_stage=stage,
        upstream_chain=list(upstream or []),
        confidence=confidence,
        unrepairable=unrepairable,
        reason=f"{stage} 出问题",
        source=source,
    )


# ── 基础策略 ────────────────────────────────────────────────


def test_no_attribution_aborts_instead_of_guessing():
    d = choose_strategy(None)
    assert d.strategy == "S6_abort"
    assert d.aborted is True
    assert "证据不足" in d.reason


def test_unrepairable_accepts_degradation():
    d = choose_strategy(_attr("material", unrepairable=True))
    assert d.strategy == "S4_accept_degraded"
    assert d.allow_degrade is True
    assert d.target_stage == ""


def test_first_failure_retries_same():
    d = choose_strategy(_attr("edit"), stage_attempts={"edit": 1})
    assert d.strategy == "S1_same"
    assert d.target_stage == "edit"


def test_first_failure_with_upstream_chain_goes_upstream():
    d = choose_strategy(_attr("edit", upstream=["material"]), stage_attempts={"edit": 1})
    assert d.strategy == "S3_upstream"
    assert d.target_stage == "material"


def test_second_failure_swaps_model():
    d = choose_strategy(_attr("edit"), stage_attempts={"edit": 2})
    assert d.strategy == "S2_model"
    assert d.target_stage == "edit"


def test_second_failure_without_model_budget_changes_path():
    d = choose_strategy(_attr("edit"), stage_attempts={"edit": 2}, max_model_swaps=0)
    assert d.strategy == "S5_new_path"


def test_third_failure_changes_path():
    d = choose_strategy(_attr("edit"), stage_attempts={"edit": 3})
    assert d.strategy == "S5_new_path"


def test_budget_exhausted_aborts():
    d = choose_strategy(_attr("edit"), budget_exhausted=True)
    assert d.strategy == "S6_abort"
    assert d.aborted is True


# ── 硬规则：同阶段同策略不得连续用第二次 ──────────────────


def test_same_strategy_escalates_instead_of_repeating():
    d = choose_strategy(
        _attr("edit"), stage_attempts={"edit": 1},
        used_pairs=[("edit", "S1_same")],
    )
    assert d.strategy == "S2_model"
    assert d.previous_strategy == "S1_same"


def test_escalation_skips_used_strategies():
    d = choose_strategy(
        _attr("edit"), stage_attempts={"edit": 2},
        used_pairs=[("edit", "S1_same"), ("edit", "S2_model")],
    )
    assert d.strategy == "S5_new_path"


def test_ladder_exhausted_aborts():
    d = choose_strategy(
        _attr("edit"), stage_attempts={"edit": 3},
        used_pairs=[("edit", "S1_same"), ("edit", "S2_model"), ("edit", "S5_new_path")],
    )
    assert d.strategy == "S6_abort"
    assert d.aborted is True
    assert "转人工" in d.reason


def test_upstream_repeated_falls_back_to_symptom_stage():
    d = choose_strategy(
        _attr("edit", upstream=["material"]), stage_attempts={"edit": 1},
        used_pairs=[("material", "S3_upstream")],
    )
    assert d.strategy in ("S2_model", "S5_new_path")
    assert d.target_stage != "material"


def test_all_strategies_are_declared():
    d = choose_strategy(_attr())
    assert d.strategy in STRATEGIES


# ── 决策留痕 ────────────────────────────────────────────────


def test_record_decision_appends_and_caps():
    state = SimpleNamespace(shared_data={})
    for i in range(55):
        record_decision(state, SupervisorDecision(id=f"d{i}", strategy="S1_same",
                                                  target_stage="edit"))
    assert len(state.shared_data["decisions"]) == 50
    assert state.shared_data["decisions"][-1]["id"] == "d54"


def test_decide_for_heal_off_mode_is_free(monkeypatch):
    from clipwright.config import settings
    from clipwright.services import pipeline_v2 as pv

    monkeypatch.setattr(settings, "supervisor_mode", "off", raising=False)
    orch = object.__new__(pv.PipelineOrchestratorV2)
    state = SimpleNamespace(shared_data={})
    assert asyncio.run(orch._decide_for_heal(state, "pl_off", [])) is None
    assert state.shared_data == {}


def test_decide_for_heal_on_mode_records(monkeypatch):
    from clipwright.config import settings
    from clipwright.services import pipeline_v2 as pv
    from clipwright.services.episode_ledger import EpisodeLedger, clear_ledgers, get_ledger

    clear_ledgers()
    monkeypatch.setattr(settings, "supervisor_mode", "on", raising=False)
    ledger = get_ledger("pl_on", persist=False)
    ledger.record(stage="material", decision="pass",
                  degradations=[{"kind": "material_placeholder", "detail": "占位"}])

    orch = object.__new__(pv.PipelineOrchestratorV2)
    state = SimpleNamespace(shared_data={})
    decision = asyncio.run(orch._decide_for_heal(state, "pl_on", []))

    assert decision is not None
    assert decision.strategy == "S4_accept_degraded"
    assert decision.mode == "on"
    assert state.shared_data["attribution"]["primary_stage"] == "material"
    assert len(state.shared_data["decisions"]) == 1
    clear_ledgers()


# ── 派工执行门禁 ────────────────────────────────────────────


def test_execute_order_rejects_non_core_stage():
    from clipwright.services import pipeline_v2 as pv

    orch = object.__new__(pv.PipelineOrchestratorV2)
    order = SimpleNamespace(id="o1", target_stage="some_plugin_agent", issues=[])
    with pytest.raises(ValueError):
        asyncio.run(orch.execute_order(
            SimpleNamespace(shared_data={}), order,
            result_data={}, persona_config={}, plugin=None,
            agent_context=SimpleNamespace(extra_params={}), bus=None,
        ))


def test_execute_order_reuses_run_agent(monkeypatch):
    from clipwright.services import pipeline_v2 as pv

    orch = object.__new__(pv.PipelineOrchestratorV2)
    calls: dict[str, object] = {}

    monkeypatch.setattr(
        pv.PipelineOrchestratorV2, "_build_input",
        staticmethod(lambda name, rd, pc, plugin, extra_params=None: {"built": name}),
    )

    async def fake_run_agent(state, name, input_data, ctx, bus, tracer=None):  # noqa: ANN001
        calls["name"] = name
        calls["input"] = input_data
        calls["order_id"] = ctx.extra_params.get("_order_id")
        return "step"

    # 实例属性赋值（不走描述符绑定，避免 self 混进参数）
    orch._run_agent = fake_run_agent

    order = SimpleNamespace(id="o42", target_stage="edit", issues=[{"severity": "error"}])
    result = asyncio.run(orch.execute_order(
        SimpleNamespace(shared_data={}), order,
        result_data={}, persona_config={}, plugin=None,
        agent_context=SimpleNamespace(extra_params={}), bus=None,
    ))

    assert result == "step"
    assert calls["name"] == "edit"
    assert calls["input"] == {"built": "edit"}
    assert calls["order_id"] == "o42"


# ── 决策端点 ────────────────────────────────────────────────


def test_decisions_endpoint_reports_accepted_degraded():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from clipwright.api import pipeline as pipeline_api
    from clipwright.api import supervisor as supervisor_api

    pipeline_api._pipeline_results["pl_dec"] = {
        "shared_data": {
            "decisions": [{"id": "d1", "strategy": "S4_accept_degraded", "target_stage": ""}],
            "accepted_degraded": True,
        },
    }
    app = FastAPI()
    app.include_router(supervisor_api.router)
    client = TestClient(app)

    body = client.get("/api/pipeline/pl_dec/decisions").json()
    assert body["found"] is True
    assert body["count"] == 1
    assert body["accepted_degraded"] is True
    assert body["decisions"][0]["strategy"] == "S4_accept_degraded"

    pipeline_api._pipeline_results.pop("pl_dec", None)
