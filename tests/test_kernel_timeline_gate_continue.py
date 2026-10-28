"""收尾守门：声称完成但没有 final_timeline 时先"再推一把"（E2E 2026-10-28）。

现象（K:\\demo 一镜到底，Pexels 已配置）：主 agent 派完 structure → material
（候选素材 43 条，0 降级）后自己收尾——结论写着"我读一下确认内容，然后派 edit 阶段"，
然后 loop 结束。守门照实判 `缺少可渲染时间线（final_timeline 为空）` → 任务 failed，
用户拿不到成片。

这类"模型提前收尾"不是数据缺失，靠守门判失败并不能让用户拿到片子。修复后：
守门发现没有 final_timeline 时，带明确指令（edit → animation → audio）再进一轮，
最多 `_MAX_TIMELINE_CONTINUATIONS` 次；仍然出不来才判失败，绝不无限循环。
"""

from __future__ import annotations

import asyncio

import pytest

from clipwright.schema.task import TaskRequest
from clipwright.services import agent_kernel as ak
from clipwright.services import task_blackboard as bb


def _kernel(tmp_path, driver):  # noqa: ANN001, ANN202
    deps = ak.KernelDeps(checkpoint_dir=tmp_path, driver=driver)
    k = ak.AgentKernel(deps=deps)
    ak._TASKS.clear()
    ak._CONTROL.clear()
    ak._TIMELINE_CONTINUATIONS.clear()
    return k


def test_premature_finish_is_continued_with_hint(tmp_path):
    calls: list[str] = []

    async def driver(task, *, context_text="", **kwargs):  # noqa: ANN001, ANN003, ANN201
        calls.append(context_text)
        return {"status": "ok", "finish": {"conclusion": "我读一下确认内容，然后派 edit"},
                "tool_calls": [], "rounds": 1}

    k = _kernel(tmp_path, driver)
    task = k.create(TaskRequest(topic="x", plan_only=False))
    asyncio.run(k.run(task))

    assert len(calls) == ak._MAX_TIMELINE_CONTINUATIONS + 1, "第一次 + 两次继续"
    assert all("收尾守门" not in c for c in calls[:1]), "首次不该带继续指令"
    assert "final_timeline" in calls[1] and "收尾守门" in calls[1], "继续时必须带明确指令"
    assert task.status == "failed"
    assert task.finish.reason == "缺少可渲染时间线（final_timeline 为空）"


def test_timeline_present_finishes_immediately(tmp_path):
    calls: list[str] = []

    async def driver(task, *, context_text="", **kwargs):  # noqa: ANN001, ANN003, ANN201
        calls.append(context_text)
        bb.put(task.id, "final_timeline", {"tracks": [], "duration_sec": 1.0}, by="audio")
        return {"status": "ok", "finish": {"conclusion": "成片已就绪"},
                "tool_calls": [], "rounds": 1}

    k = _kernel(tmp_path, driver)
    task = k.create(TaskRequest(topic="x", plan_only=False))
    asyncio.run(k.run(task))

    assert len(calls) == 1, "已经有时间线就不该再推"
    assert task.status == "done"
    assert task.finish.reason == "tool_loop_completed"


def test_continuation_counter_is_bounded(tmp_path):
    async def driver(task, *, context_text="", **kwargs):  # noqa: ANN001, ANN003, ANN201
        return {"status": "ok", "finish": {"conclusion": "done"}, "tool_calls": [], "rounds": 1}

    k = _kernel(tmp_path, driver)
    task = k.create(TaskRequest(topic="x", plan_only=False))
    asyncio.run(k.run(task))

    assert ak._TIMELINE_CONTINUATIONS.get(task.id) == ak._MAX_TIMELINE_CONTINUATIONS


def test_counter_resets_after_success(tmp_path):
    async def driver(task, *, context_text="", **kwargs):  # noqa: ANN001, ANN003, ANN201
        bb.put(task.id, "final_timeline", {"tracks": []}, by="audio")
        return {"status": "ok", "finish": {"conclusion": "ok"}, "tool_calls": [], "rounds": 1}

    k = _kernel(tmp_path, driver)
    task = k.create(TaskRequest(topic="x", plan_only=False))
    asyncio.run(k.run(task))

    assert task.id not in ak._TIMELINE_CONTINUATIONS


def test_dry_run_is_not_continued(tmp_path):
    calls: list[str] = []

    async def driver(task, *, context_text="", **kwargs):  # noqa: ANN001, ANN003, ANN201
        calls.append(context_text)
        return {"status": "ok", "finish": {"conclusion": "预览完成"},
                "tool_calls": [], "rounds": 1}

    k = _kernel(tmp_path, driver)
    task = k.create(TaskRequest(topic="x", plan_only=False, extra={"dry_run": True}))
    asyncio.run(k.run(task))

    assert len(calls) == 1, "预览模式没有时间线是正常的，不该推"
    assert task.finish.reason == "dry_run_preview"
