import pytest

from clipwright.animation.agentic_loop import AgentAction, run_agentic_loop


@pytest.mark.asyncio
async def test_tool_then_final() -> None:
    seq = [AgentAction("tool", "qa_mg"), AgentAction("final")]
    async def decide(_obs):
        return seq.pop(0)
    async def execute(name, args):
        return {"name": name}
    result = await run_agentic_loop(decide=decide, execute_tool=execute)
    assert result.status == "ok"
    assert result.rounds == 2


@pytest.mark.asyncio
async def test_skill_load_then_final() -> None:
    seq = [AgentAction("skill", "flow"), AgentAction("final")]
    async def decide(_obs):
        return seq.pop(0)
    async def execute(name, args):
        return None
    async def load(name):
        return {"skill": name}
    result = await run_agentic_loop(decide=decide, execute_tool=execute, load_skill=load)
    assert result.status == "ok"
    assert result.observations[0]["skill"] == "flow"


@pytest.mark.asyncio
async def test_max_rounds_caps() -> None:
    async def decide(_obs):
        return AgentAction("tool", "qa_mg")
    async def execute(name, args):
        return {}
    result = await run_agentic_loop(decide=decide, execute_tool=execute, max_rounds=2)
    assert result.status == "capped"
    assert result.rounds == 2
