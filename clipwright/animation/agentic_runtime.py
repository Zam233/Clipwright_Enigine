"""M2 runtime: bind AnimationAgent decisions to Tool/Skill loop."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from clipwright.animation.agent_tools import call_animation_tool
from clipwright.animation.agentic_loop import AgentAction, LoopResult, run_agentic_loop
from clipwright.animation.agentic.adapters import observation_from_result
from clipwright.animation.agentic.ledger import FailureLedger
from clipwright.animation.prompt_skills import SKILLS, compose_skills


DECIDE_SYSTEM = """你是 AnimationAgent 的导演循环。生成 mg_def 后必须 preview_html → capture_preview_frames → inspect_frames；有 blocking findings 必须 revise_mg 并重新抽帧质检；没有 inspect_frames 证据时禁止 final。观察里没有 mg_def 时禁止 final。
根据目标与已有观察，选择下一步：
- tool：调用一个动画工具；
- skill：加载一个方法论/规则包；
- final：完成，交给交付/QA。
只输出 JSON：{"kind":"tool|skill|final","name":"...","args":{},"reason":"..."}
不要输出 markdown。args 必须简短；不要复述目标。"""


class AnimationAgentRuntime:
    def __init__(self, *, max_rounds: int = 3, max_calls: int = 8,
                 default_args: dict[str, Any] | None = None, require_mg: bool = True) -> None:
        self.max_rounds = max_rounds
        self.max_calls = max_calls
        self.default_args = dict(default_args or {})
        self.require_mg = require_mg
        from clipwright.animation.agent_tool_impl import install_animation_tool_handlers
        install_animation_tool_handlers()

    async def decide_with_llm(self, llm: Any, goal: str, observations: list[Any]) -> AgentAction:
        """Prefer provider-native tool calls; never parse a free-form JSON blob if tools exist."""
        from clipwright.animation.agent_tools import list_animation_tools
        tools = list_animation_tools()
        schemas = [{
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": {"type": "object", "properties": {}, "additionalProperties": True},
            },
        } for t in tools]
        schemas.append({"type": "function", "function": {
            "name": "final", "description": "结束本轮，交付当前 mg_def",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": True},
        }})
        obs_text = "\n".join(str(o) for o in observations[-6:]) or "（无）"
        prompt = f"目标：{goal}\n\n已有观察：\n{obs_text}\n\n选择下一个工具调用。"
        resp = await llm.generate(
            messages=[{"role": "system", "content": DECIDE_SYSTEM},
                      {"role": "user", "content": prompt}],
            tools=schemas,
        )
        calls = getattr(resp, "tool_calls", None) or []
        if calls:
            tc = calls[0]
            name = str(getattr(tc, "name", "") or "")
            raw_args = getattr(tc, "arguments", "") or "{}"
            try:
                import json
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
            except Exception:
                return AgentAction("tool", "generate_mg", {}, "tool args 无法解析，回退生成")
            if name == "final":
                return AgentAction("final", "", {}, "model final")
            if name:
                return AgentAction("tool", name, args, "")
        # Provider without tool-calling: fall back to a single explicit final/parse path.
        import json
        raw = str(getattr(resp, "content", "") or "").strip()
        start_i, end_i = raw.find("{"), raw.rfind("}")
        try:
            data = json.loads(raw[start_i:end_i + 1]) if start_i >= 0 and end_i > start_i else {}
        except Exception:
            return AgentAction("tool", "generate_mg", {}, "decision JSON 无法解析，回退生成")
        if str(data.get("kind")) == "final":
            return AgentAction("final", "", {}, str(data.get("reason") or ""))
        return AgentAction("tool", str(data.get("name") or "generate_mg"),
                           dict(data.get("args") or {}), str(data.get("reason") or ""))

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

        ledger = FailureLedger()
        async def execute(name: str, args: dict[str, Any]) -> Any:
            merged = {**self.default_args, **args}
            obs = observation_from_result(name, await call_animation_tool(name, **merged))
            ledger.add(obs)
            return obs

        async def guarded(observations: list[Any]) -> AgentAction:
            action = await decide(observations)
            def data_of(o: Any) -> dict[str, Any]:
                return o.data if hasattr(o, "data") else (o if isinstance(o, dict) else {})

            mg_obs = next((o for o in reversed(observations)
                           if data_of(o).get("mg_def")), None)
            html_obs = next((o for o in reversed(observations)
                             if getattr(o, "tool", "") == "preview_html"), None)
            frame_obs = next((o for o in reversed(observations)
                              if getattr(o, "tool", "") == "capture_preview_frames"), None)
            inspect_obs = next((o for o in reversed(observations)
                                if getattr(o, "tool", "") == "inspect_frames"), None)
            if action.kind == "final":
                if not mg_obs:
                    return AgentAction("tool", "generate_mg", {}, "还没有 mg_def")
                if not html_obs:
                    return AgentAction("tool", "preview_html", {"mg_def": data_of(mg_obs)["mg_def"]}, "先做浏览器预览")
                if not frame_obs:
                    return AgentAction("tool", "capture_preview_frames",
                                       {"html_path": data_of(html_obs).get("path", "")}, "还没有抽帧证据")
                if not inspect_obs:
                    return AgentAction("tool", "inspect_frames",
                                       {"frames": data_of(frame_obs).get("frames", []), "brief": ""},
                                       "还没有 vision 质检")
                findings = data_of(inspect_obs).get("findings") or []
                blocking = [f for f in findings if str(f.get("severity") or "") == "blocking"]
                if blocking:
                    return AgentAction("tool", "revise_mg",
                                       {"mg_def": data_of(mg_obs)["mg_def"], "findings": findings},
                                       "inspect 仍有 blocking findings")
                if not ledger.can_final():
                    return AgentAction("tool", "generate_mg", {}, "存在未解决失败")
            if action.kind == "final" and not ledger.can_final():
                return AgentAction("tool", "generate_mg", {}, "存在未解决失败")
            return action

        result = await run_agentic_loop(
            decide=guarded,
            execute_tool=execute,
            load_skill=load_skill,
            max_rounds=self.max_rounds,
            max_calls=self.max_calls,
        )
        if self.require_mg and not any(
            isinstance(o, dict) and o.get("mg_def") for o in result.observations
        ):
            try:
                result.observations.append(await execute("generate_mg", {}))
                result.status = "recovered"
            except Exception as exc:  # noqa: BLE001
                result.observations.append({"error": str(exc)[:500], "tool": "generate_mg"})
        return result
