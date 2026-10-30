"""收尾最后通牒（E2E 2026-10-30）：给模型一次只写收据的机会。

现场：`mg_template_fallback` 未处置 → 判 failed；但那一轮模型既没收到
"必须写收据"的明确指令（续跑预算已被前几轮用掉），也没剩动作可做。
现在守门在即将失败前单独给一次机会，提示词只讲一件事：调 acknowledge_degradation。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from clipwright.services import agent_kernel, degradation_ledger


class _Task:
    id = "task_final_receipt"

    class usage:
        tool_calls = 0
        cost_usd = 0.0
        unknown_cost_calls = 0
        turns = 0

        @classmethod
        def model_dump(cls):
            return {"turns": cls.turns, "tool_calls": cls.tool_calls,
                    "cost_usd": cls.cost_usd,
                    "unknown_cost_calls": cls.unknown_cost_calls}

    class request:
        extra: dict = {}

    def touch(self) -> None:
        return None

    limits = staticmethod(lambda: None)


@pytest.fixture(autouse=True)
def _clean():
    for attr in ("_DEGRADATION_CONTINUATIONS", "_FINAL_RECEIPT", "_KERNEL_RECOVERY"):
        getattr(agent_kernel, attr).pop("task_final_receipt", None)
    degradation_ledger.clear("task_final_receipt")
    yield
    for attr in ("_DEGRADATION_CONTINUATIONS", "_FINAL_RECEIPT", "_KERNEL_RECOVERY"):
        getattr(agent_kernel, attr).pop("task_final_receipt", None)
    degradation_ledger.clear("task_final_receipt")


def test_final_receipt_hint_lists_kinds(monkeypatch):
    """提示词必须点名 kind 并给出唯一动作。"""
    monkeypatch.setattr(agent_kernel, "_MAX_DEGRADATION_CONTINUATIONS", 0)
    monkeypatch.setattr(agent_kernel, "_MAX_KERNEL_RECOVERY", 0)
    monkeypatch.setattr(agent_kernel, "_MAX_FINAL_RECEIPT", 1)
    monkeypatch.setattr("clipwright.services.task_blackboard.get",
                        lambda _tid: {"final_timeline": {"tracks": []}}, raising=False)
    monkeypatch.setattr(agent_kernel, "_MEMORY_LOADED", {"task_final_receipt"}, raising=False)
    degradation_ledger.register("task_final_receipt", [
        {"kind": "mg_template_fallback", "scope": "mg", "detail": "模板顶上"}])

    hints: list[str] = []

    async def _driver(task, *, context_text="", shadow=False, guard=None,  # noqa: ANN001, ANN003
                      on_tool_call=None, dry_run=False):
        hints.append(context_text)
        return {"status": "ok", "finish": {"conclusion": "收尾"},
                "rounds": 1, "tool_calls": []}

    kernel = agent_kernel.AgentKernel(agent_kernel.KernelDeps(driver=_driver))
    asyncio.run(kernel._run_tool_loop(_Task(), started=0.0))  # noqa: SLF001

    assert len(hints) == 2, "第一次收尾 + 一次最后通牒"
    assert "最后一步：写降级收据" in hints[1]
    assert "acknowledge_degradation" in hints[1]
    assert "mg_template_fallback" in hints[1]


def test_final_receipt_only_once(monkeypatch):
    monkeypatch.setattr(agent_kernel, "_MAX_DEGRADATION_CONTINUATIONS", 0)
    monkeypatch.setattr(agent_kernel, "_MAX_KERNEL_RECOVERY", 0)
    monkeypatch.setattr(agent_kernel, "_MAX_FINAL_RECEIPT", 1)
    monkeypatch.setattr("clipwright.services.task_blackboard.get",
                        lambda _tid: {"final_timeline": {"tracks": []}}, raising=False)
    monkeypatch.setattr(agent_kernel, "_MEMORY_LOADED", {"task_final_receipt"}, raising=False)
    degradation_ledger.register("task_final_receipt", [
        {"kind": "text_motion_weak", "scope": "animation"}])

    rounds: list[int] = []

    async def _driver(task, *, context_text="", shadow=False, guard=None,  # noqa: ANN001, ANN003
                      on_tool_call=None, dry_run=False):
        rounds.append(1)
        return {"status": "ok", "finish": {"conclusion": "收尾"},
                "rounds": 1, "tool_calls": []}

    kernel = agent_kernel.AgentKernel(agent_kernel.KernelDeps(driver=_driver))
    task = _Task()
    asyncio.run(kernel._run_tool_loop(task, started=0.0))  # noqa: SLF001

    assert task.status == "failed", "通牒用完后仍无收据 → 如实失败"
    assert task.finish.reason == "degradation_unresolved"
    assert len(rounds) == 2, "只给一次通牒，不能无限续"
