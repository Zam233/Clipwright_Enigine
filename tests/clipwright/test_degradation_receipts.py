"""降级处置收据（PRD modern-agent-review P0）。

现场：守门只"再推一轮"，模型口头说一句「接受」就收尾成 done —— 处置无法验证。
现在只有两种收据：`remediated`（重派同阶段成功）/ `accepted`（显式 ack 工具），
无收据 → `degradation_unresolved` 失败收尾（策略 require_receipt）。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from clipwright.services import agent_kernel, degradation_ledger as L


@pytest.fixture(autouse=True)
def _clean():
    L.clear()
    agent_kernel._PENDING_DEGRADATIONS.clear()
    agent_kernel._DEGRADATION_CONTINUATIONS.clear()
    yield
    L.clear()
    agent_kernel._PENDING_DEGRADATIONS.clear()
    agent_kernel._DEGRADATION_CONTINUATIONS.clear()


def test_register_dedupes_and_tracks_pending():
    fresh = L.register("t", [{"kind": "a", "scope": "edit"}, {"kind": "a", "scope": "edit"}])
    assert len(fresh) == 1
    assert len(L.outstanding("t")) == 1
    assert L.register("t", [{"kind": "a", "scope": "edit"}]) == []


def test_remediated_clears_stage_degradations():
    L.register("t", [{"kind": "llm_json_invalid", "scope": "animation"},
                     {"kind": "stage_tool_failures", "scope": "edit"}])
    assert L.mark_remediated("t", "animation") == 1
    assert [d["scope"] for d in L.outstanding("t")] == ["edit"]
    modes = [r["mode"] for r in L.receipts("t")]
    assert modes == ["remediated"]


def test_accept_requires_reason_and_clears_kinds():
    L.register("t", [{"kind": "mg_critique_skipped", "scope": "animation"}])
    assert L.accept("t", ["mg_critique_skipped"], reason="只有一处装饰元素受影响") == 1
    assert L.outstanding("t") == []
    receipt = L.receipts("t")[0]
    assert receipt["mode"] == "accepted" and "装饰元素" in receipt["reason"]


def test_accept_unknown_kind_is_noop():
    L.register("t", [{"kind": "x", "scope": "edit"}])
    assert L.accept("t", ["y"], reason="理由足够长了") == 0
    assert len(L.outstanding("t")) == 1


# ── Kernel 收尾：无收据 → 失败 ──────────────────────────────

class _Usage(SimpleNamespace):
    def model_dump(self):
        return dict(self.__dict__)


def _task():
    return SimpleNamespace(
        id="task_receipt", status="running",
        usage=_Usage(tool_calls=0, cost_usd=0.0, unknown_cost_calls=0,
                     turns=0, wall_sec=0.0),
        context_digest=[], plan=None, request=SimpleNamespace(extra={}), finish=None,
        touch=lambda: None,
    )


def _run_kernel(monkeypatch, *, ack: bool, tool_ok: bool = True):
    monkeypatch.setattr(agent_kernel, "_MAX_DEGRADATION_CONTINUATIONS", 0)
    monkeypatch.setattr("clipwright.services.task_blackboard.get",
                        lambda _tid: {"final_timeline": {"tracks": []}}, raising=False)
    monkeypatch.setattr(agent_kernel, "_MEMORY_LOADED", {"task_receipt"}, raising=False)
    L.register("task_receipt", [{"kind": "stage_tool_failures", "scope": "edit"}])
    if ack:
        L.accept("task_receipt", ["stage_tool_failures"], reason="失败都是无效参数重试，产物完整")

    async def _driver(task, *, context_text="", shadow=False, guard=None,  # noqa: ANN001, ANN003
                      on_tool_call=None, dry_run=False):
        return {"status": "ok", "finish": {"conclusion": "做完了"},
                "rounds": 1, "tool_calls": []}

    kernel = agent_kernel.AgentKernel(agent_kernel.KernelDeps(driver=_driver))
    task = _task()
    asyncio.run(kernel._run_tool_loop(task, started=0.0))  # noqa: SLF001
    return task


def test_unresolved_degradation_fails_task(monkeypatch):
    from clipwright.config import settings

    monkeypatch.setattr(settings, "agent_degradation_policy", "require_receipt", raising=False)
    task = _run_kernel(monkeypatch, ack=False)
    assert task.status == "failed"
    assert task.finish.reason == "degradation_unresolved"
    assert any("stage_tool_failures" in item for item in task.finish.unfinished)


def test_accepted_receipt_allows_done(monkeypatch):
    from clipwright.config import settings

    monkeypatch.setattr(settings, "agent_degradation_policy", "require_receipt", raising=False)
    task = _run_kernel(monkeypatch, ack=True)
    assert task.status == "done"
    assert task.finish.reason == "tool_loop_completed"
    assert task.finish.unfinished == []


def test_warn_policy_keeps_done(monkeypatch):
    from clipwright.config import settings

    monkeypatch.setattr(settings, "agent_degradation_policy", "warn", raising=False)
    task = _run_kernel(monkeypatch, ack=False)
    assert task.status == "done"
    # 未处置项仍然写进 unfinished（如实记录）
    assert any("stage_tool_failures" in item for item in task.finish.unfinished)


def test_ack_tool_writes_receipt(monkeypatch):
    from clipwright.tool import surface as sf

    L.register("task_ack", [{"kind": "mg_motion_weak", "scope": "animation"}])
    task = SimpleNamespace(id="task_ack")
    out = asyncio.run(sf.execute("acknowledge_degradation",
        {"kinds": ["mg_motion_weak"], "reason": "该段是片尾定格，动效弱可接受"},
        pipeline_id="task_ack", task=task))
    assert out["status"] == "ok"
    assert out["output"]["accepted"] == 1
    assert L.outstanding("task_ack") == []


def test_ack_tool_rejects_short_reason(monkeypatch):
    from clipwright.tool import surface as sf

    L.register("task_ack2", [{"kind": "stage_tool_failures", "scope": "edit"}])
    out = asyncio.run(sf.execute("acknowledge_degradation",
        {"kinds": ["x"], "reason": "嗯"},
        pipeline_id="task_ack2", task=SimpleNamespace(id="task_ack2")))
    assert out["status"] == "refused"
    assert "reason" in (out.get("error") or "")
