"""能力解析层（capability → 实时工具/技能）单元测试。

背景（PRD F10 + 用户要求）：Agent 不再写死工具名，而是声明**能力**；
插件只要给工具设置 ``capability`` 就能接管，且增删/配置变化在下一次调用
立刻生效（不需要改 Agent 源码）。
"""

from __future__ import annotations

import pytest

from clipwright.agents.base import BaseAgent
from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.tool import register_builtin_tools
from clipwright.tool.base import BaseTool
from clipwright.tool.capabilities import (
    candidates,
    explain,
    fallback_name,
    resolve,
    resolve_name,
)
from clipwright.tool.registry import ToolRegistry


class _FakeFrameTool(BaseTool):
    name = "test_frame_tool"
    description = "fake frame checker"
    capability = "frame_check"
    capability_priority = 50

    async def execute(self, video_url: str = "", **kwargs):
        return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name,
                              output={"valid": True, "from": "plugin"})


class _UnconfiguredTool(BaseTool):
    name = "test_unconfigured_tool"
    description = "never usable"
    capability = "frame_check"
    capability_priority = 99

    def is_configured(self) -> bool:
        return False

    async def execute(self, **kwargs):
        return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name, output={})


class _Agent(BaseAgent):
    agent_name = "test_agent"
    capability_prefer: dict[str, tuple[str, ...]] = {}

    async def execute(self, input_data, context):  # pragma: no cover - 不参与测试
        raise NotImplementedError


@pytest.fixture(autouse=True)
def _builtin_tools():
    if not ToolRegistry.list():
        register_builtin_tools()
    yield
    for name in ("test_frame_tool", "test_unconfigured_tool"):
        ToolRegistry.unregister(name)


def test_alias_resolution_uses_builtin() -> None:
    """没有任何插件时，能力名落到内置工具（行为与改造前一致）。"""
    tool = resolve("frame_check")
    assert tool is not None and tool.name == "frame_validator"
    assert fallback_name("frame_check") == "frame_validator"


def test_declared_capability_wins() -> None:
    """插件声明 capability 后接管该能力，且优先级高于内置。"""
    ToolRegistry.register(_FakeFrameTool(), plugin_id="test_plugin")
    tool = resolve("frame_check")
    assert tool is not None and tool.name == "test_frame_tool"
    names = [t.name for t in candidates("frame_check")]
    assert names[0] == "test_frame_tool" and "frame_validator" in names


def test_unconfigured_candidate_is_filtered_with_reason() -> None:
    ToolRegistry.register(_UnconfiguredTool(), plugin_id="test_plugin")
    tool = resolve("frame_check")
    # 优先级 99 但未配置 → 跳过，回落到内置
    assert tool is not None and tool.name == "frame_validator"
    info = explain("frame_check")
    row = next(r for r in info["candidates"] if r["name"] == "test_unconfigured_tool")
    assert row["usable"] is False and "未配置" in row["reason"]
    assert info["chosen"] == "frame_validator"


def test_prefer_only_when_usable() -> None:
    assert resolve_name("frame_check", prefer=["not_a_tool"]) is not None


@pytest.mark.asyncio
async def test_agent_call_tool_falls_back_to_alias(monkeypatch) -> None:
    """能力当前无实例时，仍按内置别名发起调用（保住既有失败语义/测试可 mock）。"""
    seen: list[str] = []

    async def _fake_execute(name: str, **kwargs):
        seen.append(name)
        return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=name, output={"ok": True})

    monkeypatch.setattr(ToolRegistry, "execute", _fake_execute)
    agent = _Agent()
    result = await agent.call_tool("subtitle_burn", video_path="v.mp4", subtitles=[])
    assert result.status == "success"
    assert seen == ["subtitle_burn"]


@pytest.mark.asyncio
async def test_agent_call_tool_unknown_capability() -> None:
    agent = _Agent()
    result = await agent.call_tool("no_such_capability_xyz")
    assert result.status == ToolStatus.NOT_FOUND
    assert "无可用实现" in (result.error or "")


def test_tool_schemas_respects_whitelist_and_availability() -> None:
    agent = _Agent()
    agent.tool_whitelist = ("frame_validator", "does_not_exist", "test_unconfigured_tool")
    ToolRegistry.register(_UnconfiguredTool(), plugin_id="test_plugin")
    names = [s["function"]["name"] for s in agent.tool_schemas()]
    assert names == ["frame_validator"]


def test_capability_report_lists_decision() -> None:
    agent = _Agent()
    agent.capability_prefer = {"frame_check": ()}
    report = agent.capability_report()
    assert report["agent"] == "test_agent"
    assert report["tools"][0]["capability"] == "frame_check"
    assert report["tools"][0]["chosen"] == "frame_validator"
