"""联网能力的接线面（PRD web-search-fetch M3）：所有 stage agent 都能解析到 web_search / web_fetch。

为什么要锁：能力别名注册了，但只要有 Agent 绕开工具面直调 Service，主 agent 的轨迹里
就会出现"没调用过任何工具却拿到了搜索结果"的黑区。这里按 Agent 逐个验证解析，
并锁住"未配置联网时阶段 Agent 看不到 web_search"这条既有的可见性纪律。
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="module", autouse=True)
def _tools_registered():
    from clipwright.tool import register_builtin_tools
    from clipwright.tool.registry import ToolRegistry

    if ToolRegistry.get("web_search") is None:
        register_builtin_tools()
    yield


@pytest.fixture()
def web_enabled(monkeypatch):
    """开启联网（本地抓取 provider，不需要 key），模拟用户配置好之后的状态。"""
    from clipwright.config import settings

    monkeypatch.setattr(settings, "enable_web_search", True, raising=False)
    monkeypatch.setattr(settings, "web_search_provider", "local", raising=False)
    monkeypatch.setattr(settings, "web_search_allow_local", True, raising=False)
    return settings


def _agents():
    from clipwright.agents.animation_agent import AnimationAgent
    from clipwright.agents.audio_agent import AudioAgent
    from clipwright.agents.edit_agent import EditAgent
    from clipwright.agents.material_agent import MaterialAgent
    from clipwright.agents.quality_agent import QualityAgent
    from clipwright.agents.requirements_agent import RequirementsAgent
    from clipwright.agents.structure_agent import StructureAgent

    return [RequirementsAgent(), StructureAgent(), MaterialAgent(), EditAgent(),
            AnimationAgent(), AudioAgent(), QualityAgent()]


@pytest.mark.parametrize("capability", ["web_search", "web_fetch"])
def test_every_stage_agent_resolves_web_capability(capability, web_enabled):
    missing = [a.agent_name for a in _agents() if a.resolve_tool(capability) is None]
    assert missing == [], f"这些 Agent 解析不到 {capability}: {missing}"


def test_web_tools_are_agent_callable(web_enabled):
    from clipwright.tool.registry import ToolRegistry

    names = {t.name for t in ToolRegistry.list_agent_callable()}
    assert {"web_search", "web_fetch"} <= names


def test_web_fetch_always_resolves_without_search_config():
    """抓取不依赖搜索 key：没配联网也能抓指定 URL。"""
    from clipwright.agents.quality_agent import QualityAgent

    assert QualityAgent().resolve_tool("web_fetch") is not None


def test_web_search_hidden_when_not_configured(monkeypatch):
    """未配置联网时阶段 Agent 看不到 web_search（不白烧 token）。"""
    from clipwright.config import settings
    from clipwright.tool.registry import ToolRegistry

    monkeypatch.setattr(settings, "enable_web_search", False, raising=False)
    assert "web_search" not in {t.name for t in ToolRegistry.list_agent_callable()}


def test_web_capabilities_declared_in_capability_map():
    from clipwright.tool.capabilities import CAPABILITY_ALIASES

    assert CAPABILITY_ALIASES["web_search"] == ("web_search",)
    assert CAPABILITY_ALIASES["web_fetch"] == ("web_fetch",)
