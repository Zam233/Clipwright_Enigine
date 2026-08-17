"""验证 vision_prompt.md 完整注入链路：loader → MG 生成描述。

覆盖：
1. Zam vision_prompt.md 可被 loader 加载（manifest.vision_prompt）
2. MGGenerator._build_context_section 含「## 视觉需求（vision_prompt）」段
3. 文字动画/字幕样式消费点是否也应注入 vision_prompt（问题3 补充）
"""

from __future__ import annotations

import pytest

from clipwright.animation.mg.generator import MGGenerator


class TestVisionPromptInjection:
    def test_build_context_section_includes_vision_prompt(self) -> None:
        """vision_prompt 非空时注入「## 视觉需求」段（优先级最高）。"""
        section = MGGenerator._build_context_section(
            {"primary_color": "#000000"},
            {},
            vision_prompt="纯黑纯白底，红色强调动词",
        )
        assert "## 视觉需求（vision_prompt）" in section
        assert "纯黑纯白底" in section
        assert "优先级高于默认设计" in section

    def test_build_context_section_no_vision_prompt(self) -> None:
        """vision_prompt 为空时不注入该段。"""
        section = MGGenerator._build_context_section({"primary_color": "#000000"}, {})
        assert "视觉需求" not in section

    def test_zam_vision_prompt_loads_from_file(self) -> None:
        """Zam 的 vision_prompt.md 能被 loader 读取（黑白红视觉指引）。"""
        from clipwright.persona.loader import load_persona_by_id, resolve_inheritance
        m = resolve_inheritance(load_persona_by_id("Zam"))
        vp = getattr(m, "vision_prompt", "") or ""
        assert vp, "Zam vision_prompt 不应为空"
        assert "纯黑" in vp or "#000000" in vp
        assert "#FF0000" in vp or "红色" in vp


class TestStyleInterpreterVisionPrompt:
    @pytest.fixture(autouse=True)
    def _no_plugin(self, monkeypatch):
        """确保走 LLM 分支：插件解释器存在时 interpret 会走插件而跳过 LLM。"""
        from clipwright.services.style_interpreter import StyleInterpreter
        monkeypatch.setattr(StyleInterpreter, "_plugin", None)

    @pytest.mark.asyncio
    async def test_llm_interpret_injects_vision_prompt(self, monkeypatch) -> None:
        """StyleInterpreter LLM prompt 注入 vision_prompt（文字动画/字幕样式消费点）。"""
        from clipwright.services.llm import LLMService
        from clipwright.services.style_interpreter import StyleInterpreter

        captured: dict = {}

        async def fake_ask(self, prompt, **kwargs):
            captured["prompt"] = prompt
            content = '{"primary_color":"#000000","secondary_color":"#FFFFFF",' \
                      '"accent_color":"#FF0000","text_color":"#FFFFFF","font_size":28,' \
                      '"title_font_size":36,"stagger_delay":0.25,"font":"sans-serif","reason":"t"}'
            return type("R", (), {"success": True, "content": content})()

        monkeypatch.setattr(LLMService, "ask", fake_ask)
        # 触发 LLM 分支：palette 存在
        result = await StyleInterpreter.interpret(
            {"palette": "冷色调+高对比度", "primary_color": "#000000"},
            {"vision_prompt": "纯黑纯白底，红色强调动词"},
        )
        assert "## 创作者视觉需求（vision_prompt，最高优先级）" in captured["prompt"]
        assert "纯黑纯白底" in captured["prompt"]
        assert result["primary_color"] == "#000000"

    @pytest.mark.asyncio
    async def test_no_vision_prompt_no_section(self, monkeypatch) -> None:
        """无 vision_prompt 时 prompt 不含该段。"""
        from clipwright.services.llm import LLMService
        from clipwright.services.style_interpreter import StyleInterpreter

        captured: dict = {}

        async def fake_ask(self, prompt, **kwargs):
            captured["prompt"] = prompt
            content = '{"primary_color":"#000000","font_size":28,"reason":"t"}'
            return type("R", (), {"success": True, "content": content})()

        monkeypatch.setattr(LLMService, "ask", fake_ask)
        await StyleInterpreter.interpret(
            {"palette": "冷色调"}, {"vision_prompt": ""},
        )
        assert "创作者视觉需求" not in captured["prompt"]
