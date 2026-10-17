"""B6：关闭开关后的行为等价性（PRD A10）与成功路径零成本（A5 的机制保证）。

A10 的验收方式是「同输入产出 timeline 逐字节比对」（需真实管线，属人工/端到端）；
这里固化**机制层**的等价性：`supervisor_mode=off` 时

1. 不做模型路由（即使配了 model_routes）；
2. 不做调度决策、不调 LLM、不写 decisions；
3. 不写归因/诊断书；
4. 账本照记（B1 是旁路观测，不影响执行）。

A5 的「成功路径成本增量=0」由第 2、3 条保证：off 模式下没有新增任何 LLM 调用。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from clipwright.config import settings
from clipwright.services import pipeline_v2 as pv
from clipwright.services.episode_ledger import clear_ledgers, get_ledger


@pytest.fixture(autouse=True)
def _off_mode(monkeypatch):
    monkeypatch.setattr(settings, "supervisor_mode", "off", raising=False)
    monkeypatch.setattr(settings, "model_routes", {
        "edit": [{"provider": "p", "model": "routed", "tier": "std", "price_in": 1}],
    }, raising=False)
    clear_ledgers()
    yield
    clear_ledgers()


def _orch() -> pv.PipelineOrchestratorV2:
    return object.__new__(pv.PipelineOrchestratorV2)


def test_off_mode_disables_model_routing():
    """即使配置了路由表，off 模式也不换模型（A10）。"""
    orch = _orch()
    assert orch._choose_route_for("edit", "pl_off") is None


def test_off_mode_skips_supervisor_decision():
    """off 模式不做决策（因此没有额外 LLM 调用 → A5 的零增量）。"""
    orch = _orch()
    state = SimpleNamespace(shared_data={})
    assert asyncio.run(orch._decide_for_heal(state, "pl_off", [])) is None
    assert state.shared_data == {}


def test_off_mode_skips_shadow_analysis():
    orch = _orch()
    state = SimpleNamespace(shared_data={}, steps=[])
    asyncio.run(orch._shadow_analyze(state, "pl_off"))
    assert state.shared_data == {}


def test_off_mode_still_records_ledger():
    """账本是旁路观测：off 模式照记，但不改变执行。"""
    ledger = get_ledger("pl_off", persist=False)
    ledger.record(stage="edit", decision="pass", duration_ms=5)
    assert ledger.attempts("edit") == 1
    assert ledger.summary().startswith("# 管线 pl_off")


def test_off_mode_is_default_configuration():
    from clipwright.config import Settings

    assert Settings.model_fields["supervisor_mode"].default == "off"


def test_empty_route_table_means_no_routing_even_when_on(monkeypatch):
    """on 模式下若没配路由表，同样不换模型（改造前后行为一致）。"""
    monkeypatch.setattr(settings, "supervisor_mode", "on", raising=False)
    monkeypatch.setattr(settings, "model_routes", {}, raising=False)
    assert _orch()._choose_route_for("edit", "pl_on") is None


def test_success_path_has_no_llm_call_in_off_mode(monkeypatch):
    """off 模式下归因/诊断都不该触达 LLM——反证：把 LLM 打桩成会抛错。"""

    async def boom(self, **kwargs):  # noqa: ANN001
        raise AssertionError("off 模式不应调用 LLM")

    from clipwright.services import llm as llm_mod

    monkeypatch.setattr(llm_mod.LLMService, "structured_output", boom)

    # 归因（规则）与诊断（模板）都是纯函数，不应触发 LLM
    from clipwright.services.attribution import attribute
    from clipwright.services.diagnosis import build_diagnosis

    ledger = get_ledger("pl_off", persist=False)
    ledger.record(stage="material", decision="pass",
                  degradations=[{"kind": "material_placeholder", "detail": "占位"}])
    attr = attribute(ledger, issues=[])
    report = build_diagnosis(ledger, attribution=attr)
    assert attr.primary_stage == "material"
    assert report.conclusion


def test_budget_snapshot_has_no_side_effects(monkeypatch):
    """A5 的另一半：没有调用记录时，成本统计是零、且不抛错。"""
    from clipwright.services import budget_guard

    snap = budget_guard.snapshot("pl_no_calls")
    assert snap["spent_usd"] == 0
    assert snap["tokens"] == 0
    assert snap["level"] == "unlimited"
