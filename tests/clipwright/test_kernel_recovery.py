"""收尾守门的确定性补救（2026-10-30，第 8 轮）。

模型什么时候收尾是概率的；`final_timeline` 缺失/降级待修是**确定性**事实。
提示词推两轮不动，就由 Kernel 用同一套工具面自己补派——记录成真实工具调用，
成功且未回放时给该阶段写 remediated 收据。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from clipwright.services import agent_kernel
from clipwright.services import degradation_ledger

pytestmark = pytest.mark.enable_kernel_recovery


class _Task:
    id = "task_recover"

    class usage:
        tool_calls = 0
        cost_usd = 0.0
        unknown_cost_calls = 0

    class request:
        extra: dict = {}


def _reset():
    for attr in ("_TIMELINE_CONTINUATIONS", "_DEGRADATION_CONTINUATIONS",
                 "_KERNEL_RECOVERY", "_PENDING_DEGRADATIONS"):
        getattr(agent_kernel, attr).pop("task_recover", None)
    degradation_ledger.clear("task_recover")


@pytest.fixture(autouse=True)
def _clean():
    _reset()
    yield
    _reset()


def test_kernel_recovery_dispatches_missing_stage(monkeypatch):
    calls: list[dict] = []

    async def _fake_execute(name, args, **kw):  # noqa: ANN001, ANN003
        calls.append({"name": name, **args})
        return {"status": "ok", "output": {}, "replayed": False}

    monkeypatch.setattr("clipwright.tool.surface.execute", _fake_execute, raising=False)
    emitted: list = []
    monkeypatch.setattr("clipwright.services.task_events.emit",
                        lambda *a, **kw: emitted.append(a), raising=False)

    task = _Task()
    asyncio.run(agent_kernel._kernel_recover_stage(task, "audio", "final_timeline 缺失"))

    assert calls and calls[0]["name"] == "delegate_agent" and calls[0]["stage"] == "audio"
    assert task.usage.tool_calls == 1, "补派也必须计进 usage"
    kinds = [e[1] for e in emitted]
    assert "narration" in kinds and "tool_result" in kinds, kinds
    assert any(e[1] == "narration" and (e[3] or {}).get("kind") == "kernel_recovery"
               for e in emitted)


def test_kernel_recovery_marks_remediated_receipt(monkeypatch):
    async def _fake_execute(name, args, **kw):  # noqa: ANN001, ANN003
        return {"status": "ok", "output": {}, "replayed": False}

    monkeypatch.setattr("clipwright.tool.surface.execute", _fake_execute, raising=False)
    monkeypatch.setattr("clipwright.services.task_events.emit", lambda *a, **kw: None,
                        raising=False)
    degradation_ledger.register("task_recover", [
        {"kind": "text_motion_weak", "scope": "animation"}])

    asyncio.run(agent_kernel._kernel_recover_stage(_Task(), "animation", "降级待修"))

    assert degradation_ledger.outstanding("task_recover") == [], "真重派成功要写 remediated 收据"


def test_replayed_recovery_does_not_claim_fixed(monkeypatch):
    async def _fake_execute(name, args, **kw):  # noqa: ANN001, ANN003
        return {"status": "ok", "output": {}, "replayed": True}

    monkeypatch.setattr("clipwright.tool.surface.execute", _fake_execute, raising=False)
    monkeypatch.setattr("clipwright.services.task_events.emit", lambda *a, **kw: None,
                        raising=False)
    degradation_ledger.register("task_recover", [
        {"kind": "text_motion_weak", "scope": "animation"}])

    asyncio.run(agent_kernel._kernel_recover_stage(_Task(), "animation", "降级待修"))

    assert len(degradation_ledger.outstanding("task_recover")) == 1, "回放不算修复"


def test_recovery_failure_is_reported_honestly(monkeypatch):
    async def _boom(name, args, **kw):  # noqa: ANN001, ANN003
        raise RuntimeError("workspace 不可写")

    monkeypatch.setattr("clipwright.tool.surface.execute", _boom, raising=False)
    emitted: list = []
    monkeypatch.setattr("clipwright.services.task_events.emit",
                        lambda *a, **kw: emitted.append(a), raising=False)

    out = asyncio.run(agent_kernel._kernel_recover_stage(_Task(), "audio", "缺时间线"))
    assert out["status"] == "error" and "workspace 不可写" in out["error"]
    assert any(e[1] == "degradation" and (e[3] or {}).get("kind") == "kernel_recovery_failed"
               for e in emitted)
