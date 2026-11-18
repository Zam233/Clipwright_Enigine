"""M2 runtime: bind AnimationAgent decisions to Tool/Skill loop."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from clipwright.animation.agent_tools import call_animation_tool
from clipwright.animation.agentic_loop import AgentAction, LoopResult, run_agentic_loop
from clipwright.animation.prompt_skills import SKILLS, compose_skills


DECIDE_SYSTEM = """你是 AnimationAgent 的导演循环。必须先用工具产出 mg_def；观察里没有 mg_def 时禁止 final。
根据目标与已有观察，选择下一步：
- tool：调用一个动画工具；
- skill：加载一个方法论/规则包；
- final：完成，交给交付/QA。
只输出 JSON：{"kind":"tool|skill|final","name":"...","args":{},"reason":"..."}
不要输出 markdown。"""


class AnimationAgentRuntime:
    def __init__(self, *, max_rounds: int = 3, max_calls: int = 8,
                 default_args: dict[str, Any] | None = None) -> None:
        self.max_rounds = max_rounds
        self.max_calls = max_calls
        self.default_args = dict(default_args or {})
        from clipwright.animation.agent_tool_impl import install_animation_tool_handlers
        install_animation_tool_handlers()

    async def decide_with_llm(self, llm: Any, goal: str, observations: list[Any]) -> AgentAction:
        from clipwright.animation.agent_tools import list_animation_tools
        tools = [f"{t.name}: {t.description}" for t in list_animation_tools()]
        skills = [f"{k}: {v.body[:80]}" for k, v in SKILLS.items() if v.kind == "builtin"]
        obs = "\n".join(str(o)[:600] for o in observations[-4:]) or "（无）"
        prompt = (f"目标：{goal}\n\n工具：\n" + "\n".join(tools)
                  + "\n\n技能：\n" + "\n".join(skills)
                  + f"\n\n已有观察：\n{obs}\n\n下一步：")
        resp = await llm.generate(messages=[{"role": "system", "content": DECIDE_SYSTEM},
                                             {"role": "user", "content": prompt}], max_tokens=300)
        import json
        raw = str(getattr(resp, "content", "") or "").strip()
        start, end = raw.find("{"), raw.rfind("}")
        data = json.loads(raw[start:end + 1]) if start >= 0 and end > start else {}
        return AgentAction(
            str(data.get("kind") or "final"), str(data.get("name") or ""),
            dict(data.get("args") or {}), str(data.get("reason") or "")[:200],
        )

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
            merged = {**self.default_args, **args}
            return await call_animation_tool(name, **merged)

        return await run_agentic_loop(
            decide=decide,
            execute_tool=execute,
            load_skill=load_skill,
            max_rounds=self.max_rounds,
            max_calls=self.max_calls,
        )
