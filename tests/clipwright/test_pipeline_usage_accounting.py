"""V38 用量入账单测：一次运行的 token 总账与渲染耗时。

只测纯聚合函数 + 运行记录接线，不跑真管线。
"""

from __future__ import annotations

from datetime import datetime, timezone

from clipwright.schema.pipeline import PipelineStatus, PipelineStep
from clipwright.services import pipeline_v2


def _step(name: str, *, usage: dict | None = None, duration_ms: int = 0,
          status: PipelineStatus = PipelineStatus.COMPLETED) -> PipelineStep:
    return PipelineStep(
        agent_name=name,
        status=status,
        duration_ms=duration_ms,
        result={"llm_usage": usage} if usage else {},
    )


class TestUsageTotals:
    def test_sums_tokens_across_agents(self) -> None:
        steps = [
            _step("structure", usage={"input_tokens": 100, "output_tokens": 40, "calls": 1}),
            _step("edit", usage={"input_tokens": 200, "output_tokens": 60, "calls": 2}),
        ]
        totals = pipeline_v2._usage_totals_from_steps(steps)
        assert totals["input_tokens"] == 300
        assert totals["output_tokens"] == 100
        assert totals["total_tokens"] == 400
        assert totals["calls"] == 3
        assert set(totals["by_agent"]) == {"structure", "edit"}

    def test_agent_without_usage_contributes_zero(self) -> None:
        totals = pipeline_v2._usage_totals_from_steps([_step("material")])
        assert totals["total_tokens"] == 0
        assert totals["by_agent"] == {}

    def test_render_sec_from_render_step(self) -> None:
        steps = [_step("render", duration_ms=12_500)]
        assert pipeline_v2._usage_totals_from_steps(steps)["render_sec"] == 12.5

    def test_render_sec_zero_when_no_render_step(self) -> None:
        assert pipeline_v2._usage_totals_from_steps([_step("edit", duration_ms=900)])["render_sec"] == 0.0

    def test_empty_steps_safe(self) -> None:
        totals = pipeline_v2._usage_totals_from_steps([])
        assert totals["total_tokens"] == 0 and totals["render_sec"] == 0.0

    def test_negative_tokens_clamped(self) -> None:
        steps = [_step("x", usage={"input_tokens": -5, "output_tokens": 3})]
        totals = pipeline_v2._usage_totals_from_steps(steps)
        assert totals["input_tokens"] == 0
        assert totals["total_tokens"] == 3


class TestRunRecordUsage:
    def test_completed_run_carries_usage(self) -> None:
        pipeline_v2.clear_run_records()
        pipeline_id = "pl_usage_test"
        pipeline_v2.record_run_start(pipeline_id, "演示")
        steps = [
            _step("structure", usage={"input_tokens": 10, "output_tokens": 5, "calls": 1}),
            _step("render", duration_ms=4000),
        ]
        pipeline_v2.record_run_complete(pipeline_id, "completed", steps)
        records = pipeline_v2.get_run_records(limit=5)
        record = next(r for r in records if r["id"] == pipeline_id)
        assert record["usage"]["total_tokens"] == 15
        assert record["usage"]["render_sec"] == 4.0
        assert record["usage"]["by_agent"]["structure"]["input_tokens"] == 10
        pipeline_v2.clear_run_records()

    def test_record_without_start_still_has_usage(self) -> None:
        pipeline_v2.clear_run_records()
        pipeline_v2.record_run_complete(
            "pl_usage_orphan", "completed",
            [_step("structure", usage={"input_tokens": 1, "output_tokens": 1})],
        )
        record = next(r for r in pipeline_v2.get_run_records(limit=5) if r["id"] == "pl_usage_orphan")
        assert record["usage"]["total_tokens"] == 2
        pipeline_v2.clear_run_records()
