"""Generic bounded Tool/Skill loop for AnimationAgent."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


@dataclass
class AgentAction:
    kind: str  # tool|skill|final
    name: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    reason: str = ""


@dataclass
class LoopResult:
    status: str
    actions: list[AgentAction] = field(default_factory=list)
    observations: list[Any] = field(default_factory=list)
    rounds: int = 0
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "rounds": self.rounds, "error": self.error,
                "actions": [a.__dict__ for a in self.actions],
                "observations": self.observations}


async def run_agentic_loop(
    *,
    decide: Callable[[list[Any]], Awaitable[AgentAction]],
    execute_tool: Callable[[str, dict[str, Any]], Awaitable[Any]],
    load_skill: Callable[[str], Awaitable[Any]] | None = None,
    max_rounds: int = 3,
    max_calls: int = 8,
    initial_observations: list[Any] | None = None,
) -> LoopResult:
    actions: list[AgentAction] = []
    observations: list[Any] = list(initial_observations or [])
    rounds = 0
    calls = 0
    while rounds < max_rounds:
        rounds += 1
        action = await decide(observations)
        actions.append(action)
        if action.kind == "final":
            return LoopResult("ok", actions, observations, rounds)
        if action.kind == "skill":
            if load_skill is None:
                observations.append({"error": "skill loader unavailable"})
                continue
            observations.append(await load_skill(action.name))
            continue
        if action.kind == "tool":
            if calls >= max_calls:
                return LoopResult("capped", actions, observations, rounds, "max_calls")
            calls += 1
            try:
                observations.append(await execute_tool(action.name, action.args))
            except Exception as exc:  # noqa: BLE001 — 工具失败作为观察，不终止循环
                observations.append({"error": str(exc)[:500], "tool": action.name})
            continue
        observations.append({"error": f"unknown action kind: {action.kind}"})
    return LoopResult("capped", actions, observations, rounds, "max_rounds")
