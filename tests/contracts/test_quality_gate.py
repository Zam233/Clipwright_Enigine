"""quality 门：final_timeline 存在但 quality 未跑时强制续跑一次。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from clipwright.services import agent_kernel


class _Usage(SimpleNamespace):
    def model_dump(self):
        return dict(self.__dict__)


def _task():
    return SimpleNamespace(
        id="task_quality_gate", status="running",
        usage=_Usage(tool_calls=0, cost_usd=0.0, unknown_cost_calls=0,
                     turns=0, wall_sec=0.0),
        context_digest=[], plan=None, request=SimpleNamespace(extra={}), finish=None,
        touch=lambda: None,
    )


def test_quality_gate_requires_quality(monkeypatch) -> None:
    from clipwright.config import settings

    monkeypatch.setattr(settings, "agent_quality_gate", True, raising=False)
    monkeypatch.setattr(agent_kernel, "_MAX_TIMELINE_CONTINUATIONS", 0)
    monkeypatch.setattr("clipwright.services.task_blackboard.get",
                        lambda _tid: {"final_timeline": {"tracks": []}}, raising=False)
    calls = {"n": 0}

    async def _driver(task, **_kwargs):  # noqa: ANN001
        calls["n"] += 1
        return {"status": "ok", "finish": {"conclusion": "完成"},
                "rounds": 1, "tool_calls": []}

    kernel = agent_kernel.AgentKernel(agent_kernel.KernelDeps(driver=_driver))
    task = _task()
    asyncio.run(kernel._run_tool_loop(task, started=0.0))  # noqa: SLF001
    assert calls["n"] >= 2
    assert task.finish is not None
    assert task.finish.reason == "quality_not_run"
