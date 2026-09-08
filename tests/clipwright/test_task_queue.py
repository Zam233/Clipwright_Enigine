"""A10: TaskQueue 优先级 + Mongo 持久化 + 重启恢复 + 管线接线 测试。"""

from __future__ import annotations

import asyncio

import pytest

from clipwright.services.task_queue import (
    PipelineTask,
    QueueFullError,
    TaskQueue,
    TaskStatus,
    _mongo_collection,
)


class TestPriorityOrdering:
    @pytest.mark.asyncio
    async def test_high_priority_runs_first(self) -> None:
        """A10: 高优先级任务先出队（同批提交时）。"""
        order: list[int] = []
        q = TaskQueue(max_concurrent=1)

        async def mk(label: int):
            async def handler():
                await asyncio.sleep(0)
                order.append(label)
            return handler

        await q.submit("pipeline", await mk(1), priority=1)
        await q.submit("pipeline", await mk(5), priority=5)
        await q.submit("pipeline", await mk(3), priority=3)

        # 等待全部完成
        for _ in range(50):
            if q.pending_count == 0 and q.running_count == 0 and len(order) >= 3:
                break
            await asyncio.sleep(0.05)

        assert order == [5, 3, 1]

    @pytest.mark.asyncio
    async def test_priority_clamped(self) -> None:
        q = TaskQueue()
        tid = await q.submit("pipeline", _noop, priority=99)
        task = q.get_task(tid)
        assert task is not None
        assert task.priority == 5


class TestMongoPersistence:
    @pytest.mark.asyncio
    async def test_persist_and_recover_stale(self, monkeypatch) -> None:
        """A10: Mongo 连接时任务落库；pending/running 且内存缺失 → recovered。"""
        fake_docs = [{"task_id": "task_dead", "task_type": "pipeline", "status": "pending"}]
        fake_col = _FakeCol(fake_docs)
        monkeypatch.setattr(
            "clipwright.services.task_queue._mongo_collection",
            lambda name="task_queue": fake_col,
        )

        q = TaskQueue()
        # 模拟重启：内存无 task_dead，Mongo 有 pending → recovered
        recovered = q.recover_stale()
        assert len(recovered) == 1
        assert recovered[0]["task_id"] == "task_dead"
        assert recovered[0]["recovered"] is True
        assert recovered[0]["status"] == "interrupted"

    @pytest.mark.asyncio
    async def test_submit_persists_doc(self, monkeypatch) -> None:
        fake_col = _FakeCol([])
        monkeypatch.setattr(
            "clipwright.services.task_queue._mongo_collection",
            lambda name="task_queue": fake_col,
        )
        q = TaskQueue()
        tid = await q.submit("pipeline", _noop, priority=3)
        # 提交即持久化
        assert len(fake_col.upserts) == 1
        assert fake_col.upserts[0]["task_id"] == tid
        assert fake_col.upserts[0]["priority"] == 3


class TestPipelineTaskEndpoint:
    def test_tasks_route_registered(self) -> None:
        from clipwright.main import app as main_app
        schema = main_app.openapi()
        paths = list(schema.get("paths", {}).keys())
        assert any(p.endswith("/tasks") for p in paths)


