"""ResultAssembler（B02）：所有终态唯一出口、needs 归一、报告 I/O 不覆盖原状态。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from clipwright.schema.agent import Need
from clipwright.services import result_assembler as ra


def _base(**kw):
    args = dict(run_id="r1", kind="stage", status="ok", stage="structure",
                summary="完成", task_id="t1", trace_id="tr1")
    args.update(kw)
    return ra.build(**args)


def test_build_derives_reason_and_handoff():
    r = _base(status="ok")
    assert r.terminal_reason == "completed"
    assert r.handoff_summary
    assert r.report_state == "unavailable"

    needs = _base(status="needs",
                  needs=[{"field": "audience", "why": "缺受众", "from_stage": "requirements"}],
                  failure={"category": "input_insufficient"})
    assert needs.terminal_reason == "input_insufficient"
    assert needs.needs[0].field == "audience"
    assert "audience" in needs.handoff_summary


def test_needs_mirrored_from_failure_and_normalized():
    r = _base(status="needs",
              failure={"category": "input_insufficient",
                       "needs": [{"field": "scenes", "why": "剪辑需要分镜",
                                  "from_stage": "structure"}]})
    assert isinstance(r.needs[0], Need)
    assert r.needs[0].resolution == "upstream"


def test_needs_and_redo_mutually_exclusive():
    with pytest.raises(ValidationError):
        _base(status="failed",
              failure={"category": "quality_gate_failed"},
              needs=[{"field": "x", "why": "y"}],
              redo_request={"target_stage": "edit", "source_run_id": "prev",
                            "base_artifact_sha": "sha256:1"})


def test_cost_unknown_semantics():
    assert _base(cost_usd=None).cost_unknown is True
    assert _base(cost_usd=0.0).cost_unknown is False


def test_trace_state_matrix():
    assert _base().trace_state == "empty"
    assert _base(tool_calls=[{"tool": "x"}]).trace_state == "partial"
    assert _base(tool_calls=[{"tool": "x"}], tool_calls_path="p.json").trace_state == "complete"
    assert _base(tool_calls_total=0, tool_calls=[{"tool": "x"}]).trace_state == "empty"


def test_report_io_failure_keeps_terminal_state():
    ok = _base(status="ok")
    degraded = ra.report_io_failure(ok, "PermissionError: denied")
    assert degraded.status == "degraded"
    assert degraded.report_state == "unavailable"
    assert degraded.degradations[-1]["kind"] == "report_io_failed"

    failed = _base(status="failed", failure={"category": "internal_defect"})
    kept = ra.report_io_failure(failed, "disk full")
    assert kept.status == "failed"
    assert kept.terminal_reason == "internal_defect"


def test_dry_run_report_state():
    assert _base(status="dry_run").report_state == "not_applicable"
