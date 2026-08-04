"""MGRenderer 渲染器测试 — 基线行为 + 新增 easing/属性/元素类型。

Baseline regression tests pin the pre-upgrade renderer output so that old-style
MG definitions keep rendering identically after the renderer extension.
"""

from __future__ import annotations

from clipwright.animation.mg_renderer import MGRenderer


def _old_style_def() -> dict:
    """旧风格 MG 定义（easing/新元素类型出现之前的写法）。"""
    return {
        "animation_id": "mg_old_style",
        "name": "旧风格",
        "duration_sec": 4.0,
        "width": 1920,
        "height": 1080,
        "style": {"background": "transparent", "font_family": "sans-serif"},
        "elements": [
            {
                "type": "text",
                "content": "Hello {text}",
                "x": "center",
                "y": "center",
                "font_size": 48,
                "font_color": "#ffffff",
                "keyframes": [
                    {"time": 0, "opacity": 0, "scale": 0.3, "filter": "blur(8px)"},
                    {"time": 0.5, "opacity": 1, "scale": 1.0, "filter": "blur(0px)"},
                    {"time": 3.8, "opacity": 0, "translate_y": -20},
                ],
            },
            {
                "type": "shape",
                "shape": "rect",
                "color": "#4f8cff",
                "width": 200,
                "height": 3,
                "keyframes": [
                    {"time": 0, "opacity": 0},
                    {"time": 1.0, "opacity": 1},
                ],
            },
        ],
        "params": {"text": {"type": "string", "default": "世界"}},
    }


class TestMGRendererBaseline:
    """基线回归：未升级渲染器时旧定义也必须输出原样 HTML。"""

    def test_render_structure(self) -> None:
        """完整 HTML 结构骨架。"""
        html = MGRenderer.render(_old_style_def(), {"text": "世界"})
        assert "<!DOCTYPE html>" in html
        assert 'data-fps="30"' in html
        assert 'data-width="1920"' in html
        assert 'data-height="1080"' in html
        assert 'data-duration="4.00"' in html

    def test_render_param_substitution(self) -> None:
        """{text} 占位符被替换。"""
        html = MGRenderer.render(_old_style_def(), {"text": "世界"})
        assert ">Hello 世界<" in html

    def test_render_keyframes_text(self) -> None:
        """text 元素关键帧块（含 transform 与 props 直出）。"""
        html = MGRenderer.render(_old_style_def())
        assert "@keyframes mg_anim_0{" in html
        assert "0.0%{transform:translateX(-50%) translateY(-50%) scale(0.3);opacity:0;filter:blur(8px);}" in html
        assert "12.5%{transform:translateX(-50%) translateY(-50%) scale(1.0);opacity:1;filter:blur(0px);}" in html
        assert "95.0%{transform:translateX(-50%) translateY(-50%) translateY(-20px);opacity:0;}" in html

    def test_render_element_style_linear_baseline(self) -> None:
        """元素级 animation 简写保留 linear（基线行为）。"""
        html = MGRenderer.render(_old_style_def())
        assert "el.style.animation='mg_anim_0 4.0s linear forwards';" in html

    def test_render_shape_rect(self) -> None:
        """shape rect 元素内联样式。"""
        html = MGRenderer.render(_old_style_def())
        assert "width:200px;height:3px;background:#4f8cff;border-radius:4;" in html

    def test_render_text_inline_style(self) -> None:
        """text 元素内联样式。"""
        html = MGRenderer.render(_old_style_def())
        assert "font-size:48px;color:#ffffff;font-weight:normal" in html

    def test_render_unknown_type_skipped(self) -> None:
        """未知元素类型被跳过。"""
        d = _old_style_def()
        d["elements"].append({"type": "bogus", "keyframes": [{"time": 0, "opacity": 1}]})
        html = MGRenderer.render(d)
        assert "mg-e2" not in html

    def test_static_style_placeholder_filled(self) -> None:
        """静态样式透传字段（box_shadow/text_shadow 等）中的占位符也必须被替换。

        回归：{accent} 出现在 line/color、box_shadow、text_shadow 等非 content
        字段时，旧实现只填 content 导致占位符字面量渲染进成片。
        """
        d = _old_style_def()
        d["elements"].append({
            "type": "line",
            "width": 300,
            "height": 2,
            "x": "center",
            "y": 300,
            "color": "{accent}",
            "box_shadow": "0 0 12px {accent}",
            "keyframes": [
                {"time": 0, "opacity": 0, "width": 0},
                {"time": 1.0, "opacity": 1, "width": 300},
            ],
        })
        d["elements"].append({
            "type": "text",
            "content": "{text}",
            "x": "center", "y": 400,
            "font_color": "{accent}",
            "text_shadow": "0 0 20px {accent}",
            "keyframes": [
                {"time": 0, "opacity": 0},
                {"time": 1.0, "opacity": 1},
            ],
        })
        html = MGRenderer.render(d, {"text": "世界", "accent": "#f59e0b"})
        assert "{accent}" not in html
        assert "box-shadow:0 0 12px #f59e0b;" in html
        assert "text-shadow:0 0 20px #f59e0b;" in html
        assert "color:#f59e0b" in html