class TestBackpressureAndCancelRace:
    """轮69（D5）：排队上限 / 取消竞态 / aging / pending 计数。"""

    @pytest.mark.asyncio
    async def test_queue_full_raises(self) -> None:
        q = TaskQueue(max_pending=2)
        for i in range(2):
            t = PipelineTask(f"t{i}", "pipeline", _noop, (), {})
            q._tasks[t.task_id] = t
        with pytest.raises(QueueFullError):
            await q.submit("pipeline", _noop)

    @pytest.mark.asyncio
    async def test_cancel_while_waiting_for_semaphore(self) -> None:
        """已出队但等信号量期间被取消 → 不得执行（旧实现会照跑）。"""
        ran: list[str] = []
        q = TaskQueue(max_concurrent=1)
        release = asyncio.Event()

        async def blocker() -> None:
            await release.wait()

        async def victim() -> None:
            ran.append("victim")

        await q.submit("pipeline", blocker, priority=5)
        tid = await q.submit("pipeline", victim, priority=1)
        for _ in range(100):
            task = q.get_task(tid)
            if not q._pending_queue and task and task.status == TaskStatus.PENDING:
                break
            await asyncio.sleep(0.02)

        assert q.cancel(tid) is True
        release.set()
        await asyncio.sleep(0.2)
        assert ran == []
        assert q.get_task(tid).status == TaskStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_aging_prevents_starvation(self) -> None:
        """等待 3 分钟的 priority=1 任务应超过新到的 priority=2 任务。"""
        order: list[str] = []
        q = TaskQueue(max_concurrent=1)
        release = asyncio.Event()

        async def blocker() -> None:
            await release.wait()

        def mk(label: str):
            async def handler() -> None:
                order.append(label)
            return handler

        await q.submit("pipeline", blocker, priority=5)
        old_id = await q.submit("pipeline", mk("old_low"), priority=1)
        q.get_task(old_id).enqueued_mono -= 180.0  # 模拟已等待 3 分钟
        await q.submit("pipeline", mk("new_mid"), priority=2)

        for _ in range(100):
            if q.pending_count == 2:
                break
            await asyncio.sleep(0.02)
        release.set()
        for _ in range(100):
            if len(order) >= 2:
                break
            await asyncio.sleep(0.02)
        assert order == ["old_low", "new_mid"]

    @pytest.mark.asyncio
    async def test_pending_count_includes_semaphore_waiters(self) -> None:
        q = TaskQueue(max_concurrent=1)
        release = asyncio.Event()

        async def blocker() -> None:
            await release.wait()

        await q.submit("pipeline", blocker)
        await q.submit("pipeline", _noop)
        for _ in range(100):
            if not q._pending_queue:
                break
            await asyncio.sleep(0.02)
        # 旧实现返回 len(_pending_queue)（此时为 0），少算等信号量的任务
        assert q.pending_count == 1
        release.set()
        await asyncio.sleep(0.1)


class TestCancelPropagation:
    """轮70（D13）：handler 必须向上传播 CancelledError，否则队列把取消/超时误判为 COMPLETED。"""

    @staticmethod
    async def _cancel_queue_task(tid: str) -> None:
        loop_task = next(
            t for t in asyncio.all_tasks() if t.get_name() == f"task-queue-{tid}"
        )
        loop_task.cancel()
        await asyncio.sleep(0.3)

    @pytest.mark.asyncio
    async def test_swallowing_handler_mislabeled_completed(self) -> None:
        """反例固化（D13 根因）：吞掉取消 → 队列误判 COMPLETED。"""
        q = TaskQueue(max_concurrent=1)
        started = asyncio.Event()

        async def swallower():
            started.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                return "swallowed"

        tid = await q.submit("pipeline", swallower)
        await asyncio.wait_for(started.wait(), 2)
        await self._cancel_queue_task(tid)
        assert q.get_task(tid).status == TaskStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_reraising_handler_marked_cancelled(self) -> None:
        """修复语义：重新抛出 → 队列标记 CANCELLED。"""
        q = TaskQueue(max_concurrent=1)
        started = asyncio.Event()

        async def reraiser():
            started.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                raise

        tid = await q.submit("pipeline", reraiser)
        await asyncio.wait_for(started.wait(), 2)
        await self._cancel_queue_task(tid)
        assert q.get_task(tid).status == TaskStatus.CANCELLED


async def _noop():
    return None


class _FakeCol:
    """伪 Mongo 集合：记录 upsert / delete，find 返回固定文档。"""

    def __init__(self, find_docs: list[dict] | None = None):
        self.find_docs = find_docs or []
        self.upserts: list[dict] = []
        self.deletes: list[str] = []

    def update_one(self, filt, update, upsert=False):
        self.upserts.append({**filt, **update.get("$set", {})})

    def delete_one(self, filt):
        self.deletes.append(filt.get("task_id", ""))

    def find(self, filt):
        return list(self.find_docs)
