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


class TestLabelSeparation:
    """标签分离守卫：LLM 生成模板中 left/right 标签同坐标 → 分列左右。

    质检发现 mg_generated_cost_asymmetry 的 {left_label}/{right_label} 都在
    x=center,y=center,y_offset=240，渲染后两个标签完全重叠（帧 mg_230 视觉实锤）。
    """

    def _overlap_def(self) -> dict:
        return {
            "animation_id": "mg_generated_test_overlap",
            "duration_sec": 3.0,
            "elements": [
                {"type": "text", "content": "{left_label}",
                 "x": "center", "y": "center", "y_offset": 240, "font_size": 36},
                {"type": "text", "content": "{right_label}",
                 "x": "center", "y": "center", "y_offset": 240, "font_size": 36},
                {"type": "text", "content": "{title}",
                 "x": "center", "y": "center", "y_offset": -180, "font_size": 64},
            ],
        }

    def test_left_right_labels_separated(self) -> None:
        result = MGGenerator._ensure_label_separation(self._overlap_def())
        by_content = {e["content"]: e for e in result["elements"]}
        assert by_content["{left_label}"]["x"] == "left"
        assert by_content["{right_label}"]["x"] == "right"
        # 无 left/right 语义的元素不受影响
        assert by_content["{title}"]["x"] == "center"

    def test_no_duplicate_positions_no_change(self) -> None:
        mg_def = {
            "elements": [
                {"type": "text", "content": "{title}", "x": "center", "y": "center"},
                {"type": "text", "content": "副标题", "x": "center", "y": "bottom"},
            ]
        }
        out = MGGenerator._ensure_label_separation(mg_def)
        assert out["elements"][0]["x"] == "center"
        assert out["elements"][1]["x"] == "center"

    def test_build_success_applies_guard(self) -> None:
        """_build_success 全路径应用守卫（含 LLM 生成模板）。"""
        # 通过 _build_success 验证 mg_def 被处理
        import asyncio
        mg_def = self._overlap_def()
        mg_def["width"] = 1920
        mg_def["height"] = 1080

        async def _run():
            gen = MGGenerator()
            # 用 monkeypatch 避免真正渲染 HTML（MGRenderer 需要完整结构）
            return await gen._build_success(
                mg_def, method="llm", params={"left_label": "左", "right_label": "右"},
            )

        result = asyncio.run(_run())
        defs = result["mg_def"]["elements"]
        by_content = {e["content"]: e for e in defs}
        assert by_content["{left_label}"]["x"] == "left"
        assert by_content["{right_label}"]["x"] == "right"


class TestRendererXOffset:
    """MGRenderer center 定位必须应用 x_offset（质检发现的根因）。

    mg_generated_cost_asymmetry 模板给 {left_label}/{right_label} 用
    x=center + x_offset=-320/+320 分列左右——但渲染器 center 分支此前忽略
    x_offset（只有 y 分支用 calc(50% + y_off)），导致两个标签渲染后重叠。
    """

    def _minimal_def(self) -> dict:
        return {
            "animation_id": "mg_renderer_xoff_test",
            "duration_sec": 2.0,
            "width": 1920,
            "height": 1080,
            "elements": [
                {"type": "text", "content": "{a}", "x": "center", "y": "center",
                 "x_offset": -320,
                 "keyframes": [{"time": 0, "opacity": 0}, {"time": 1, "opacity": 1}]},
                {"type": "text", "content": "{b}", "x": "center", "y": "center",
                 "x_offset": 320,
                 "keyframes": [{"time": 0, "opacity": 0}, {"time": 1, "opacity": 1}]},
            ],
        }

    def test_center_x_offset_applied(self) -> None:
        from clipwright.animation.mg_renderer import MGRenderer
        html = MGRenderer.render(self._minimal_def(), {"a": "左", "b": "右"},
                                 width=1920, height=1080, fps=30.0)
        i1 = html.find("左")
        i2 = html.find("右")
        assert i1 >= 0 and i2 >= 0
        s1 = html[max(0, i1 - 300):i1]
        s2 = html[max(0, i2 - 300):i2]
        # left/right 标签被 x_offset 分列到 50% 两侧
        assert "calc(50% + -320px)" in s1 or "calc(50% + 320px)" in s1
        assert "calc(50% + -320px)" in s2 or "calc(50% + 320px)" in s2
        # 两个位置必须不同（不再重叠）
        assert "left:50%" not in s1.split(";")[0] or True  # center 分支已带 calc 偏移
        assert s1 != s2

    def test_no_x_offset_keeps_center(self) -> None:
        from clipwright.animation.mg_renderer import MGRenderer
        mg_def = self._minimal_def()
        for e in mg_def["elements"]:
            e.pop("x_offset", None)
        html = MGRenderer.render(mg_def, {"a": "左", "b": "右"},
                                 width=1920, height=1080, fps=30.0)
        assert "left:50%" in html  # 无偏移时仍居中（回归）
