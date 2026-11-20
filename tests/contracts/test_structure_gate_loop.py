"""B15：Structure 自检 Gate 与同 run 回灌修复。"""

from __future__ import annotations

import pytest

from clipwright.agents.structure_agent import StructureAgent, _scenes_from_script

SCRIPT = "第一句讲背景。第二句讲方法。第三句讲结果。第四句讲结论。"


def _agent() -> StructureAgent:
    return StructureAgent()


def test_gate_accepts_valid_scenes():
    scenes = _scenes_from_script(SCRIPT, 0)
    assert scenes
    problems = StructureAgent._scene_gate_problems(scenes, script_text=SCRIPT)
    assert problems == []


@pytest.mark.asyncio
async def test_repair_feeds_findings_back_and_recovers():
    agent = _agent()
    calls: list[str] = []

    async def generate(hint: str = ""):
        calls.append(hint)
        return _scenes_from_script(SCRIPT, 0), []

    scenes, warnings, problems = await agent._gate_repair_scenes(
        [], [], generate=generate, script_text=SCRIPT, max_repairs=2)
    assert len(calls) == 1
    assert "上一轮结构自检未通过" in calls[0]
    assert "没有可用场景" in calls[0]
    assert scenes and problems == []
    assert any("回灌" in w for w in warnings)


@pytest.mark.asyncio
async def test_repair_stops_when_generator_returns_nothing():
    agent = _agent()
    calls: list[str] = []

    async def generate(hint: str = ""):
        calls.append(hint)
        return [], []

    scenes, warnings, problems = await agent._gate_repair_scenes(
        [], [], generate=generate, script_text=SCRIPT, max_repairs=2)
    assert len(calls) == 1, "生成器给不出场景时不再空转"
    assert problems and "没有可用场景" in problems[0]
    assert any("回灌" in w for w in warnings)


@pytest.mark.asyncio
async def test_repair_retries_invalid_nonempty_up_to_budget():
    agent = _agent()
    calls: list[str] = []
    bad = [{"description": "缺 title"}]

    async def generate(hint: str = ""):
        calls.append(hint)
        return list(bad), []

    scenes, warnings, problems = await agent._gate_repair_scenes(
        [], [], generate=generate, script_text="", max_repairs=2)
    assert len(calls) == 2, "非空但无效的场景应占用满修复预算"
    assert problems, "修复预算用完必须回报未通过"
    assert scenes == bad
