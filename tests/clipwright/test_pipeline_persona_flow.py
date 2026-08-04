"""T9 (C5)：persona_prompt / vision_prompt 注入 structure + animation agents 测试。

验证管线把 Persona 的 persona_prompt 与 vision_prompt 一路从 _build_input →
Input schema → StructureAgent / AnimationAgent（→ MGGenerator）传递到位，
且空/None vision_prompt 保持空串注入、不崩溃。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from clipwright.animation.mg.generator import MGGenerator
from clipwright.config import settings
from clipwright.schema.agent import (
    AgentContext,
    AgentDecision,
    AnimationInput,
    StructureInput,
)
from clipwright.schema.timeline import Timeline
from clipwright.services.pipeline_v2 import PipelineOrchestratorV2


# ── pipeline_v2._build_input ──────────────────────────────


def _orchestrator() -> PipelineOrchestratorV2:
    # __new__ 跳过 _agents 构造（避免实例化全部 Agent 的额外开销）
    return PipelineOrchestratorV2.__new__(PipelineOrchestratorV2)


class TestBuildInputPrompts:
    """_build_input 把 persona_prompt / vision_prompt 注入 structure / animation 输入。"""

    def test_animation_includes_persona_and_vision_prompt(self) -> None:
        orch = _orchestrator()
        orch._persona_prompt = "人格提示语"
        orch._vision_prompt = "视觉需求提示语"
        inputs = orch._build_input(
            "animation", {"timeline": {"id": "tl", "tracks": []}}, {}, None,
        )
        assert inputs["persona_prompt"] == "人格提示语"
        assert inputs["vision_prompt"] == "视觉需求提示语"

    def test_structure_includes_persona_and_vision_prompt(self) -> None:
        orch = _orchestrator()
        orch._persona_prompt = "人格提示语"
        orch._vision_prompt = "视觉需求提示语"
        inputs = orch._build_input("structure", {}, {}, None)
        assert inputs["persona_prompt"] == "人格提示语"
        assert inputs["vision_prompt"] == "视觉需求提示语"

    def test_empty_vision_prompt_injects_empty_string(self) -> None:
        orch = _orchestrator()
        orch._persona_prompt = "人格提示语"
        orch._vision_prompt = ""
        inputs = orch._build_input(
            "animation", {"timeline": {"id": "tl", "tracks": []}}, {}, None,
        )
        assert inputs["vision_prompt"] == ""

    def test_none_vision_prompt_injects_empty_string(self) -> None:
        orch = _orchestrator()
        orch._vision_prompt = None
        inputs = orch._build_input(
            "animation", {"timeline": {"id": "tl", "tracks": []}}, {}, None,
        )
        assert inputs["vision_prompt"] == ""


# ── schema 序列化 ─────────────────────────────────────────


class TestInputSchema:
    """AnimationInput / StructureInput 序列化新的 prompt 字段。"""

    def test_animation_input_serializes_prompts(self) -> None:
        inp = AnimationInput(
            context=AgentContext(
                pipeline_id="p1", persona_id="x", category_plugin_id="y", topic="z",
            ),
            timeline=Timeline(),
            persona_prompt="人格提示语",
            vision_prompt="视觉需求提示语",
        )
        data = inp.model_dump()
        assert data["persona_prompt"] == "人格提示语"
        assert data["vision_prompt"] == "视觉需求提示语"

    def test_animation_input_optional_fields_default_none(self) -> None:
        inp = AnimationInput(
            context=AgentContext(
                pipeline_id="p1", persona_id="x", category_plugin_id="y", topic="z",
            ),
            timeline=Timeline(),
        )
        data = inp.model_dump()
        assert data.get("persona_prompt") is None
        assert data.get("vision_prompt") is None

    def test_structure_input_serializes_vision_prompt(self) -> None:
        inp = StructureInput(
            context=AgentContext(
                pipeline_id="p1", persona_id="x", category_plugin_id="y", topic="z",
            ),
            vision_prompt="视觉需求提示语",
        )
        assert inp.model_dump()["vision_prompt"] == "视觉需求提示语"


# ── MGGenerator vision_prompt 穿透 ────────────────────────


def _valid_def() -> dict:
    """合法 MG 定义（通过 validate_mg_json 且可渲染）。"""
    return {
        "animation_id": "mg_generated_persona_flow_test",
        "name": "测试动画",
        "description": "persona flow 测试",
        "duration_sec": 3.0,
        "width": 1920,
        "height": 1080,
        "style": {"background": "transparent", "font_family": "sans-serif"},
        "params": {"text": {"type": "string", "default": "世界"}},
        "elements": [
            {
                "type": "text",
                "content": "Hello {text}",
                "x": "center",
                "y": "center",
                "font_size": 48,
                "font_color": "#ffffff",
                "keyframes": [
                    {"time": 0, "opacity": 0, "scale": 0.3},
                    {"time": 0.5, "opacity": 1, "scale": 1.0},
                    {"time": 2.8, "opacity": 0, "translate_y": -20},
                ],
            },
        ],
    }


class TestMGGeneratorVisionPrompt:
    """MGGenerator 把 vision_prompt 从 generate 穿透到 _call_llm 上下文。"""

    @pytest.mark.asyncio
    async def test_generate_threads_vision_prompt_to_call_llm(self, monkeypatch) -> None:
        g = MGGenerator()
        captured: dict[str, Any] = {}

        async def fake_call_llm(
            description, text_content, persona_style,
            scene_context, category_context, vision_prompt="",
        ):
            captured["vision_prompt"] = vision_prompt
            captured["context"] = MGGenerator._build_context_section(
                persona_style, category_context, vision_prompt=vision_prompt,
            )
            return _valid_def()

        async def fake_critique(*args, **kwargs):
            return None

        monkeypatch.setattr(g, "_call_llm", fake_call_llm)
        monkeypatch.setattr(g, "_critique_quality", fake_critique)
        result = await g.generate("测试描述", "测试文字", vision_prompt="视觉需求Y")
        assert result["success"] is True
        assert captured["vision_prompt"] == "视觉需求Y"
        assert "## 视觉需求" in captured["context"]
        assert "视觉需求Y" in captured["context"]

    def test_build_context_section_with_vision_prompt(self) -> None:
        ctx = MGGenerator._build_context_section(
            {"primary_color": "#fff"}, {}, vision_prompt="视觉需求文本",
        )
        assert "## 视觉需求（vision_prompt）" in ctx
        assert "视觉需求文本" in ctx

    def test_build_context_section_empty_vision_prompt_no_section(self) -> None:
        ctx = MGGenerator._build_context_section({}, {})
        assert "视觉需求" not in ctx


# ── StructureAgent system prompt ──────────────────────────


class _CaptureLLM:
    """记录 with_tools 的 system_prompt 并返回合法场景列表。"""

    def __init__(self, content: str) -> None:
        self._content = content
        self.system_prompt = ""
        self.last_usage = None

    async def with_tools(self, **kwargs: Any) -> Any:
        self.system_prompt = kwargs.get("system_prompt", "")
        return type("_Result", (), {"content": self._content})()


class TestStructureAgentVisionPrompt:
    """StructureAgent 在 vision_prompt 提供时把「## Vision Prompt」段注入 system prompt。"""

    @pytest.mark.asyncio
    async def test_execute_system_prompt_includes_vision_prompt(self, monkeypatch) -> None:
        from clipwright.agents.structure_agent import StructureAgent

        agent = StructureAgent()
        scenes = [{
            "title": "开场",
            "description": "引入话题 [文字动画]淡入：你好",
            "keywords": ["话题", "引入"],
            "duration_sec": 30,
            "voiceover_script": "旁白",
            "visual_description": {},
        }]
        fake = _CaptureLLM(json.dumps(scenes, ensure_ascii=False))
        agent._llm = fake  # type: ignore[assignment]
        monkeypatch.setattr(settings, "llm_api_key", "test-key")

        context = AgentContext(
            pipeline_id="t9-test",
            persona_id="p_test",
            category_plugin_id="",
            topic="测试选题",
        )
        inp = StructureInput(
            context=context,
            persona_prompt="人格提示语",
            vision_prompt="视觉需求提示语",
        )
        out = await agent.execute(inp, context)

        assert "## Vision Prompt" in fake.system_prompt
        assert "视觉需求提示语" in fake.system_prompt
        # Persona Prompt 注入保持不回归
        assert "## Persona Prompt" in fake.system_prompt
        assert "人格提示语" in fake.system_prompt
        assert out.decision == AgentDecision.PASS
        assert out.scenes
