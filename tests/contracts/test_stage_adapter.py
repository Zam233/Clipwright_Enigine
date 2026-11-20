"""B03：唯一阶段 Adapter 契约测试。

- 早退/正常结束都返回 AgentResult；
- `dry_run` 不再被折成 `ok`；
- 全仓只有一个阶段 Adapter 符号（`stage_subagent.run_stage` 仅窄转发）。
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from clipwright.schema.run import RunSpec
from clipwright.schema.stage import make_stage_result
from clipwright.services import stage_adapter, stage_subagent

ROOT = Path(__file__).resolve().parents[2] / "clipwright"


def _task():
    return SimpleNamespace(id="task_b03")


@pytest.mark.asyncio
async def test_adapter_preserves_dry_run(monkeypatch):
    async def fake_execute(task, spec):
        return make_stage_result(stage=str(spec.stage), status="dry_run",
                           summary="装配通过，未执行")

    monkeypatch.setattr(stage_subagent, "run_stage", fake_execute)
    result = await stage_adapter.run_stage(
        RunSpec(kind="stage", stage="structure"), _task(), "run_b03_dry", "trace_b03")
    assert result.status == "dry_run"
    assert result.terminal_reason == "validated_only"
    assert result.report_state == "not_applicable"


@pytest.mark.asyncio
async def test_adapter_maps_needs_and_failure(monkeypatch):
    async def fake_execute(task, spec):
        return make_stage_result(
            stage="edit", status="failed", error="缺 candidate_clips",
            summary="剪辑缺输入",
            needs=[{"field": "candidate_clips", "why": "剪辑需要候选素材",
                    "from_stage": "material", "resolution": "upstream"}],
            failure={"category": "input_insufficient", "retryable": "needs_upstream",
                     "blocking_point": "缺 candidate_clips"},
        )

    monkeypatch.setattr(stage_subagent, "run_stage", fake_execute)
    result = await stage_adapter.run_stage(
        RunSpec(kind="stage", stage="edit"), _task(), "run_b03_needs", "trace_b03")
    assert result.status == "failed"
    assert result.terminal_reason == "input_insufficient"
    assert result.needs and result.needs[0].field == "candidate_clips"
    assert "candidate_clips" in result.handoff_summary


@pytest.mark.asyncio
async def test_adapter_missing_stage_refused():
    result = await stage_adapter.run_stage(RunSpec(kind="stage"), _task(), "r1", "t1")
    assert result.status == "refused"
    assert result.terminal_reason == "input_insufficient"


@pytest.mark.asyncio
async def test_inline_returns_agent_result(monkeypatch):
    from clipwright.schema.agent import StructureOutput

    async def fake_inline(stage, input_obj, context, timeout_sec=None):
        return StructureOutput(scenes=[{"id": "s1", "duration": 10.0}])

    monkeypatch.setattr(stage_subagent, "run_inline", fake_inline)
    ctx = SimpleNamespace(pipeline_id="task_b03")
    result = await stage_adapter.run_inline("structure", object(), ctx)
    assert result.status == "ok"
    assert result.mode == "inline"
    assert result.outputs["scenes"][0]["id"] == "s1"


def test_requirements_factory_reachable():
    """B03：requirements 必须能经同一工厂实例化（不再由 stage_subagent 特判排除）。"""
    agent = stage_subagent.agent_for("requirements")
    assert getattr(agent, "agent_name", "") == "requirements_agent"


def test_single_stage_adapter_symbol():
    """只允许两个符号：Adapter（唯一入口）+ 领域执行（真跑阶段，不是第二个 Adapter）。"""
    allowed = {"stage_adapter.py", "stage_subagent.py"}
    found: list[tuple[str, str]] = []
    for path in ROOT.rglob("*.py"):
        if "__pycache__" in str(path):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "run_stage":
                doc = ast.get_docstring(node) or ""
                found.append((path.name, doc))
    files = {name for name, _ in found}
    assert files <= allowed, f"出现额外 run_stage：{files - allowed}"
    impls = [doc for name, doc in found if name == "stage_subagent.py"]
    assert impls and any("真跑一个阶段" in doc for doc in impls), "领域执行必须保留且带说明"
    adapters = [doc for name, doc in found if name == "stage_adapter.py"]
    assert adapters, "缺唯一 Adapter"
