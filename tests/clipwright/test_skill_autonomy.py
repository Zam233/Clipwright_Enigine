"""Skill 自主调用/事件契约的定向回归（2026-11-14 skill 插件化 M1–M2）。

- 主 agent 事件：`_skill_meta` 必须把技能与普通工具分开（kind=skill）；
- 子 agent 工具面：`BaseAgent.tool_schemas` 默认并入可用技能，可显式关闭；
- 无技能时 `run_skill_round` 必须 skipped（不阻塞阶段、不调 LLM）。
"""

from __future__ import annotations

import pytest

from clipwright.agents.base import BaseAgent
from clipwright.schema.skill import SkillExecResult, SkillStatus
from clipwright.services.agent_loop import _skill_meta
from clipwright.skill.base import BaseSkill
from clipwright.skill.registry import SkillRegistry


class _ProbeSkill(BaseSkill):
    name = "audit_probe_skill"
    description = "审计用技能"
    required_tools: list[str] = []

    async def execute(self, **kwargs) -> SkillExecResult:  # noqa: ANN003
        return SkillExecResult(status=SkillStatus.SUCCESS, skill_name=self.name)


class _ProbeAgent(BaseAgent):
    agent_name = "probe_agent"
    description = "probe"

    async def execute(self, input_data=None, context=None):  # noqa: ANN001
        return None


@pytest.fixture
def probe_skill():
    SkillRegistry.register(_ProbeSkill())
    try:
        yield
    finally:
        SkillRegistry.unregister("audit_probe_skill")


def test_skill_meta_marks_skill(probe_skill):
    assert _skill_meta("audit_probe_skill") == {
        "kind": "skill",
        "skill": "audit_probe_skill",
    }
    assert _skill_meta("read_file")["kind"] == "tool"


def test_agent_tool_schemas_include_skills(probe_skill):
    agent = _ProbeAgent()
    names = {
        str((s.get("function") or {}).get("name") or "")
        for s in agent.tool_schemas(include_skills=True)
    }
    assert "audit_probe_skill" in names
    # 显式关闭时不并入
    names_no_skill = {
        str((s.get("function") or {}).get("name") or "")
        for s in agent.tool_schemas(include_skills=False)
    }
    assert "audit_probe_skill" not in names_no_skill


@pytest.mark.asyncio
async def test_run_skill_round_skips_without_skills():
    agent = _ProbeAgent()
    result = await agent.run_skill_round("什么也不做")
    assert result["status"] == "skipped"
    assert result["tool_calls"] == []
