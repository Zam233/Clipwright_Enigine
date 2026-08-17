"""A10: TaskQueue 优先级 + Mongo 持久化 + 重启恢复 + 管线接线 测试。"""

from __future__ import annotations

import asyncio

import pytest

from clipwright.services.task_queue import TaskQueue, _mongo_collection


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
