"""B12：公共 AgentLoop `drive` + 预算闸 + subagent_round 事件 + 分级预算表面。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from clipwright.services import agent_loop


@dataclass
class _Decision:
    calls: list[Any] = field(default_factory=list)
    text: str = ""


@dataclass
class _Verdict:
    accepted: bool
    code: str = ""
    missing: list[str] = field(default_factory=list)
    reason: str = ""


def _obs(kind: str = "tool", name: str = "") -> agent_loop.LoopObservation:
    return agent_loop.LoopObservation(kind=kind, name=name, ok=True)


@pytest.mark.asyncio
async def test_drive_runs_until_final_and_emits_rounds():
    decisions = [
        _Decision(calls=[agent_loop.LoopAction("tool", "read_file")]),
        _Decision(calls=[agent_loop.LoopAction("tool", "media_probe")]),
        _Decision(calls=[], text="done"),
    ]
    rounds: list[tuple[int, list[str], str]] = []

    async def decide(_obs):
        return decisions.pop(0)

    async def dispatch(_action):
        return _obs("tool", _action.name)

    exit_ = await agent_loop.drive(
        run_id="run_1", decide=decide, dispatch=dispatch,
        emit_round=lambda r, names, rid: rounds.append((r, names, rid)),
    )
    assert exit_.status == "ok" and exit_.reason == "completed"
    assert exit_.rounds == 3 and exit_.actions == 2
    assert rounds == [(1, ["read_file"], "run_1"), (2, ["media_probe"], "run_1"),
                      (3, [], "run_1")]


@pytest.mark.asyncio
async def test_gate_rejection_is_fed_back_as_observation():
    seen: list[list[Any]] = []

    async def decide(obs):
        seen.append(list(obs))
        if any(getattr(o, "kind", "") == "gate_rejected" for o in obs):
            return _Decision(calls=[], text="fixed")
        return _Decision(calls=[], text="draft")

    async def dispatch(_a):
        return _obs("tool", "generate")

    def gate(_candidate):
        if any(getattr(o, "kind", "") == "gate_rejected" for o in seen[-1]):
            return _Verdict(True)
        return _Verdict(False, code="missing_evidence", missing=["preview"])

    exit_ = await agent_loop.drive(
        run_id="run_2", decide=decide, dispatch=dispatch, gate=gate)
    assert exit_.status == "ok"
    assert any(getattr(o, "kind", "") == "gate_rejected" for o in exit_.observations)
    assert exit_.rounds == 2


@pytest.mark.asyncio
async def test_budget_and_cancel_gates():
    async def decide(_obs):
        return _Decision(calls=[agent_loop.LoopAction("tool", "x")])

    async def dispatch(_a):
        return _obs()

    exit_ = await agent_loop.drive(
        run_id="r", decide=decide, dispatch=dispatch,
        budget=agent_loop.LoopBudget(rounds=2, actions=10))
    assert exit_.status == "failed" and exit_.reason == "limit_rounds"

    exit2 = await agent_loop.drive(
        run_id="r", decide=decide, dispatch=dispatch,
        budget=agent_loop.LoopBudget(rounds=10, actions=2))
    assert exit2.status == "failed" and exit2.reason == "limit_calls"

    exit3 = await agent_loop.drive(
        run_id="r", decide=decide, dispatch=dispatch, is_cancelled=lambda: True)
    assert exit3.status == "cancelled" and exit3.reason == "user_cancelled"


@pytest.mark.asyncio
async def test_wall_sec_none_means_no_limit_but_configured_enforced():
    ticks = {"t": 0.0}

    def clock():
        ticks["t"] += 1.0
        return ticks["t"]

    async def decide(_obs):
        return _Decision(calls=[agent_loop.LoopAction("tool", "x")])

    async def dispatch(_a):
        return _obs()

    no_limit = await agent_loop.drive(
        run_id="r", decide=decide, dispatch=dispatch, clock=clock,
        budget=agent_loop.LoopBudget(rounds=3, actions=10, wall_sec=None))
    assert no_limit.reason == "limit_rounds"  # 不是 limit_wall

    ticks["t"] = 0.0
    limited = await agent_loop.drive(
        run_id="r", decide=decide, dispatch=dispatch, clock=clock,
        budget=agent_loop.LoopBudget(rounds=10, actions=10, wall_sec=2.0))
    assert limited.status == "failed" and limited.reason == "limit_wall"


@pytest.mark.asyncio
async def test_budgeted_surface_degrades_unknown_costly(monkeypatch):
    called = {"n": 0}

    async def dispatch(_a):
        called["n"] += 1
        return _obs("tool", _a.name)

    tiers = {"tts": "costly", "read_file": "read"}
    surface = agent_loop.BudgetedSurface(
        dispatch, tier_of=lambda n: tiers.get(n, "read"), prices={}, lease_usd=None)

    out = await surface(agent_loop.LoopAction("tool", "tts"))
    assert out.ok is False and "budget_unreserved" in out.error
    assert called["n"] == 0, "预留不足不得真执行付费动作"

    ok = await surface(agent_loop.LoopAction("tool", "read_file"))
    assert ok.ok is True and called["n"] == 1


@pytest.mark.asyncio
async def test_round_emitter_writes_event(monkeypatch):
    got: list[tuple] = []

    def fake_emit(task_id, etype, message="", detail=None, **_kw):
        got.append((task_id, etype, detail))

    import clipwright.services.agent_loop as loop_mod
    monkeypatch.setattr(loop_mod, "emit", fake_emit)
    emitter = agent_loop.make_round_emitter("task_x")
    emitter(2, ["a", "b"], "run_9")
    assert got == [("task_x", "subagent_round",
                    {"run_id": "run_9", "round": 2, "visible_calls": ["a", "b"]})]
