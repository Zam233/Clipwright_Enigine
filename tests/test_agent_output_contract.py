"""F9 失败回报契约（PRD v0.2）— 单元测试。

覆盖：
- 6 个执行型输出的「执行失败」必须携带 FailureReport；
- 质检的「判定失败」不要求 FailureReport（它不是执行故障）；
- 宽松模式自动补 unknown 并计数；严格模式直接抛错；
- 降级登记作用域与 llm_or_fallback 接线；
- 后处理（类型 transform）降级登记。
"""

from __future__ import annotations

import asyncio

import pytest

from clipwright.agents.base import BaseAgent
from clipwright.schema.agent import (
    AgentDecision,
    AnimationOutput,
    AudioOutput,
    EditOutput,
    MaterialOutput,
    QualityOutput,
    RequirementsOutput,
    StructureOutput,
    agent_contract_stats,
)
from clipwright.services.degradation import degradation_scope, note_degradation

EXECUTION_OUTPUTS = [
    StructureOutput,
    MaterialOutput,
    EditOutput,
    AnimationOutput,
    AudioOutput,
    RequirementsOutput,
]


class _DummyAgent(BaseAgent):
    agent_name = "dummy"

    async def execute(self, input_data, context):  # pragma: no cover - 不直接调用
        raise NotImplementedError


def test_build_error_output_fills_failure_report():
    """6 个 Agent 共用的失败构造必须带齐 FailureReport。"""
    agent = _DummyAgent()
    for cls in EXECUTION_OUTPUTS:
        out = agent.build_error_output("boom", cls)
        assert out.decision == AgentDecision.FAIL
        assert out.failure is not None, f"{cls.__name__} 的失败输出缺 FailureReport"
        assert out.failure.category == "internal_defect"
        assert "boom" in out.failure.blocking_point
        assert out.failure.retryable in ("retry_same", "needs_upstream", "needs_human", "permanent")


def test_quality_judgment_fail_keeps_failure_none():
    """质检判「片子不合格」不是执行故障，不该被要求交失败回报。"""
    out = QualityOutput(decision=AgentDecision.FAIL, passed=False)
    assert out.decision == AgentDecision.FAIL
    assert out.failure is None


def test_execution_fail_without_failure_is_autofilled_in_lenient_mode():
    before = agent_contract_stats()["unknown_failure_total"]
    out = QualityOutput(decision=AgentDecision.FAIL, error="llm exploded")
    assert out.failure is not None
    assert out.failure.category == "unknown"
    assert agent_contract_stats()["unknown_failure_total"] == before + 1


def test_strict_mode_raises_for_missing_failure(monkeypatch):
    monkeypatch.setenv("CLIPWRIGHT_AGENT_CONTRACT_STRICT", "1")
    with pytest.raises(Exception):
        QualityOutput(decision=AgentDecision.FAIL, error="llm exploded")


def test_degradation_scope_collects_entries():
    sink = []
    with degradation_scope(sink):
        note_degradation("llm_timeout_fallback", detail="x", scope="material",
                         impact="用模板顶上")
    assert len(sink) == 1
    assert sink[0].kind == "llm_timeout_fallback"
    assert sink[0].scope == "material"
    assert "模板" in sink[0].impact


def test_degradation_outside_scope_does_not_raise():
    """没有活跃作用域时只记日志，不能因为登记而炸掉执行。"""
    item = note_degradation("legacy_point", detail="no scope")
    assert item.kind == "legacy_point"


def test_llm_or_fallback_records_timeout_degradation():
    agent = _DummyAgent()

    async def _boom():
        raise asyncio.TimeoutError()

    sink = []
    with degradation_scope(sink):
        value = asyncio.run(agent.llm_or_fallback(_boom, "FALLBACK", timeout=0.01))
    assert value == "FALLBACK"
    assert any(d.kind == "llm_timeout_fallback" for d in sink)


def test_finalize_timeline_records_transform_degradation():
    """类型插件后处理失败保留原时间线，但必须登记降级。"""
    from clipwright.services.pipeline_v2 import PipelineOrchestratorV2

    class _FakePlugin:
        plugin_id = "fake_type"

        def post_process_timeline(self, timeline):
            raise RuntimeError("transform boom")

    class _Ctx:
        # 关掉署名轨后处理，本用例只验 transform 失败路径
        extra_params: dict = {"credit_outro": False}

    orchard = object.__new__(PipelineOrchestratorV2)
    ctx = _Ctx()
    tl = {"tracks": []}
    out = PipelineOrchestratorV2._finalize_timeline(orchard, tl, _FakePlugin(), ctx, "pl_test")

    assert out == tl, "后处理失败时必须保留原时间线"
    recorded = ctx.extra_params.get("_finalize_degradations") or []
    assert recorded, "后处理失败必须登记降级（否则占位/未生效被当成正常交付）"
    assert recorded[0]["kind"] == "plugin_transform_failed"
