"""M2 runtime: bind AnimationAgent decisions to Tool/Skill loop."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from clipwright.animation.agent_tools import call_animation_tool
from clipwright.animation.agentic_loop import AgentAction, LoopResult, run_agentic_loop
from clipwright.animation.prompt_skills import compose_skills


class AnimationAgentRuntime:
    def __init__(self, *, max_rounds: int = 3, max_calls: int = 8) -> None:
        self.max_rounds = max_rounds
        self.max_calls = max_calls

    async def run(
        self,
        decide: Callable[[list[Any]], Awaitable[AgentAction]],
        *,
        category: str = "",
        shot_type: str = "",
        needs: set[str] | None = None,
    ) -> LoopResult:
        async def load_skill(name: str) -> dict[str, Any]:
            return {"skill": name, "prompt": compose_skills(category=category or name, shot_type=shot_type, needs=needs)}

        async def execute(name: str, args: dict[str, Any]) -> Any:
            return await call_animation_tool(name, **args)

        return await run_agentic_loop(
            decide=decide,
            execute_tool=execute,
            load_skill=load_skill,
            max_rounds=self.max_rounds,
            max_calls=self.max_calls,
        )
