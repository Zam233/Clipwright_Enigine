"""C2：录制回放接入 Kernel（A2 的可验证形式）。

C 放弃了「同输入→逐字节同输出」。所以要证明"这次改动没把东西搞坏"，只能靠：
**录一次真跑 → 用录制离线重演 → 对比结构**。本文件测这条链路真的通。

注意回放**不联网、不跑工具**：任何"录制里没有"的调用都会抛 RecordingMiss，
不会偷偷去执行——否则回放本身又变成一个不确定的副作用来源。
"""

from __future__ import annotations

import asyncio

import pytest

from clipwright.schema.task import Plan, PlanStep, TaskRequest
from clipwright.services import agent_kernel as ak
from clipwright.services import recording as rec
from clipwright.services import task_events as te


class FixedPlanner:
    """确定性规划器：plan/reflect 都给固定结果。"""

    async def plan(self, task):  # noqa: ANN001, ANN201
        return Plan(
            steps=[
                PlanStep(id="s1", title="探测", capability="media_probe",
                         args={"input_path": "a.mp4"}),
                PlanStep(id="s2", title="再看", capability="media_probe",
                         args={"input_path": "b.mp4"}),
            ],
            rationale="固定两步",
        )

    async def reflect(self, task, step, outcome):  # noqa: ANN001, ANN201
        return {"action": "continue"}


async def fixed_executor(task, step):  # noqa: ANN001, ANN201
    return {"status": "ok", "output": {"duration_sec": 12.5, "frames": 300,
                                       "path": f"{step.id}.mp4"}}


@pytest.fixture(autouse=True)
def _clean():
    ak.reset_kernel_state()
    te.clear()
    yield
    ak.reset_kernel_state()
    te.clear()


def _kernel(tmp_path, *, planner=None, executor=None):
    return ak.AgentKernel(ak.KernelDeps(
        planner=planner or FixedPlanner(),
        executor=executor or fixed_executor,
        checkpoint_dir=tmp_path / "tasks",
    ))


def test_recorded_run_can_be_replayed_offline(tmp_path):
    """A2：录一次 → 用录制重演 → 计划/步骤/摘要结构一致。"""
    rec_path = tmp_path / "run.jsonl"

    # 1) 真跑并录制
    with rec.recording_scope(rec.Recorder(rec_path)):
        k1 = _kernel(tmp_path)
        task1 = k1.create(TaskRequest(topic="做一个 30 秒视频", plan_only=False))
        task1 = asyncio.run(k1.run(task1))

    assert task1.status == "done"
    assert rec_path.exists()

    # 2) 用录制重演（不联网、不跑工具）
    replayer = rec.Replayer(rec_path)
    ak.reset_kernel_state()
    te.clear()
    k2 = _kernel(
        tmp_path,
        planner=rec.make_replay_planner(replayer),
        executor=rec.make_replay_executor(replayer),
    )
    task2 = k2.create(TaskRequest(topic="做一个 30 秒视频", plan_only=False))
    task2 = asyncio.run(k2.run(task2))

    # 结构一致：步骤、状态、关键数值
    assert [s.capability for s in task2.plan.steps] == [s.capability for s in task1.plan.steps]
    assert [s.status for s in task2.plan.steps] == [s.status for s in task1.plan.steps]
    assert task2.status == task1.status
    assert task2.context_digest[0]["facts"] == task1.context_digest[0]["facts"]


def test_replay_miss_is_loud_not_silent(tmp_path):
    """录制不完整时回放必须报错，不允许"顺手真跑一次"。"""
    rec_path = tmp_path / "partial.jsonl"
    with rec.recording_scope(rec.Recorder(rec_path)):
        k1 = _kernel(tmp_path)
        task1 = k1.create(TaskRequest(topic="只规划", plan_only=True))
        asyncio.run(k1.plan(task1))

    replayer = rec.Replayer(rec_path)
    ak.reset_kernel_state()
    te.clear()
    boom = rec.make_replay_executor(replayer)
    k2 = _kernel(tmp_path, planner=rec.make_replay_planner(replayer), executor=boom)
    task2 = k2.create(TaskRequest(topic="只规划", plan_only=False))

    # 录制里没有 tool 调用 → 执行器抛 RecordingMiss → Kernel 把它变成失败收尾
    task2 = asyncio.run(k2.run(task2))
    assert task2.status == "failed"
    # 缺失必须是响的：原因里能读到 RecordingMiss，而不是"任务莫名结束"
    assert "RecordingMiss" in (task2.finish.reason + str(task2.finish.unfinished))


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
        rec.Replayer(path_b).result_of("tool", "media_probe", {"n": "a"})