def _new_style_def() -> dict:
    """新风格 MG 定义（逐关键帧 easing + glow + 渐变 + 新元素类型）。"""
    return {
        "animation_id": "mg_new_style",
        "name": "新风格",
        "duration_sec": 4.0,
        "width": 1920,
        "height": 1080,
        "style": {"background": "transparent", "font_family": "sans-serif"},
        "elements": [
            {
                "type": "bg",
                "background": "linear-gradient(135deg, #0a0e1a 0%, #16234a 100%)",
                "keyframes": [
                    {"time": 0, "opacity": 0},
                    {"time": 0.5, "opacity": 1, "easing": "ease-out"},
                ],
            },
            {
                "type": "text",
                "content": "{text}",
                "x": "center", "y": "center",
                "font_size": 92,
                "font_weight": "bold",
                "font_color": "#ffffff",
                "text_shadow": "0 0 40px rgba(79,140,255,0.8)",
                "keyframes": [
                    {"time": 0, "opacity": 0, "translate_y": 24},
                    {"time": 1.0, "opacity": 1, "translate_y": 0, "easing": "back-out"},
                    {"time": 3.6, "opacity": 1},
                    {"time": 3.9, "opacity": 0, "easing": "ease-in"},
                ],
            },
            {
                "type": "ring",
                "width": 420, "height": 420,
                "x": "center", "y": "center",
                "border_width": 2,
                "border_color": "rgba(79,140,255,0.5)",
                "box_shadow": "0 0 40px rgba(79,140,255,0.4)",
                "keyframes": [
                    {"time": 0, "opacity": 0, "scale": 0.6},
                    {"time": 0.8, "opacity": 1, "scale": 1.0, "easing": "back-out"},
                ],
            },
            {
                "type": "line",
                "width": 360, "height": 4,
                "x": "center", "y": "center",
                "y_offset": 92,
                "color": "#4f8cff",
                "box_shadow": "0 0 24px rgba(79,140,255,0.9)",
                "keyframes": [
                    {"time": 0, "width": 0, "opacity": 0},
                    {"time": 1.6, "width": 360, "opacity": 1, "easing": "back-out"},
                ],
            },
            {
                "type": "arc",
                "width": 520, "height": 520,
                "x": "center", "y": "center",
                "border_width": 3,
                "border_color": "#fbbf24",
                "keyframes": [
                    {"time": 0, "opacity": 0, "rotate": -90},
                    {"time": 1.2, "opacity": 1, "rotate": 0, "easing": "back-out"},
                ],
            },
            {
                "type": "circle",
                "width": 8, "height": 8,
                "x": "center", "y": "center",
                "color": "#fbbf24",
                "keyframes": [
                    {"time": 0, "opacity": 0, "scale": 0.3},
                    {"time": 2.0, "opacity": 1, "scale": 1.0, "easing": "elastic-out"},
                ],
            },
        ],
        "params": {"text": {"type": "string", "default": "标题"}},
    }


