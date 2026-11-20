"""B06：报告自由正文 + 最小机器信封 + 原子写盘 + report_state 推导。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from clipwright.schema.run import RunSpec
from clipwright.schema.stage import make_stage_result
from clipwright.services import stage_adapter, stage_report, stage_subagent


def test_body_is_verbatim_free_markdown(tmp_path):
    body = ("## 我的标题\n\n"
            "第一段：候选 0 条。\n\n"
            "```json\n{\"k\": \"中文/引用\"}\n```\n\n"
            "最后一段：先补素材再剪。")
    path, seq = stage_report.write_report(
        workspace_root=tmp_path, stage="material", status="ok", attempt=1,
        body=body, run_id="run_free_2", task_id="task_free_2")
    assert seq == 1
    text = Path(path).read_text(encoding="utf-8")
    assert body in text
    # 不再有固定四节外壳
    for title in ("我做了什么", "结果如何", "发现的问题", "给下一位"):
        assert f"## {title}" not in text


def test_frontmatter_minimal_and_machine_envelope(tmp_path):
    path, _ = stage_report.write_report(
        workspace_root=tmp_path, stage="edit", status="degraded", attempt=2,
        body="正文", run_id="run_env", task_id="task_env", cost_usd=0.5)
    text = Path(path).read_text(encoding="utf-8")
    head = text.split("---")[1]
    for key in ("schema_version", "stage", "status", "attempt", "seq",
                "task_id", "run_id", "cost_usd", "artifacts", "created_at"):
        assert f"{key}:" in head
    assert "run_id: \"run_env\"" in head
    assert "schema_version: 2" in head


def test_write_is_atomic_no_tmp_left(tmp_path):
    stage_report.write_report(workspace_root=tmp_path, stage="edit", status="ok",
                              attempt=1, body="原子写")
    reports = tmp_path / "reports"
    assert list(reports.glob("*.tmp")) == []
    assert list(reports.glob("*.md"))


def test_write_failure_returns_empty(tmp_path, monkeypatch):
    def _boom(*_a, **_kw):
        raise OSError("disk full")

    monkeypatch.setattr(stage_report.os, "replace", _boom)
    path, seq = stage_report.write_report(workspace_root=tmp_path, stage="edit",
                                          status="ok", attempt=1, body="x")
    assert path == "" and seq == 0


def test_list_reports_uses_body_preview_not_sections(tmp_path):
    stage_report.write_report(workspace_root=tmp_path, stage="material", status="ok",
                              attempt=1, body="候选 0 条。素材库为空。先补素材再剪。")
    items = stage_report.list_reports(tmp_path)
    assert items and "先补素材再剪" in items[0]["preview"]
    assert "next_for" not in items[0]
    assert items[0]["run_id"] == "" and items[0]["task_id"] == ""


@pytest.mark.asyncio
async def test_read_report_pagination_and_section_rejected(tmp_path, monkeypatch):
    from clipwright.config import settings
    from clipwright.services.workspace import Workspace
    from clipwright.tool.reports import ReadReportTool

    monkeypatch.setattr(settings, "workspace_dir", tmp_path / "ws")
    monkeypatch.setattr(settings, "project_dir", tmp_path / "projects")
    ws = Workspace("proj_read_paging")
    root = str(ws.dirs().root)
    stage_report.write_report(workspace_root=root, stage="edit", status="ok",
                              attempt=1, body="A" * 600)
    first = await ReadReportTool().execute(workspace_root=root, seq=1, max_chars=200)
    out = first.output
    assert out["total_chars"] > 200 and out["truncated"] is True
    assert out["next_offset"] == 200

    second = await ReadReportTool().execute(workspace_root=root, seq=1, max_chars=200,
                                            offset=out["next_offset"])
    assert second.output["offset"] == 200

    rejected = await ReadReportTool().execute(workspace_root=root, seq=1, section="结果如何")
    assert rejected.error and "section 参数已移除" in str(rejected.error)


@pytest.mark.asyncio
async def test_adapter_derives_report_state_from_path(monkeypatch):
    async def fake_stage(task, spec):
        return make_stage_result(stage=str(spec.stage), status="ok", summary="ok",
                           report_path="reports/01-structure-1.md", report_seq=1)

    monkeypatch.setattr(stage_subagent, "run_stage", fake_stage)
    result = await stage_adapter.run_stage(
        RunSpec(kind="stage", stage="structure"),
        SimpleNamespace(id="task_report"), "run_r1", "trace_r1")
    assert result.report_state == "written"
    assert result.report_seq == 1
