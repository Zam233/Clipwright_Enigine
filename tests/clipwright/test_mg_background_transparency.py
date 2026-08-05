"""T3 MG 背景透明测试。

用户诉求：MG 动画叠加在实拍素材上，默认不得有不透明全幅背景；
仅当 vision_prompt 明确要求背景时才允许 bg 元素保留背景。

覆盖：
- _build_context_section 输出含「禁止背景层」约束
- vision_prompt 为空 → bg 元素 background 被强制 transparent
- vision_prompt 非空（明确要求背景）→ bg 元素保留原背景
- validator 仍接受 bg 元素类型（未从 VALID_ELEMENT_TYPES 移除）
"""

from __future__ import annotations

import copy

from clipwright.animation.mg.generator import MGGenerator
from clipwright.animation.mg.validator import VALID_ELEMENT_TYPES, validate_mg_json


def _mg_def_with_bg() -> dict:
    """最小合法 mg_def，含一个渐变背景的 bg 元素。"""
    return {
        "animation_id": "mg_generated_test_bg",
        "duration_sec": 3.0,
        "width": 1920,
        "height": 1080,
        "elements": [
            {
                "type": "bg",
                "background": "linear-gradient(135deg, #0a0e1a 0%, #16234a 100%)",
                "keyframes": [
                    {"time": 0, "opacity": 0, "easing": "ease-out"},
                    {"time": 0.5, "opacity": 1},
                ],
            },
            {
                "type": "text",
                "content": "标题",
                "x": "center",
                "y": "center",
                "keyframes": [
                    {"time": 0, "opacity": 0, "easing": "back-out"},
                    {"time": 0.5, "opacity": 1},
                ],
            },
        ],
    }


class TestContextSectionForbidsBackground:
    def test_context_section_forbids_background(self) -> None:
        section = MGGenerator._build_context_section({}, {}, "")
        assert "禁止背景" in section
        # 约束必须指向透明背景，而非鼓励生成背景层
        assert "transparent" in section

    def test_context_section_forbids_background_with_palette(self) -> None:
        persona_style = {
            "primary_color": "#000000",
            "secondary_color": "#FFFFFF",
            "accent_color": "#FF0000",
        }
        section = MGGenerator._build_context_section(persona_style, {}, "")
        assert "禁止背景" in section
        # 回归：最终约束块仍以蓝紫禁令收尾（persona 色板测试依赖）
        assert section.rstrip().endswith("禁止默认蓝紫科技渐变、发光粒子、彩虹渐变。")


class TestEnsureNoBackground:
    def test_generate_strips_bg_without_vision(self) -> None:
        mg_def = _mg_def_with_bg()
        result = MGGenerator._ensure_no_background(copy.deepcopy(mg_def), "")
        bg = next(e for e in result["elements"] if e["type"] == "bg")
        assert bg["background"] == "transparent"
        # 非 bg 元素不受影响
        text = next(e for e in result["elements"] if e["type"] == "text")
        assert "background" not in text

    def test_generate_strips_bg_with_blank_vision(self) -> None:
        """空白 vision_prompt（仅空格）视同未要求背景。"""
        mg_def = _mg_def_with_bg()
        result = MGGenerator._ensure_no_background(copy.deepcopy(mg_def), "   ")
        bg = next(e for e in result["elements"] if e["type"] == "bg")
        assert bg["background"] == "transparent"

    def test_generate_keeps_bg_with_vision(self) -> None:
        mg_def = _mg_def_with_bg()
        original_bg = mg_def["elements"][0]["background"]
        result = MGGenerator._ensure_no_background(
            copy.deepcopy(mg_def), "背景用深蓝色"
        )
        bg = next(e for e in result["elements"] if e["type"] == "bg")
        assert bg["background"] == original_bg


class TestValidatorStillAllowsBgType:
    def test_bg_type_still_valid(self) -> None:
        assert "bg" in VALID_ELEMENT_TYPES
        ok, errors = validate_mg_json(_mg_def_with_bg())
        assert ok, f"validate_mg_json introduced new errors for bg element: {errors}"


class TestTemplatePathStripsBackground:
    """模板 MG（_handle_mg_animation 直接渲染模板）也必须剥离背景渐变。

    质检发现：T3 仅覆盖 LLM MG 路径（generator.generate），模板路径漏掉——
    8 个内置模板（mg_comparison_split 等）自带 bg 渐变，渲染后遮挡实拍素材。
    """

    def test_template_bg_stripped_by_handle_mg_animation(self) -> None:
        from clipwright.agents.animation_agent import AnimationAgent
        from clipwright.animation.mg_renderer import MGRenderer

        # 真实模板含 bg 渐变背景
        mg_def = MGRenderer.load_animation("mg_comparison_split")
        assert mg_def is not None
        bg = next((e for e in mg_def.get("elements", []) if e.get("type") == "bg"), None)
        assert bg is not None and bg.get("background") != "transparent", (
            "前置条件：模板应自带渐变背景，否则测试无意义"
        )

        # 走模板路径：_handle_mg_animation 渲染出的 HTML 背景必须透明
        import asyncio
        from clipwright.schema.timeline import Clip, ClipKind, Timeline, Track
        from clipwright.agents.animation_agent import AnimationAgent

        agent = AnimationAgent()
        agent._tl_width = 1920
        agent._tl_height = 1080
        agent._tl_fps = 30.0
        agent._vision_prompt = ""  # 无视觉需求 → 强制透明

        track = Track(
            id="t_anim", name="动画轨", kind=ClipKind.ANIMATION, index=4,
        )
        vid = Clip(
            id="c_v", kind=ClipKind.VIDEO, asset_id="a", track_id="t_v",
            start_sec=0.0, duration_sec=5.0,
        )
        marker = {"text": "左侧|右侧|中央"}

        async def _run():
            await agent._handle_mg_animation(
                track, vid, "mg_comparison_split", "对比", "左侧|右侧|中央",
                5.0, marker, {},
            )
            return track

        track = asyncio.run(_run())
        assert len(track.clips) == 1
        html = (track.clips[0].metadata or {}).get("mg_html", "")
        assert "linear-gradient" not in html.split("bg")[0][-200:]  # bg 区域无渐变
        assert "background:transparent" in html or "background: transparent" in html
