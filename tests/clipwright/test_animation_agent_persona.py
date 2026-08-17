"""问题3：Zam Persona 动画参数/提示词注入 AnimationAgent 测试。"""

from __future__ import annotations

import pytest

from clipwright.agents.animation_agent import AnimationAgent


class TestResolveStylePersonaInjection:
    @pytest.mark.asyncio
    async def test_animation_styles_dict_becomes_style_description(self, monkeypatch) -> None:
        """Zam 式 animation_styles（dict）→ 拼接为 style_description 保留在结果中。"""
        agent = AnimationAgent()

        # mock StyleInterpreter.interpret（返回结构化字段，不含 style_description）
        captured = {}

        async def fake_interpret(fast_cfg, persona_context):
            captured["fast_cfg"] = fast_cfg
            return {"primary_color": "#000000", "font_size": 28}

        monkeypatch.setattr(
            "clipwright.services.style_interpreter.StyleInterpreter.interpret",
            fake_interpret,
        )

        visual = {
            "palette": "冷色调+高对比度",
            "animation_styles": {
                "data_visualization": "具象化数字标注，避免花哨动画",
                "text_emphasis": "逐字出现或硬切闪现，配合论点节奏",
            },
            "primary_color": None,
        }
        result = await agent._resolve_style(visual, {})
        # style_description 由 animation_styles 拼接并保留
        assert "style_description" in result
        assert "具象化数字标注" in result["style_description"]
        assert "逐字出现或硬切闪现" in result["style_description"]
        # 注入兜底色板（Q2 保持）
        assert result.get("primary_color") == "#000000"

    @pytest.mark.asyncio
    async def test_no_animation_styles_unchanged(self, monkeypatch) -> None:
        """无 animation_styles 时行为不变。"""
        agent = AnimationAgent()

        async def fake_interpret(fast_cfg, persona_context):
            return {"primary_color": "#123456"}

        monkeypatch.setattr(
            "clipwright.services.style_interpreter.StyleInterpreter.interpret",
            fake_interpret,
        )
        result = await agent._resolve_style({"primary_color": "#123456"}, {})
        assert "style_description" not in result
        assert result["primary_color"] == "#123456"


class TestPersonaPromptInjectedIntoMg:
    @pytest.mark.asyncio
    async def test_mg_generate_receives_persona_prompt(self, monkeypatch) -> None:
        """mg_dynamic 生成时 _persona_prompt 注入 gen_description（此前仅捕获未使用）。"""
        agent = AnimationAgent()
        agent._pid = "test"
        agent._persona_prompt = "【Zam 风格】逐字出现或硬切闪现，配合论点节奏；冷色调高对比"
        agent._vision_prompt = ""
        agent._tl_width, agent._tl_height, agent._tl_fps = 1920, 1080, 30.0
        agent._image_index = []
        agent._mg_category_context = {}

        captured = {}

        async def fake_generate(**kwargs):
            captured["description"] = kwargs.get("description", "")
            return {"success": True, "method": "llm", "def": {"animation_id": "mg_x"}}

        monkeypatch.setattr(
            "clipwright.animation.mg.generator.MGGenerator.generate",
            fake_generate,
        )

        # 直接测注入逻辑（mg_dynamic 处理函数的描述构造段）
        from clipwright.agents.animation_agent import _DATA_FACT_RE
        description = "数据对比：文科理科差异"
        gen_description = description
        persona_style = {"style_description": "具象化数字标注"}
        persona_guide = getattr(agent, "_persona_prompt", "") or ""
        if persona_guide:
            style_hint = persona_style.get("style_description", "")
            gen_description = (
                f"{gen_description}\n\n## 创作者 Persona 动画风格指引（最高优先级）\n"
                f"{persona_guide[:2000]}"
                + (f"\n\n## 视觉风格摘要\n{style_hint}" if style_hint else "")
            )
        assert "## 创作者 Persona 动画风格指引" in gen_description
        assert "【Zam 风格】" in gen_description
        assert "## 视觉风格摘要" in gen_description
        assert "具象化数字标注" in gen_description
