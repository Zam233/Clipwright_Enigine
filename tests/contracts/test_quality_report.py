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
