"""A2：录制 → 回放（M3 后的形态）。

M3 删掉了计划表执行，录制点从 `_run_step` 移到 tool loop 的每次工具调用
（`_on_tool_call` → `maybe_record("tool", ...)`）。回放驱动器直接拿录制里的工具结果，
不再需要"回放规划器 + 回放执行器"这对计划表玩具。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from clipwright.schema.task import Plan, PlanStep, TaskRequest
from clipwright.services import agent_kernel as ak
from clipwright.services import recording as rec
from clipwright.services import task_events as te

CALLS = [("media_probe", {"input_path": "a.mp4"}),
         ("media_probe", {"input_path": "b.mp4"})]


class RecordingDriver:
    """像模型一样依次调用两次工具（真实结果）。"""

    def __init__(self, calls: list[tuple[str, dict[str, Any]]] | None = None) -> None:
        self.calls = calls or CALLS
        self.served = 0

    async def __call__(self, task, *, context_text="", shadow=False, dry_run=False,  # noqa: ANN001
                       guard=None, on_tool_call=None):
        for name, args in self.calls:
            if guard is not None and guard():
                return {"status": "aborted", "stopped_reason": "aborted",
                        "tool_calls": []}
            result = {"status": "ok",
                      "output": {"duration_sec": 12.5, "frames": 300,
                                 "path": f"{args['input_path']}"}}
            self.served += 1
            if on_tool_call is not None:
                # 真实 agent_loop 会把**调用参数**一并回调，录制才按参数建键；
                # 假驱动器也必须这样，否则回放找键必然 miss。
                on_tool_call(name, result, args)
        return {"status": "ok", "finish": {"conclusion": "两次探测完成"},
                "tool_calls": [{"tool": c[0]} for c in self.calls],
                "rounds": len(self.calls) + 1, "stopped_reason": "model_final"}


class ReplayDriver:
    """回放驱动器：工具结果全部取自录制，缺录制就**响亮失败**。"""

    def __init__(self, replayer: rec.Replayer) -> None:
        self.replayer = replayer

    async def __call__(self, task, *, context_text="", shadow=False, dry_run=False,  # noqa: ANN001
                       guard=None, on_tool_call=None):
        for name, args in CALLS:
            result = self.replayer.result_of("tool", name, args)  # 缺了会抛 RecordingMiss
            if on_tool_call is not None:
                on_tool_call(name, result, args)
        return {"status": "ok", "finish": {"conclusion": "回放完成"},
                "tool_calls": [{"tool": "media_probe"}], "rounds": 3,
                "stopped_reason": "model_final"}


@pytest.fixture(autouse=True)
def _clean():
    ak.reset_kernel_state()
    te.clear()
    yield
    ak.reset_kernel_state()
    te.clear()


def _kernel(tmp_path, driver=None) -> ak.AgentKernel:
    return ak.AgentKernel(ak.KernelDeps(
        driver=driver or RecordingDriver(),
        checkpoint_dir=tmp_path / "tasks",
    ))


def test_recorded_run_can_be_replayed_offline(tmp_path):
    """A2：录一次 → 用录制重演 → 工具结果与关键数值结构一致。"""
    rec_path = tmp_path / "run.jsonl"

    # 1) 真跑并录制
    with rec.recording_scope(rec.Recorder(rec_path)):
        k1 = _kernel(tmp_path)
        task1 = k1.create(TaskRequest(topic="做一个 30 秒视频", plan_only=False))
        task1 = asyncio.run(k1.run(task1))

    assert task1.status in ("done", "failed")
    assert rec_path.exists()

    # 2) 用录制重演（不联网、不跑工具）
    ak.reset_kernel_state()
    te.clear()
    replayer = rec.Replayer(rec_path)
    k2 = _kernel(tmp_path, driver=ReplayDriver(replayer))
    task2 = k2.create(TaskRequest(topic="做一个 30 秒视频", plan_only=False))
    task2 = asyncio.run(k2.run(task2))

    assert task2.usage.tool_calls == task1.usage.tool_calls
    assert task2.context_digest[0]["facts"] == task1.context_digest[0]["facts"]
    assert task2.status == task1.status


def test_replay_miss_is_loud_not_silent(tmp_path):
    """录制不完整时回放必须报错，不允许"顺手真跑一次"。"""
    rec_path = tmp_path / "partial.jsonl"
    with rec.recording_scope(rec.Recorder(rec_path)):
        k1 = _kernel(tmp_path)
        task1 = k1.create(TaskRequest(topic="只规划", plan_only=True))

        async def fake_plan(self, task):  # noqa: ANN001, ARG001
            return Plan(steps=[PlanStep(id="p1", capability="media_probe")])

        # 只录"假设"，不录任何工具调用
        orig = ak.LLMPlanner.plan
        ak.LLMPlanner.plan = fake_plan  # type: ignore[method-assign]
        try:
            asyncio.run(k1.plan(task1))
        finally:
            ak.LLMPlanner.plan = orig  # type: ignore[method-assign]

    ak.reset_kernel_state()
    te.clear()
    k2 = _kernel(tmp_path, driver=ReplayDriver(rec.Replayer(rec_path)))
    task2 = k2.create(TaskRequest(topic="只规划", plan_only=False))
    task2 = asyncio.run(k2.run(task2))

    assert task2.status == "failed"
    # 缺失必须是响的：原因里能读到 RecordingMiss，而不是"任务莫名结束"
    text = (task2.finish.reason if task2.finish else "") + str(
        task2.finish.unfinished if task2.finish else "")
    assert "RecordingMiss" in text


def test_maybe_record_is_noop_without_scope(tmp_path):
    """生产默认路径零开销：没有录制作用域时不写文件、不报错。"""
    rec.maybe_record("tool", "media_probe", {"a": 1}, {"status": "ok"})
    assert rec.current_recorder() is None


def test_recording_scope_is_isolated_between_tasks(tmp_path):
    """并发跑两条任务时录制不能串（ContextVar 隔离）。"""
    path_a = tmp_path / "a.jsonl"
    path_b = tmp_path / "b.jsonl"
    with rec.recording_scope(rec.Recorder(path_a)):
        rec.maybe_record("tool", "media_probe", {"n": "a"}, {"status": "ok"})
    with rec.recording_scope(rec.Recorder(path_b)):
        rec.maybe_record("tool", "media_probe", {"n": "b"}, {"status": "ok"})
    assert rec.Replayer(path_a).result_of("tool", "media_probe", {"n": "a"})["status"] == "ok"
    assert rec.Replayer(path_b).result_of("tool", "media_probe", {"n": "b"})["status"] == "ok"
    with pytest.raises(rec.RecordingMiss):
        rec.Replayer(path_a).result_of("tool", "media_probe", {"n": "zzz"})
