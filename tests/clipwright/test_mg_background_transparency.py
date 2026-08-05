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
