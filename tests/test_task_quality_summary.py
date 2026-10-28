"""B8：Task 路径的质检摘要（closeout plan B8 / C14）。

老管线把质检结果塞进结果字典的 `quality_issues`，Task 路径没有等价物 →
前端质检页对新产品线永远空。这里钉住三件事：

1. 摘要从**黑板**（`issues` / `redo_agent`）算，不从某次 run 的返回值算
   —— 质检是任务级事实；
2. 收尾时写进 `task_results` 快照，`/api/task/{id}/result` 直接能读到；
3. 老管线的两个死 helper（`_extract_quality_summary` / `_aggregate_agent_notes`）
   已删除——同一份语义不允许有两个实现。
"""

from __future__ import annotations

import asyncio

import pytest

from clipwright.schema.task import TaskRequest
from clipwright.services import agent_kernel as ak
from clipwright.services import task_blackboard as bb
from clipwright.services import task_results


class _Driver:
    def __init__(self, *, issues: list[dict] | None = None, redo: str = "") -> None:
        self.issues = issues or []
        self.redo = redo

    async def __call__(self, task, *, context_text="", shadow=False,  # noqa: ANN001
                       guard=None, on_tool_call=None, dry_run=False):  # noqa: ANN001
        if self.issues:
            bb.put(task.id, "issues", self.issues, by="quality")
        if self.redo:
            bb.put(task.id, "redo_agent", self.redo, by="quality")
        # 让收尾守门通过（有可渲染时间线才算完成）
        bb.put(task.id, "final_timeline", {"duration_sec": 5.0, "tracks": []}, by="audio")
        return {"status": "ok", "finish": {"conclusion": "出片"},
                "tool_calls": [], "rounds": 1, "stopped_reason": "model_final"}


@pytest.fixture(autouse=True)
def _clean():
    ak.reset_kernel_state()
    task_results.clear_results()
    yield
    ak.reset_kernel_state()
    task_results.clear_results()


def test_quality_summary_shape():
    summary = task_results.quality_summary({
        "issues": [
            {"kind": "placeholder_clip", "severity": "error", "detail": "占位图", "clip_id": "c1"},
            {"kind": "subtitle_overflow", "severity": "warn", "detail": "字幕超框"},
        ],
        "redo_agent": "edit",
    })
    assert summary["count"] == 2
    assert summary["by_severity"] == {"error": 1, "warn": 1}
    assert summary["redo_agent"] == "edit"
    assert summary["items"][0]["clip_id"] == "c1"


def test_quality_summary_empty_when_no_issues():
    assert task_results.quality_summary({"issues": []}) == {}
    assert task_results.quality_summary(None) == {}


def test_finish_writes_snapshot_with_quality(tmp_path):
    """收尾必须把质检摘要写进快照，否则前端质检页还是空的。"""
    kernel = ak.AgentKernel(ak.KernelDeps(
        driver=_Driver(issues=[{"kind": "placeholder_clip", "severity": "error",
                                "detail": "占位图"}], redo="material"),
        checkpoint_dir=tmp_path / "tasks"))
    task = kernel.create(TaskRequest(topic="x", plan_only=False))
    task = asyncio.run(kernel.run(task))

    snap = task_results.last_result(task.id)
    assert snap is not None
    assert snap["timeline"], "快照要带时间线（前端出片/编辑器都读它）"
    assert snap["quality_issues"]["count"] == 1
    assert snap["quality_issues"]["redo_agent"] == "material"


def test_snapshot_falls_back_to_blackboard(tmp_path):
    """没有显式快照时 `last_result` 回落到黑板（同一份事实源）。"""
    bb.put("t_fallback", "final_timeline", {"duration_sec": 1.0, "tracks": []}, by="audio")
    snap = task_results.last_result("t_fallback")
    assert snap is not None and snap["source"] == "blackboard"


def test_legacy_quality_helpers_deleted():
    """同一份语义不允许两份实现：老管线的 helper 应已删除。"""
    from clipwright.api import pipeline as pipeline_api

    assert not hasattr(pipeline_api, "_extract_quality_summary")
    assert not hasattr(pipeline_api, "_aggregate_agent_notes")
