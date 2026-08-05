"""Q2 (T2) MG 动画符合 Persona 视觉色板测试。

覆盖计划 acceptance criteria：
- Zam parameter.yaml 不变（primary/accent 仍 null、palette 仍在）
- _resolve_style(Zam visual) 注入+override 后 primary_color=="#000000"（非蓝）
- 兜底注入：无显式色 → interpret 收到 config 含 #000000/#FFFFFF/#FF0000
- 剥离 palette 后走 has_exact 快路径（LLM 未调）；未剥离时走 LLM 但 override 保证非蓝
- _build_context_section 输出含 `## 最终约束` 块，颜色 == 注入色板（无 #3b82f6/#4f8cff）
- material_agent 回归：_extract_persona_style(Zam) 仍含 palette 关键词
"""

from __future__ import annotations

import json
from pathlib import Path
import types

import pytest
import yaml

from clipwright.agents.animation_agent import AnimationAgent
from clipwright.agents.material_agent import MaterialAgent
from clipwright.animation.mg.generator import MGGenerator
from clipwright.services.style_interpreter import StyleInterpreter

_REPO = Path(__file__).resolve().parents[2]
_ZAM_YAML = _REPO / "personas" / "Zam" / "parameter.yaml"


def _zam_persona_config() -> dict:
    data = yaml.safe_load(_ZAM_YAML.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _zam_visual() -> dict:
    return dict(_zam_persona_config()["visual"])


class _LLMCallState:
    def __init__(self) -> None:
        self.calls: list[dict] = []


def _install_fake_llm(monkeypatch, state: _LLMCallState, resp_payload: dict) -> None:
    """替换 LLMService.ask：记录调用并返回固定 JSON。"""
    class _FakeLLM:
        async def ask(self, prompt, **kwargs):
            state.calls.append({"prompt": prompt, "kwargs": kwargs})
            content = json.dumps(resp_payload, ensure_ascii=False)
            return types.SimpleNamespace(success=True, content=content)
    monkeypatch.setattr("clipwright.services.llm.LLMService", _FakeLLM)


class TestZamYamlUntouched:
    def test_primary_accent_null_palette_present(self) -> None:
        visual = _zam_visual()
        assert visual.get("primary_color") is None
        assert visual.get("accent_color") is None
        assert visual.get("palette")  # 非空：material_agent 素材搜索依赖


class TestResolveStyleInjection:
    @pytest.mark.asyncio
    async def test_zam_resolves_black_white_red_fast_path(self, monkeypatch) -> None:
        """剥离 palette 后走 has_exact 快路径 → LLM 未调，输出黑白红（非蓝）。"""
        state = _LLMCallState()
        _install_fake_llm(monkeypatch, state, {"primary_color": "#3b82f6"})
        agent = AnimationAgent()
        style = await agent._resolve_style(_zam_visual(), {})
        assert style["primary_color"] == "#000000"
        assert style["secondary_color"] == "#FFFFFF"
        assert style["accent_color"] == "#FF0000"
        # 快路径：LLM 分支未调用
        assert state.calls == []

    @pytest.mark.asyncio
    async def test_injection_reaches_interpret_when_no_explicit_colors(self, monkeypatch) -> None:
        """兜底注入：空 visual_config → interpret 收到 #000000/#FFFFFF/#FF0000。"""
        captured: dict = {}

        async def fake_interpret(config, persona_context):
            captured["config"] = dict(config)
            return dict(config)
        monkeypatch.setattr(StyleInterpreter, "interpret", fake_interpret)
        agent = AnimationAgent()
        result = await agent._resolve_style({}, {})
        assert result.get("primary_color") == "#000000"
        assert result.get("secondary_color") == "#FFFFFF"
        assert result.get("accent_color") == "#FF0000"

    @pytest.mark.asyncio
    async def test_llm_path_override_forces_non_blue(self, monkeypatch) -> None:
        """未剥离 palette 走 LLM（mock 返回蓝）→ override 仍盖回 #000000。"""
        state = _LLMCallState()
        _install_fake_llm(monkeypatch, state, {
            "primary_color": "#3b82f6", "secondary_color": "#64748b",
            "accent_color": "#f59e0b", "text_color": "#f1f5f9",
        })
        config = _zam_visual()
        config["primary_color"] = "#000000"
        config["secondary_color"] = "#FFFFFF"
        config["accent_color"] = "#FF0000"
        result = await StyleInterpreter.interpret(config, {})
        assert state.calls, "palette 存在时走 LLM 分支"
        assert result["primary_color"] == "#000000"
        assert result["secondary_color"] == "#FFFFFF"
        assert result["accent_color"] == "#FF0000"


class TestBuildContextSectionHardConstraint:
    def test_hard_constraint_present_with_injected_palette(self) -> None:
        persona_style = {
            "primary_color": "#000000",
            "secondary_color": "#FFFFFF",
            "accent_color": "#FF0000",
        }
        section = MGGenerator._build_context_section(persona_style, {})
        assert "## 最终约束" in section
        assert "#000000" in section
        assert "#FFFFFF" in section
        assert "#FF0000" in section
        assert "#3b82f6" not in section
        assert "#4f8cff" not in section
        assert "禁止默认蓝紫科技渐变" in section
        # 最终约束位于所有内容段落之后（在本例中即末尾内容块）
        assert section.rstrip().endswith("禁止默认蓝紫科技渐变、发光粒子、彩虹渐变。")

    def test_hard_constraint_appears_after_style_guidance(self) -> None:
        """约束覆盖「简报动画风格」「动画风格指引」：出现在它们之后。"""
        persona_style = {
            "primary_color": "#000000", "secondary_color": "#FFFFFF",
            "accent_color": "#FF0000",
        }
        category_context = {
            "display_name": "测试",
            "mg_style_guidance": "使用蓝色渐变科技风",
            "brief_animation_style": {"style": "蓝色渐变", "tone": "科技"},
        }
        section = MGGenerator._build_context_section(persona_style, category_context)
        assert section.index("## 最终约束") > section.index("简报动画风格")
        assert section.index("## 最终约束") > section.index("动画风格指引")


class TestMaterialAgentPaletteRegression:
    def test_extract_persona_style_still_uses_palette(self) -> None:
        persona_config = _zam_persona_config()
        style = MaterialAgent._extract_persona_style(persona_config)
        assert "黑白" in style  # palette 关键词仍在（未被清空/破坏）
