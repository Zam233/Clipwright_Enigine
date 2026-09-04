"""批次 A 回归：调用链 P0 修复（fix-report A1/A2/A3/A4/A8）。

- A1: /run-async 不再因读取 body.headers 而 500；
- A2: TaskQueue 支持任务级超时且超时终态为 timeout（非 failed/cancelled）；
- A3: /run-async 运行中任务注册 _running_pipelines，/cancel 可即时中断；
- A4: SSE 流以 cancelled/timeout 为终态关闭；
- A8: 幂等键 set-before-run，并发重复请求去重。

端点用直接协程调用驱动（TestClient 每请求独立事件循环，后台任务跨请求
不存活，无法覆盖运行中取消路径）。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from clipwright.schema.pipeline import PipelineRequest
from clipwright.services.task_queue import TaskQueue, TaskStatus

_MIN_REQUEST = {
    "persona_id": "p1",
    "category_plugin_id": "c1",
    "topic": "batch-a 冒烟",
}


class _FakeRequest:
    """最小 Request 桩：off/token 鉴权模式下 current_user_id 走 state.user_id。"""

    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.state = SimpleNamespace(user_id=None, user_role=None)

    async def is_disconnected(self) -> bool:  # SSE 流用
        return False


def _cleanup_pipeline_state(pipeline_id: str) -> None:
    from clipwright.api import pipeline as pipeline_api
    from clipwright.services.trace import clear as _clear_trace

    pipeline_api._pipeline_results.pop(pipeline_id, None)
    pipeline_api._running_pipelines.pop(pipeline_id, None)
    pipeline_api._pipeline_owners.pop(pipeline_id, None)
    pipeline_api._pipeline_tasks.pop(pipeline_id, None)
    pipeline_api._user_cancel_requested.discard(pipeline_id)
    _clear_trace(pipeline_id)


async def _wait_for_async(predicate, timeout_sec: float = 10.0):
    deadline = asyncio.get_running_loop().time() + timeout_sec
    while asyncio.get_running_loop().time() < deadline:
        value = predicate()
        if value:
            return value
        await asyncio.sleep(0.05)
    return None


@pytest.fixture()
def fake_orch_blocked(monkeypatch):
    """永不完成的 orchestrator（供取消测试）。"""
    from clipwright.api import pipeline as pipeline_api

    class _FakeOrchBlocked:
        async def run(self, request, pipeline_id: str = "", task_id: str = ""):
            await asyncio.sleep(60)
            raise AssertionError("管线应在取消时被中断，而不是跑完")

    monkeypatch.setattr(pipeline_api, "PipelineOrchestratorV2", _FakeOrchBlocked)


# ── A2: 任务级超时 ──────────────────────────────


class TestTaskQueuePerTaskTimeout:
    @pytest.mark.asyncio
    async def test_long_task_survives_default_timeout(self) -> None:
        """默认 task_timeout_sec=1 时，timeout_sec=5 的任务不被杀（A2 核心）。"""
        q = TaskQueue(max_concurrent=1, task_timeout_sec=1)

        async def handler() -> str:
            await asyncio.sleep(0.5)
            return "done"

        task_id = await q.submit("pipeline", handler, timeout_sec=5)
        for _ in range(80):
            if q.get_status(task_id).get("status") in ("completed", "failed", "timeout", "cancelled"):
                break
            await asyncio.sleep(0.1)
        assert q.get_status(task_id)["status"] == "completed"

    @pytest.mark.asyncio
    async def test_timeout_task_marks_timeout_not_failed(self) -> None:
        """超时任务终态为 timeout（区别于失败/取消）。"""
        q = TaskQueue(max_concurrent=1, task_timeout_sec=1)

        async def handler() -> None:
            await asyncio.sleep(5)

        task_id = await q.submit("pipeline", handler, timeout_sec=0.2)
        for _ in range(80):
            if q.get_status(task_id).get("status") in ("completed", "failed", "timeout", "cancelled"):
                break
            await asyncio.sleep(0.1)
        body = q.get_status(task_id)
        assert body["status"] == TaskStatus.TIMEOUT
        assert "超时" in (body.get("error") or "")


# ── A1/A3/A4/A8: run-async 调用链 ────────────────


@pytest.mark.asyncio
async def test_run_async_accepts_missing_priority_header(fake_orch_blocked, monkeypatch):
    """A1: 不带 X-Priority 头也必须成功启动（曾因 request.headers 500）。"""
    from clipwright.api import pipeline as pipeline_api

    # 干净的并发环境：独占队列（避免其它用例遗留任务占用信号量）
    monkeypatch.setattr(
        "clipwright.services.task_queue._task_queue", TaskQueue(max_concurrent=3),
    )
    resp = await pipeline_api.run_pipeline_async(
        PipelineRequest(**_MIN_REQUEST), _FakeRequest(),
    )
    assert resp["pipeline_id"].startswith("pl_")
    assert resp["status"] == "started"
    assert resp["task_id"].startswith("task_")
    await pipeline_api.cancel_pipeline(resp["pipeline_id"], _FakeRequest())
    _cleanup_pipeline_state(resp["pipeline_id"])


@pytest.mark.asyncio
async def test_run_async_idempotency_dedup(fake_orch_blocked, monkeypatch):
    """A8: 相同 Idempotency-Key 二次请求直接去重（占位在 await 前写入）。"""
    from clipwright.api import pipeline as pipeline_api

    monkeypatch.setattr(
        "clipwright.services.task_queue._task_queue", TaskQueue(max_concurrent=3),
    )
    req1 = _FakeRequest()
    req1.headers["Idempotency-Key"] = "batch-a-idem-1"
    r1 = await pipeline_api.run_pipeline_async(PipelineRequest(**_MIN_REQUEST), req1)
    req2 = _FakeRequest()
    req2.headers["Idempotency-Key"] = "batch-a-idem-1"
    r2 = await pipeline_api.run_pipeline_async(PipelineRequest(**_MIN_REQUEST), req2)
    assert r2["status"] == "deduplicated"
    assert r2["pipeline_id"] == r1["pipeline_id"]
    await pipeline_api.cancel_pipeline(r1["pipeline_id"], _FakeRequest())
    _cleanup_pipeline_state(r1["pipeline_id"])


@pytest.mark.asyncio
async def test_run_async_cancel_interrupts_and_stream_closes(fake_orch_blocked, monkeypatch):
    """A3/A4: 运行中任务可被即时取消，SSE 流以 cancelled 终态关闭。"""
    from clipwright.api import pipeline as pipeline_api

    monkeypatch.setattr(
        "clipwright.services.task_queue._task_queue", TaskQueue(max_concurrent=3),
    )
    resp = await pipeline_api.run_pipeline_async(
        PipelineRequest(**_MIN_REQUEST), _FakeRequest(),
    )
    pid = resp["pipeline_id"]

    # 等待队列 handler 启动并注册运行任务（同一事件循环内，必然存活）
    registered = await _wait_for_async(lambda: pipeline_api._running_pipelines.get(pid))
    assert registered is not None, "run-async 未注册运行任务（A3 回归）"

    cancel_resp = await pipeline_api.cancel_pipeline(pid, _FakeRequest())
    assert cancel_resp["status"] == "cancelling"

    result = await _wait_for_async(lambda: pipeline_api._pipeline_results.get(pid))
    assert result is not None, "取消后未写终态结果"
    assert result["status"] == "cancelled"

    # A4: SSE 流在回放 cancelled 事件后应主动关闭（生成器自然耗尽）
    from clipwright.services.trace import get_all_events

    events = get_all_events(pid)
    assert any(e.get("type") == "cancelled" for e in events)
    stream_resp = await pipeline_api.stream_pipeline_trace(pid, _FakeRequest())
    types_seen: list[str] = []
    async for payload in stream_resp.body_iterator:
        line = payload if isinstance(payload, str) else payload.decode("utf-8")
        for seg in line.split("data: ")[1:]:
            import json as _json

            types_seen.append(_json.loads(seg.strip()).get("type", ""))
    assert "cancelled" in types_seen
    _cleanup_pipeline_state(pid)


@pytest.mark.asyncio
async def test_run_async_timeout_written_as_timeout_state(monkeypatch):
    """A2: 队列超时强杀后终态为 timeout（非 cancelled），区别于用户取消。"""
    from clipwright.api import pipeline as pipeline_api

    class _SleepOrch:
        async def run(self, request, pipeline_id: str = "", task_id: str = ""):
            await asyncio.sleep(30)

    monkeypatch.setattr(pipeline_api, "PipelineOrchestratorV2", _SleepOrch)
    monkeypatch.setattr(
        "clipwright.services.task_queue._task_queue", TaskQueue(max_concurrent=3),
    )

    # 强制队列以 0.3s 任务超时提交，触发真实 wait_for 超时路径
    orig_submit = TaskQueue.submit

    async def fast_submit(self, task_type, handler, *a, **kw):
        kw["timeout_sec"] = 0.3
        return await orig_submit(self, task_type, handler, *a, **kw)

    monkeypatch.setattr(TaskQueue, "submit", fast_submit)

    resp = await pipeline_api.run_pipeline_async(
        PipelineRequest(**_MIN_REQUEST), _FakeRequest(),
    )
    pid = resp["pipeline_id"]

    result = await _wait_for_async(lambda: pipeline_api._pipeline_results.get(pid))
    assert result is not None, "队列超时后未写终态结果"
    assert result["status"] == "timeout"
    assert "超时" in (result.get("error") or "")
    _cleanup_pipeline_state(pid)
