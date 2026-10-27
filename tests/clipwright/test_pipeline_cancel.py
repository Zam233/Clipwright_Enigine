"""G2：协作式取消——**标记层**（modern-agent PRD §4.1 / M3）。

老编排器的"取消在 agent 边界生效"那几条用例随执行引擎一起删除（被测对象不存在了）。
仍然有效、也必须保住的断言是：

- 取消标记本身可置可清（`mark_cancelled` / `is_cancelled` / `clear_cancel`）；
- 取消端点对**任务**（主 Agent）有效：任务被置为 cancelled 并落终态事件。

新执行路径的取消在 Kernel 侧由 `_CONTROL` + `agent_run._LIVE` 实现，
相关用例见 `tests/test_agent_kernel.py::test_cancel_marks_task_cancelled` 与
`tests/test_agent_run.py::test_cancel_works_for_stage_run`。
"""

from __future__ import annotations

import pytest

from clipwright.services.pipeline_history import (
    clear_cancel,
    is_cancelled,
    mark_cancelled,
)


@pytest.fixture(autouse=True)
def _clean_cancel():
    clear_cancel()
    yield
    clear_cancel()


def test_mark_and_check() -> None:
    assert is_cancelled("pl_x") is False
    mark_cancelled("pl_x")
    assert is_cancelled("pl_x") is True
    clear_cancel()
    assert is_cancelled("pl_x") is False


def test_clear_single_cancel_flag() -> None:
    mark_cancelled("pl_a")
    mark_cancelled("pl_b")
    clear_cancel("pl_a")
    assert is_cancelled("pl_a") is False
    assert is_cancelled("pl_b") is True


def test_cancel_endpoint_marks_agent_task_cancelled() -> None:
    """取消端点对主 Agent 任务生效（老管线分支已删，只剩这条真路径）。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from clipwright.api import pipeline as pipeline_api
    from clipwright.schema.task import TaskRequest
    from clipwright.services import task_events as te
    from clipwright.services.agent_kernel import AgentKernel, KernelDeps

    class _NoopDriver:
        async def __call__(self, task, *, context_text="", shadow=False,  # noqa: ANN001
                           guard=None, on_tool_call=None):  # noqa: ANN001, ARG002
            return {"status": "ok", "finish": {"conclusion": "noop"},
                    "tool_calls": [], "rounds": 1, "stopped_reason": "model_final"}

    kernel = AgentKernel(KernelDeps(driver=_NoopDriver()))
    task = kernel.create(TaskRequest(topic="x", plan_only=False))
    app = FastAPI()
    app.include_router(pipeline_api.router)
    client = TestClient(app)

    resp = client.post(f"/api/pipeline/cancel/{task.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "cancelled"
    assert te.events(task.id)
