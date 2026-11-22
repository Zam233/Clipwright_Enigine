"""AgentResult v2 契约测试（B01 / X01a）。

冻结目标：阶段与调研共用同一 AgentResult(schema_version=2)；
`brief` / `mode` 字段必须在模型里（extra=forbid）；
`dry_run` 是合法终态；needs 与 redo_request 互斥。
"""

from __future__ import annotations

import hashlib
import json
import typing
from pathlib import Path

import pytest
from pydantic import ValidationError

from clipwright.schema.agent import FailureReport, Need, RedoRequest
from clipwright.schema.run import AgentResult, RunSpec, RunStatus

FIXTURES = Path(__file__).resolve().parent / "agent_result_v2"


def _frontend_root() -> Path:
    import os

    raw = os.environ.get("CLIPWRIGHT_FRONTEND_ROOT", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path(__file__).resolve().parents[2].parent / "Clipweight-Client"


FRONTEND_FIXTURES = _frontend_root() / "src/services/api/contracts/agent_result_v2"


def _result(**kwargs) -> AgentResult:
    base = {
        "run_id": "run_test_1",
        "kind": "stage",
        "status": "ok",
        "terminal_reason": "completed",
        "summary": "完成",
    }
    base.update(kwargs)
    return AgentResult(**base)


def test_terminal_matrix(monkeypatch):
    monkeypatch.setenv("CLIPWRIGHT_AGENT_RESULT_STRICT", "1")

    _result()
    _result(status="degraded", degradations=[{"kind": "llm_timeout_fallback"}])
    _result(
        status="needs",
        needs=[Need(field="audience", why="用户未提供", resolution="human")],
        failure={"category": "input_insufficient", "retryable": "needs_upstream"},
    )
    _result(status="failed", failure={"category": "internal_defect"})
    _result(status="refused", failure={"category": "policy_refused"})
    _result(status="cancelled", failure={"category": "cancelled"})
    _result(status="dry_run")

    with pytest.raises(ValidationError):
        _result(status="needs")
    with pytest.raises(ValidationError):
        _result(status="degraded")
    with pytest.raises(ValidationError):
        _result(status="failed")
    with pytest.raises(ValidationError):
        _result(terminal_reason="")


def test_dry_run_is_valid_run_status():
    assert "dry_run" in typing.get_args(RunStatus)
    assert _result(status="dry_run").status == "dry_run"


def test_brief_mode_roundtrip_and_extra_forbid():
    r = _result(brief="给 ZAM 做一条 3 分钟测评", mode="inline")
    assert r.brief.startswith("给 ZAM")
    assert r.mode == "inline"
    assert r.schema_version == 2
    assert RunSpec(mode="task").mode == "task"

    with pytest.raises(ValidationError):
        _result(legacy_wire="still-sent")


def test_false_zero_empty_preserved():
    r = _result(outputs={"flag": False, "count": 0, "items": [], "name": ""})
    assert r.outputs["flag"] is False
    assert r.outputs["count"] == 0
    assert r.outputs["items"] == []
    assert r.outputs["name"] == ""


def test_needs_and_redo_request_are_mutually_exclusive():
    redo = RedoRequest(
        scope="clip:c1",
        target_stage="edit",
        issue_ids=["i1"],
        source_run_id="run_prev",
        base_artifact_sha="sha256:abc",
    )
    with pytest.raises(ValidationError):
        _result(
            status="failed",
            failure={"category": "quality_gate_failed"},
            needs=[Need(field="audience", why="x")],
            redo_request=redo,
        )
    ok = _result(status="failed", failure={"category": "quality_gate_failed"},
                 redo_request=redo)
    assert ok.redo_request.base_artifact_sha == "sha256:abc"


def test_redo_request_requires_source_and_base():
    with pytest.raises(ValidationError):
        RedoRequest(target_stage="edit")


def test_to_tool_payload_projects_v2_fields():
    r = _result(
        brief="brief-x",
        status="needs",
        needs=[Need(field="audience", why="缺受众")],
        failure={"category": "input_insufficient"},
        report_state="unavailable",
        handoff_summary="分镜待补受众后重跑",
        tool_calls_total=7,
        trace_state="partial",
        replay_of="run_old",
    )
    payload = r.to_tool_payload()
    assert payload["schema_version"] == 2
    assert payload.get("brief") in (None, "brief-x")  # payload 允许不带 brief（重放走 spec_ref）
    assert payload["needs"][0]["field"] == "audience"
    assert payload["redo_request"] is None
    assert payload["report_state"] == "unavailable"
    assert payload["handoff_summary"]
    assert payload["tool_calls_total"] == 7
    assert payload["trace_state"] == "partial"
    assert payload["replay_of"] == "run_old"


def test_failure_report_has_no_needs_mirror():
    # B21c：缺项真相只在 AgentResult.needs / StageResult.needs，failure 不再镜像
    assert "needs" not in FailureReport.model_fields


# ── X01a：fixture 快照（必须从模型往返，且双仓同字节） ──────────────────


def test_snapshot_roundtrip():
    paths = sorted(FIXTURES.glob("*.json"))
    assert len(paths) >= 8, "fixture 至少覆盖七终态 + brief 空串"
    for path in paths:
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw)
        result = AgentResult.model_validate(payload)
        assert result.schema_version == 2
        for key in ("status", "terminal_reason", "brief", "mode", "report_state",
                    "handoff_summary", "trace_state", "tool_calls_total"):
            assert key in payload, f"{path.name} 缺键 {key}"
        dumped = json.dumps(result.model_dump(mode="json"), ensure_ascii=False,
                            indent=2, sort_keys=True) + "\n"
        assert dumped == raw, f"{path.name} 往返不稳定（fixture 必须是模型生成）"


def test_fixtures_cover_all_terminal_statuses():
    statuses = {
        json.loads(p.read_text(encoding="utf-8"))["status"]
        for p in FIXTURES.glob("*.json")
        if p.name != "brief_empty.json"
    }
    assert set(typing.get_args(RunStatus)) <= statuses


def test_fixture_false_zero_empty_survive():
    payload = json.loads((FIXTURES / "ok.json").read_text(encoding="utf-8"))
    assert payload["outputs"]["mg_dynamic"] is False
    assert payload["outputs"]["warnings"] == 0
    assert payload["outputs"]["dropped"] == []


def test_brief_key_exists_even_when_empty():
    payload = json.loads((FIXTURES / "brief_empty.json").read_text(encoding="utf-8"))
    assert "brief" in payload and payload["brief"] == ""


def test_frontend_fixtures_identical_when_present():
    if not FRONTEND_FIXTURES.is_dir():
        pytest.skip("前端仓不在本机；X01b 在双仓联验时断言同字节")
    for path in sorted(FIXTURES.glob("*.json")):
        mirror = FRONTEND_FIXTURES / path.name
        assert mirror.is_file(), f"前端缺 fixture {path.name}"
        assert (hashlib.sha256(path.read_bytes()).hexdigest()
                == hashlib.sha256(mirror.read_bytes()).hexdigest()), f"{path.name} 双仓不同字节"