class TestMGRendererNewStyle:
    """新能力：逐关键帧 easing / glow / 渐变 / 新元素类型。"""

    def test_easing_emitted_inside_keyframes_stop(self) -> None:
        """easing 必须出现在 @keyframes 停止块内部。"""
        html = MGRenderer.render(_new_style_def())
        # back-out 映射到 cubic-bezier 且在百分比块内
        assert "@keyframes mg_anim_1{" in html
        assert "25.0%{transform:translateX(-50%) translateY(-50%) translateY(0px);opacity:1;animation-timing-function:cubic-bezier(0.175, 0.885, 0.32, 1.275);}" in html
        assert "97.5%{transform:translateX(-50%) translateY(-50%);opacity:0;animation-timing-function:ease-in;}" in html

    def test_easing_named_curve_map(self) -> None:
        """命名曲线映射到 CSS 曲线。"""
        html = MGRenderer.render(_new_style_def())
        assert "animation-timing-function:ease-out;" in html
        assert "animation-timing-function:ease-in;" in html
        assert "animation-timing-function:cubic-bezier(0.175, 0.885, 0.32, 1.275);" in html

    def test_easing_not_on_element_style_animation(self) -> None:
        """easing 禁止出现在元素级 animation 简写。"""
        html = MGRenderer.render(_new_style_def())
        assert "el.style.animation='mg_anim_1 4.0s linear forwards';" in html
        assert "el.style.animation='mg_anim_1 4.0s ease-out forwards';" not in html

    def test_easing_cubic_bezier_array_verbatim(self) -> None:
        """cubic-bezier 数组透传为 cubic-bezier(x1,y1,x2,y2)。"""
        d = _new_style_def()
        d["elements"][1]["keyframes"][1]["easing"] = [0.68, -0.55, 0.265, 1.55]
        html = MGRenderer.render(d)
        assert "animation-timing-function:cubic-bezier(0.68, -0.55, 0.265, 1.55);" in html

    def test_elastic_bounce_approx(self) -> None:
        """elastic/bounce 映射为近似 cubic-bezier。"""
        html = MGRenderer.render(_new_style_def())
        assert "animation-timing-function:cubic-bezier(0.68, -0.55, 0.265, 1.55);" in html

    def test_new_props_passthrough(self) -> None:
        """text_shadow / box_shadow / letter_spacing / font_weight / transform_origin / line_height / background 透传。"""
        d = _new_style_def()
        d["elements"][1]["text_shadow"] = "0 0 40px rgba(79,140,255,0.8)"
        d["elements"][1]["letter_spacing"] = "4px"
        d["elements"][1]["line_height"] = "1.5"
        d["elements"][1]["transform_origin"] = "center bottom"
        html = MGRenderer.render(d)
        assert "text-shadow:0 0 40px rgba(79,140,255,0.8)" in html
        assert "letter-spacing:4px" in html
        assert "line-height:1.5" in html
        assert "transform-origin:center bottom" in html

    def test_new_props_animatable_in_keyframes(self) -> None:
        """新属性也可在关键帧内动画。"""
        d = _new_style_def()
        d["elements"][1]["keyframes"] = [
            {"time": 0, "opacity": 0, "letter_spacing": "0px"},
            {"time": 1.0, "opacity": 1, "letter_spacing": "6px", "easing": "ease-out"},
        ]
        html = MGRenderer.render(d)
        assert "letter-spacing:0px" in html
        assert "letter-spacing:6px" in html

    def test_bg_element_full_frame(self) -> None:
        """bg 元素全幅渐变底层。"""
        html = MGRenderer.render(_new_style_def())
        assert "position:absolute;inset:0;z-index:0" in html
        assert "linear-gradient(135deg, #0a0e1a 0%, #16234a 100%)" in html

    def test_line_element(self) -> None:
        """line 元素细长 rect。"""
        html = MGRenderer.render(_new_style_def())
        assert "width:360px;height:4px;background:#4f8cff" in html

    def test_circle_element(self) -> None:
        """circle 元素正圆。"""
        html = MGRenderer.render(_new_style_def())
        assert "width:8px;height:8px;background:#fbbf24;border-radius:50%" in html

    def test_ring_element(self) -> None:
        """ring 元素空心圆环。"""
        html = MGRenderer.render(_new_style_def())
        assert "border-radius:50%;" in html
        assert "border:2px solid rgba(79,140,255,0.5);" in html
        assert "box-shadow:0 0 40px rgba(79,140,255,0.4)" in html

    def test_arc_element(self) -> None:
        """arc 元素近似圆弧（标注近似）。"""
        html = MGRenderer.render(_new_style_def())
        assert "border-radius:50% 50% 0 0" in html
        assert "border:3px solid #fbbf24" in html

    def test_old_template_still_renders(self) -> None:
        """旧模板渲染不回归。"""
        html = MGRenderer.render(_old_style_def())
        assert "mg_anim_0" in html
        assert "mg_anim_1" in html
        assert "linear forwards" in html


class TestMGRendererTimelineDims:
    """T1(C2a): 动态分辨率/帧率解析。

    根 [data-composition-id] div 必须携带 data-width/data-height（=拟定分辨率，
    与内联样式一致），供 Hyperframes 正确设定合成尺寸；fps 使用实际时间线帧率。
    """

    def test_caller_dims_win_over_mg_def(self) -> None:
        """传入 1080x1920 → 根 div data-width/data-height 与内联样式一致。"""
        html = MGRenderer.render(_old_style_def(), width=1080, height=1920)
        assert 'data-width="1080"' in html
        assert 'data-height="1920"' in html
        assert 'style="width:1080px;height:1920px;position:relative;overflow:hidden"' in html

    def test_caller_fps_rendered(self) -> None:
        """fps=25 → data-fps="25"（30.0 仍渲染为 "30"）。"""
        html = MGRenderer.render(_old_style_def(), width=1080, height=1920, fps=25)
        assert 'data-fps="25"' in html

    def test_mg_def_without_dims_falls_back(self) -> None:
        """mg_def 无 width/height 且无调用方尺寸 → 回退 1920x1080，不崩溃。"""
        d = _old_style_def()
        d.pop("width", None)
        d.pop("height", None)
        html = MGRenderer.render(d)
        assert 'data-width="1920"' in html
        assert 'data-height="1080"' in html
        assert "width:1920px;height:1080px" in html
