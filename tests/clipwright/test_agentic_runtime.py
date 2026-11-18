import pytest

from clipwright.animation.agentic_loop import AgentAction
from clipwright.animation.agentic_runtime import AnimationAgentRuntime


@pytest.mark.asyncio
async def test_runtime_loads_skill_then_final() -> None:
    seq = [AgentAction("skill", "flow"), AgentAction("final")]
    async def decide(_obs):
        return seq.pop(0)
    rt = AnimationAgentRuntime()
    result = await rt.run(decide, category="flow", shot_type="flow")
    assert result.status == "ok"
    assert "text-arrow" in result.observations[0]["prompt"]


@pytest.mark.asyncio
async def test_llm_decide_parses_action() -> None:
    class Resp:
        content = '{"kind":"tool","name":"qa_mg","args":{"mg_def":{}},"reason":"check"}'
    class LLM:
        async def generate(self, **kwargs):
            return Resp()
    action = await AnimationAgentRuntime().decide_with_llm(LLM(), "test", [])
    assert action.kind == "tool"
    assert action.name == "qa_mg"
