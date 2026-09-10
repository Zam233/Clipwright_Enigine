"""A-1 门面转调测试 —— 注册全 kind → 回收 → 注册表回到初始状态（M2 验收）。"""
from __future__ import annotations

import pytest

from clipwright.plugins.contribution import (
    Contribution,
    ContributionKind,
    ContributionRegistry,
)
from clipwright.plugins.contribution_dispatch import _WIRED_KINDS

TOOL_ID = "audit_dispatch_tool"
SKILL_ID = "audit_dispatch_skill"
AGENT_ID = "audit_dispatch_agent"
SOURCE_ID = "audit_dispatch_source"
CATEGORY_ID = "audit_dispatch_cat"


class FakeTool:
    name = TOOL_ID
    description = "dispatch 测试工具"
    required_tools = []

    async def run(self):
        return None


class FakeSkill:
    name = SKILL_ID
    description = "dispatch 测试技能"
    required_tools = []

    def to_llm_tool(self, fmt="anthropic"):
        return {"name": self.name, "description": self.description, "input_schema": {}}

    def _infer_parameters(self):
        return {}

    def is_available(self):
        return True

    async def run(self):
        return None


class FakeAgent:
    agent_name = AGENT_ID

    async def run(self):
        return None


class FakeMaterialSource:
    source_id = SOURCE_ID
    source_name = "dispatch 源"

    async def search(self, query, **kwargs):
        return []


class FakeCategory:
    plugin_id = CATEGORY_ID
    display_name = "dispatch 类型"
    description = ""

    def translate_persona(self, params):
        return {}


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    ContributionRegistry.unregister_plugin("dispatch_probe")


def _probe_items():
    return [
        Contribution(ContributionKind.TOOL, TOOL_ID, FakeTool()),
        Contribution(ContributionKind.SKILL, SKILL_ID, FakeSkill()),
        Contribution(ContributionKind.AGENT, AGENT_ID, FakeAgent()),
        Contribution(ContributionKind.MATERIAL_SOURCE, SOURCE_ID, FakeMaterialSource()),
        Contribution(ContributionKind.CATEGORY, CATEGORY_ID, FakeCategory()),
        Contribution(ContributionKind.HOOK, "audit_dispatch_hook",
                     _noop, meta={"point": "pre_agent"}),
        Contribution(ContributionKind.PROMPT, "audit_dispatch_prompt", "text",
                     meta={"agent": "structure", "prompt": "测试片段"}),
    ]


def _noop(*args, **kwargs):
    return None


class TestDispatchRoundTrip:
    def test_all_wired_kinds_register_and_recycle(self):
        items = _probe_items()
        report = ContributionRegistry.register("dispatch_probe", items)
        assert report.ok, f"注册被拒: {report.rejected}"

        from clipwright.tool.registry import ToolRegistry
        from clipwright.skill.registry import SkillRegistry
        from clipwright.agents.registry import AgentRegistry
        from clipwright.material.registry import MaterialRegistry
        from clipwright.category.registry import CategoryRegistry

        assert ToolRegistry.get(TOOL_ID) is not None
        assert SkillRegistry.get(SKILL_ID) is not None
        assert AgentRegistry.get(AGENT_ID) is not None
        assert MaterialRegistry.get(SOURCE_ID) is not None
        assert CategoryRegistry.get(CATEGORY_ID) is not None

        recycled = ContributionRegistry.unregister_plugin("dispatch_probe")
        assert len(recycled) == len(items)
        assert ToolRegistry.get(TOOL_ID) is None
        assert SkillRegistry.get(SKILL_ID) is None
        assert AgentRegistry.get(AGENT_ID) is None
        assert MaterialRegistry.get(SOURCE_ID) is None
        assert CategoryRegistry.get(CATEGORY_ID) is None
        assert ContributionRegistry.list() == []

    def test_wired_kinds_covers_all_a_kinds(self):
        expect = {
            ContributionKind.TOOL, ContributionKind.SKILL, ContributionKind.AGENT,
            ContributionKind.MATERIAL_SOURCE, ContributionKind.CATEGORY,
            ContributionKind.STYLE_INTERPRETER, ContributionKind.HOOK,
            ContributionKind.PROMPT,
        }
        assert _WIRED_KINDS == expect

    def test_agent_registration_failure_is_reported(self):
        class CoreNameAgent:
            agent_name = "structure"

            async def run(self):
                return None

        report = ContributionRegistry.register("dispatch_probe", [
            Contribution(ContributionKind.AGENT, "structure", CoreNameAgent()),
        ])
        assert not report.ok
        assert "注册失败" in report.rejected[0][1]
        assert ContributionRegistry.resolve(ContributionKind.AGENT, "structure") is None
