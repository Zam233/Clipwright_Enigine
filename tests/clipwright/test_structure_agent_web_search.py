"""StructureAgent 联网工具接入测试（W3，见 docs/agent-search-cancel.md）。

核心结论：structure_agent 已通过 `ToolRegistry.list_agent_callable()` **动态收集**
所有 `agent_callable=True 且 is_available()` 的工具（见 execute L298-333），
无需任何生产代码改动。web_search / web_fetch 的 is_available() 由
`WebSearchService().is_configured()`（settings.enable_web_search + api_key）决定，
因此：

- 未配置联网 → with_tools 的 tools **不含** web_search / web_fetch
- 已配置联网 → with_tools 的 tools **包含** web_search / web_fetch
- LLM 不调用工具直接返回场景 JSON → 场景解析路径与现状一致（无回归）

本文件用回归测试锁定以上行为，防止后续硬编码工具列表时把联网工具遗漏。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from clipwright.agents.structure_agent import StructureAgent
from clipwright.config import settings
from clipwright.schema.agent import AgentContext, AgentDecision, StructureInput
from clipwright.tool import register_builtin_tools
from clipwright.tool.registry import ToolRegistry

# 有效分镜 JSON。description 均带动画标记 → _enrich_scene_animations 短路，
# 不会触发第二次 LLM 调用，使测试只聚焦 with_tools 的 tools 参数。
_SCENES_JSON = json.dumps(
    [
        {
            "title": "开场",
            "description": "引入话题 [文字动画]强调：关键结论",
            "keywords": ["话题", "引入"],
            "duration_sec": 30,
            "voiceover_script": "开场旁白",
            "visual_description": {"material_library": "auto", "material_content": "画面"},
        },
        {
            "title": "论证",
            "description": '数据论证 [逻辑动画]mg_dynamic:{"description":"柱状图","text":"A|B"}',
            "keywords": ["数据", "论证"],
            "duration_sec": 60,
            "voiceover_script": "论证旁白",
            "visual_description": {},
        },
    ],
    ensure_ascii=False,
)


class _FakeResponse:
    """模拟 LLMResponse 最小接口（with_tools 返回对象，无工具调用）。"""

    def __init__(self, content: str) -> None:
        self.content = content
        self.success = True
        self.tool_calls: list[Any] = []
        self.status_code = 200


class FakeLLM:
    """记录 with_tools 收到的 tools 参数，并返回预设场景 JSON（无工具调用）。"""

    def __init__(self) -> None:
        self.tool_names: list[list[str]] = []
        self.last_usage: dict[str, int] | None = None

    async def with_tools(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        tool_executor: Any,
        tools: list[dict[str, Any]],
        pipeline_id: str | None = None,
        **kwargs: Any,
    ) -> _FakeResponse:
        self.tool_names.append([t["function"]["name"] for t in tools])
        return _FakeResponse(_SCENES_JSON)


@pytest.fixture(autouse=True)
def _register_builtin_tools() -> None:
    """确保内置工具（含 web_search/web_fetch）已注册；list_agent_callable()
    每次调用都会实时评估 is_available()，故注册一次后由 monkeypatch 控制开关。"""
    register_builtin_tools()


def _agent() -> StructureAgent:
    return StructureAgent()


def _context() -> AgentContext:
    return AgentContext(
        pipeline_id="w3-test",
        persona_id="p_test",
        category_plugin_id="",
        topic="测试选题",
    )


def _input() -> StructureInput:
    return StructureInput(
        context=_context(),
        persona_config={
            "identity": {"tone": "neutral"},
            "language": {"academic_density": 0.1, "max_sentence_len": 30},
            "rhythm": {"cut_profile": "even_flow"},
            "constraints": {"max_duration_sec": 900},
        },
    )


def _configure_llm(monkeypatch: pytest.MonkeyPatch) -> FakeLLM:
    """使 execute 走 with_tools 分支（llm_api_key 非空），并注入 FakeLLM。"""
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    agent = _agent()
    fake = FakeLLM()
    agent._llm = fake  # type: ignore[assignment]
    return agent, fake


# ── Test A/B：with_tools 的 tools 参数随配置开关联动 ──────────────────────


async def test_tools_exclude_web_when_not_configured(monkeypatch) -> None:
    """未配置联网（enable_web_search=False / api_key=''）→ tools 不含 web_search/web_fetch。"""
    monkeypatch.setattr(settings, "enable_web_search", False)
    monkeypatch.setattr(settings, "web_search_api_key", "")
    agent, fake = _configure_llm(monkeypatch)

    out = await agent.execute(_input(), _context())

    assert fake.tool_names, "with_tools 应被调用（llm_api_key 已配置）"
    names = fake.tool_names[-1]
    assert "web_search" not in names
    assert "web_fetch" not in names
    # 走 with_tools 分支且场景解析正常
    assert out.decision == AgentDecision.PASS
    assert out.scenes


async def test_tools_include_web_when_configured(monkeypatch) -> None:
    """已配置联网（enable_web_search=True + api_key）→ tools 包含 web_search/web_fetch。"""
    monkeypatch.setattr(settings, "enable_web_search", True)
    monkeypatch.setattr(settings, "web_search_api_key", "k")
    agent, fake = _configure_llm(monkeypatch)

    out = await agent.execute(_input(), _context())

    assert fake.tool_names, "with_tools 应被调用（llm_api_key 已配置）"
    names = fake.tool_names[-1]
    assert "web_search" in names
    assert "web_fetch" in names
    assert out.decision == AgentDecision.PASS
    assert out.scenes


# ── Test C：LLM 无工具调用 → 场景解析 fallback 路径不变 ───────────────────


async def test_no_tool_call_parses_scenes_unchanged(monkeypatch) -> None:
    """LLM 直接返回场景 JSON（不调用任何工具）→ 场景正常解析，无 error。"""
    monkeypatch.setattr(settings, "enable_web_search", True)
    monkeypatch.setattr(settings, "web_search_api_key", "k")
    agent, fake = _configure_llm(monkeypatch)

    out = await agent.execute(_input(), _context())

    assert out.decision == AgentDecision.PASS
    assert out.error is None
    assert len(out.scenes) == 2
    assert out.scenes[0]["title"] == "开场"
    assert out.scenes[1]["title"] == "论证"
    assert out.script_skeleton["scene_count"] == 2


# ── Test D：ToolRegistry.list_agent_callable() 门控（轻量版 A/B）──────────


def test_registry_gates_web_tools_by_config(monkeypatch) -> None:
    """list_agent_callable()：未配置排除 web 工具，配置后包含。"""
    monkeypatch.setattr(settings, "enable_web_search", False)
    monkeypatch.setattr(settings, "web_search_api_key", "")
    names_off = [t.name for t in ToolRegistry.list_agent_callable()]
    assert "web_search" not in names_off
    assert "web_fetch" not in names_off

    monkeypatch.setattr(settings, "enable_web_search", True)
    monkeypatch.setattr(settings, "web_search_api_key", "k")
    names_on = [t.name for t in ToolRegistry.list_agent_callable()]
    assert "web_search" in names_on
    assert "web_fetch" in names_on
