"""quality_report：读取最近 quality 报告，缺失时明确报错。"""

from __future__ import annotations

import asyncio

from clipwright.tool.quality_report import QualityReportTool


def test_quality_report_latest(tmp_path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "1-quality.md").write_text("old", encoding="utf-8")
    (reports / "2-quality.md").write_text("new report", encoding="utf-8")
    out = asyncio.run(QualityReportTool().execute(workspace_root=str(tmp_path)))
    assert str(out.status) == "success"
    assert out.output["name"] == "2-quality.md"
    assert "new report" in out.output["content"]


def test_quality_report_missing(tmp_path) -> None:
    out = asyncio.run(QualityReportTool().execute(workspace_root=str(tmp_path)))
    assert str(out.status) == "not_found"
    assert "quality" in out.error


def test_quality_report_reads_blackboard_issues() -> None:
    from clipwright.services import task_blackboard as bb

    tid = "task_qreport_board"
    bb.reset(tid)
    bb.put(tid, "issues", [{"severity": "warning", "category": "audio",
                            "message": "没有音频轨"}])
    try:
        out = asyncio.run(QualityReportTool().execute(task_id=tid))
        assert out.output["source"] == "blackboard"
        assert out.output["passed"] is False
        assert out.output["issues"]
    finally:
        bb.reset(tid)
