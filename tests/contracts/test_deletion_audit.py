"""B21：计划内删除项的最小回归（删掉的东西不许复活）。"""

from __future__ import annotations

import inspect


def test_kernel_emit_report_dead_code_removed():
    from clipwright.services import agent_kernel

    assert not hasattr(agent_kernel, "_emit_report"), "旧汇报卡死代码不应复活"


def test_stage_run_mirror_removed():
    from clipwright.services import stage_subagent

    assert not hasattr(stage_subagent, "_RUNS"), "阶段内存运行表已删除（RunStore 唯一真相）"
    assert not hasattr(stage_subagent, "list_stages")
    assert not hasattr(stage_subagent, "import_runs")


def test_stage_result_model_removed():
    from clipwright import schema

    assert not hasattr(schema.stage, "StageResult"), "两套终态并存已收口为 AgentResult"


def test_stage_report_has_no_fixed_section_parser():
    from clipwright.services import stage_report

    assert not hasattr(stage_report, "_section_text"), "固定四段解析已删除"
    assert not hasattr(stage_report, "next_for"), "next_for 已删除"


def test_read_report_rejects_section_param():
    import asyncio

    from clipwright.schema.tool import ToolStatus
    from clipwright.tool.reports import ReadReportTool

    out = asyncio.run(ReadReportTool().execute(section="结论"))
    assert out.status == ToolStatus.ERROR
    assert "section" in str(out.error)


def test_run_store_is_the_only_stage_run_store():
    from clipwright.services import run_store

    for fn in ("save", "get_for_task", "list_task", "lookup_idem", "bind_idem"):
        assert hasattr(run_store, fn), f"run_store 缺 {fn}"


def test_quality_issues_implicit_injection_removed():
    from clipwright.agents import base

    assert not hasattr(base.BaseAgent, "quality_issues_hint"), "隐式质检注入已删除"
