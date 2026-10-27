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


@pytest.fixture(autouse=True)
def _force_agent_mode_off(monkeypatch):
    """本模块测的是「老 API 直跑」链路（pipeline_id 前缀 pl_、运行中取消、幂等去重）。

    这些断言只在 ``agent_mode=off`` 下成立；而本机 settings.json 可能已被操作者
    调成 ``on``（双轨会把请求转成 Task，pipeline_id 变成 task_xxx），
    且 settings API 测试会把 store 覆盖 apply 到共享 settings 单例。
    测试不该依赖本机配置 / 执行顺序 → 显式钉住 off。
    """
    from clipwright.config import settings

    monkeypatch.setattr(settings, "agent_mode", "off")
    # P2 起老管线默认下线（run-async 返回 410）；本模块就是要测这条链路，显式打开
    monkeypatch.setattr(settings, "pipeline_enabled", True, raising=False)


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


