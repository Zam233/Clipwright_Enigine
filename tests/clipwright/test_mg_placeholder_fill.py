"""T11(C7a) — MG 占位符双保护测试。

根因：LLM 生成的 mg_def 有时引用 {left}/{right}/{vs}/{accent} 等占位符但从不被填充，
导致字面 {…} 文本渲染进成片。双保护：

1. generator._build_llm_params union 扫描 — 有 params 键时仍扫描内容占位符并取并集
2. mg_renderer.fill 全面填充 — content + keyframe 属性值 + 静态透传 + shape 属性
3. 固定模板路径 _handle_mg_animation 按模板实际 params 定义对齐填充
4. 渲染后残留扫描 → 二次填充 / 降级模板渲染兜底（_render_html_no_residuals）
"""

from __future__ import annotations

import re

from clipwright.agents.animation_agent import AnimationAgent
from clipwright.animation.mg.generator import MGGenerator
from clipwright.animation.mg_renderer import MGRenderer
from clipwright.schema.timeline import Clip, ClipKind, Track

_RESIDUAL = re.compile(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}")


def _assert_no_placeholder(text: str) -> None:
    assert _RESIDUAL.search(text) is None, f"仍含字面占位符: {_RESIDUAL.findall(text)}"


def _def_with_params_and_unknown() -> dict:
    """params 声明 {text}，但内容还引用了未声明的 {unknown}（旧实现会残留）。"""
    return {
        "animation_id": "mg_union_test",
        "name": "union 扫描",
        "duration_sec": 3.0,
        "width": 1920,
        "height": 1080,
        "style": {"background": "transparent", "font_family": "sans-serif"},
        "params": {"text": {"type": "string", "default": "标题"}},
        "elements": [
            {
                "type": "text",
                "content": "Hello {text} | {unknown}",
                "x": "center", "y": "center",
                "font_size": 48,
                "font_color": "#ffffff",
                "keyframes": [
                    {"time": 0, "opacity": 0},
                    {"time": 1.0, "opacity": 1},
                ],
            },
        ],
    }


class TestBuildLlmParamsUnion:
    """双保护(a): generator 侧 union 扫描。"""

    def test_union_scan_fills_content_placeholder_beyond_params(self) -> None:
        """有 params 键 {text}，但内容里 {unknown} 仍被补进 union 并填充。"""
        g = MGGenerator()
        mg_def = _def_with_params_and_unknown()
        params = g._build_llm_params(mg_def, "世界")
        assert params["text"] == "世界"
        assert "unknown" in params
        assert params["unknown"] == ""
        html = MGRenderer.render(mg_def, params)
        _assert_no_placeholder(html)
        assert "Hello 世界 | " in html

    def test_llm_def_render_has_zero_residuals(self) -> None:
        """{unknown} + {text} 的 mg_def 渲染后 HTML 中 {word} 匹配数为 0。"""
        g = MGGenerator()
        mg_def = _def_with_params_and_unknown()
        params = g._build_llm_params(mg_def, "苹果|安卓")
        html = MGRenderer.render(mg_def, params)
        assert len(_RESIDUAL.findall(html)) == 0

    def test_accent_override_still_applied(self) -> None:
        """Persona 主色仍覆盖默认 accent。"""
        g = MGGenerator()
        d = _def_with_params_and_unknown()
        d["elements"][0]["font_color"] = "{accent}"
        d["params"]["accent"] = {"type": "string", "default": "#4f8cff"}
        params = g._build_llm_params(d, "世界", {"primary_color": "#ff0000"})
        assert params["accent"] == "#ff0000"
        html = MGRenderer.render(d, params)
        assert "color:#ff0000" in html
        _assert_no_placeholder(html)


class TestRendererComprehensiveFill:
    """双保护(b): mg_renderer fill 全面覆盖。"""

    def test_keyframe_attribute_value_filled(self) -> None:
        """keyframe 属性值 box_shadow: "0 0 12px {accent}" 被填充。"""
        d = {
            "animation_id": "mg_kf_fill",
            "name": "关键帧填充",
            "duration_sec": 3.0,
            "width": 1920,
            "height": 1080,
            "style": {"background": "transparent", "font_family": "sans-serif"},
            "elements": [
                {
                    "type": "shape",
                    "shape": "rect",
                    "x": "center", "y": "center",
                    "width": 300, "height": 40,
                    "keyframes": [
                        {"time": 0, "opacity": 0, "box_shadow": "0 0 12px {accent}"},
                        {"time": 1.0, "opacity": 1, "box_shadow": "0 0 24px {accent}"},
                    ],
                },
            ],
        }
        html = MGRenderer.render(d, {"accent": "#f59e0b"})
        assert "box-shadow:0 0 12px #f59e0b;" in html
        assert "box-shadow:0 0 24px #f59e0b;" in html
        _assert_no_placeholder(html)

    def test_keyframe_transform_value_filled(self) -> None:
        """keyframe transform 属性值占位符被填充。"""
        d = {
            "animation_id": "mg_kf_transform",
            "name": "关键帧位移",
            "duration_sec": 3.0,
            "width": 1920,
            "height": 1080,
            "style": {"background": "transparent", "font_family": "sans-serif"},
            "elements": [
                {
                    "type": "text",
                    "content": "{text}",
                    "x": "center", "y": "center",
                    "font_size": 48,
                    "font_color": "#ffffff",
                    "keyframes": [
                        {"time": 0, "translate_y": "{off}", "scale": "{s}", "opacity": 0},
                        {"time": 1.0, "translate_y": "0", "scale": 1.0, "opacity": 1},
                    ],
                },
            ],
        }
        html = MGRenderer.render(d, {"text": "标题", "off": "-20", "s": "0.5"})
        assert "translateY(-20px)" in html
        assert "scale(0.5)" in html
        _assert_no_placeholder(html)

    def test_static_passthrough_filled(self) -> None:
        """静态透传属性 text_shadow/box_shadow 的占位符被填充。"""
        d = {
            "animation_id": "mg_static_fill",
            "name": "静态透传",
            "duration_sec": 3.0,
            "width": 1920,
            "height": 1080,
            "style": {"background": "transparent", "font_family": "sans-serif"},
            "elements": [
                {
                    "type": "text",
                    "content": "{text}",
                    "x": "center", "y": "center",
                    "font_size": 48,
                    "font_color": "#ffffff",
                    "text_shadow": "0 0 20px {accent}",
                    "box_shadow": "0 0 30px {accent}",
                    "keyframes": [{"time": 0, "opacity": 1}],
                },
            ],
        }
        html = MGRenderer.render(d, {"text": "标题", "accent": "#f59e0b"})
        assert "text-shadow:0 0 20px #f59e0b;" in html
        assert "box-shadow:0 0 30px #f59e0b;" in html
        _assert_no_placeholder(html)

    def test_shape_props_filled(self) -> None:
        """shape 属性（width/color/border）占位符被填充。"""
        d = {
            "animation_id": "mg_shape_fill",
            "name": "形状填充",
            "duration_sec": 3.0,
            "width": 1920,
            "height": 1080,
            "style": {"background": "transparent", "font_family": "sans-serif"},
            "elements": [
                {
                    "type": "line",
                    "width": "{w}",
                    "height": 4,
                    "x": "center", "y": "center",
                    "color": "{accent}",
                    "stroke_color": "{border}",
                    "stroke_width": 3,
                    "keyframes": [{"time": 0, "opacity": 1}],
                },
            ],
        }
        html = MGRenderer.render(d, {"w": "400", "accent": "#4f8cff", "border": "#fbbf24"})
        assert "width:400px" in html
        assert "background:#4f8cff" in html
        assert "border:3px solid #fbbf24" in html
        _assert_no_placeholder(html)


class TestFixedTemplateParamAlignment:
    """双保护(d): 固定模板路径按模板实际 params 定义对齐填充。"""

    def _track(self) -> Track:
        return Track(id="t_mg", name="动画轨", kind=ClipKind.ANIMATION, index=0)

    def _video_clip(self) -> Clip:
        return Clip(
            id="v1", kind=ClipKind.VIDEO, asset_id="a1", track_id="t_mg",
            start_sec=1.0, duration_sec=5.0,
        )

    async def test_comparison_split_no_literal_placeholders(self) -> None:
        """mg_comparison_split（params=left/right/left_sub/right_sub/vs/accent）
        不再硬编码 text/value/unit/subtitle — 产物 HTML 无 {left}/{right}/{vs} 残留。"""
        agent = AnimationAgent()
        track = self._track()
        vid = self._video_clip()
        await agent._handle_mg_animation(
            track, vid, "mg_comparison_split", "左右对比",
            "骁龙8Gen3|天玑9300", 4.0, {},
        )
        assert len(track.clips) == 1
        c = track.clips[0]
        assert c.kind == ClipKind.ANIMATION
        html = (c.metadata or {}).get("mg_html", "")
        assert html
        assert "{left}" not in html
        assert "{right}" not in html
        assert "{vs}" not in html
        _assert_no_placeholder(html)
        # 按位置对齐：left=第 1 段，right=第 2 段
        assert "骁龙8Gen3" in html
        assert "天玑9300" in html

    async def test_comparison_split_persona_accent(self) -> None:
        """persona 主色覆盖固定模板 accent 参数。"""
        agent = AnimationAgent()
        track = self._track()
        vid = self._video_clip()
        await agent._handle_mg_animation(
            track, vid, "mg_comparison_split", "左右对比",
            "A|B", 4.0, {},
            persona_style={"primary_color": "#e11d48"},
        )
        assert len(track.clips) == 1
        c = track.clips[0]
        mg_params = (c.metadata or {}).get("mg_params") or {}
        assert mg_params.get("accent") == "#e11d48"
        _assert_no_placeholder((c.metadata or {}).get("mg_html", ""))

    async def test_title_reveal_uses_declared_text_param(self) -> None:
        """mg_title_reveal 声明 text+accent — 仍正确填充 {text}。"""
        agent = AnimationAgent()
        track = self._track()
        vid = self._video_clip()
        await agent._handle_mg_animation(
            track, vid, "mg_title_reveal", "标题揭示",
            "年度总结", 4.0, {},
        )
        assert len(track.clips) == 1
        c = track.clips[0]
        html = (c.metadata or {}).get("mg_html", "")
        assert html
        assert "年度总结" in html
        _assert_no_placeholder(html)


class TestResidualSweepFallback:
    """双保护(c): 渲染后残留扫描 → 降级模板渲染兜底。"""

    def test_residual_after_fill_falls_back_to_clean_template(self) -> None:
        """mg_def 中参数默认值自带 {unknown}（二次填充无法清除）→ 降级模板输出无占位符。"""
        import asyncio

        g = MGGenerator()
        mg_def = {
            "animation_id": "mg_residual_test",
            "name": "残留测试",
            "duration_sec": 2.0,
            "width": 1920,
            "height": 1080,
            "style": {"background": "transparent", "font_family": "sans-serif"},
            "params": {"text": {"type": "string", "default": "价格 {unknown}"}},
            "elements": [
                {
                    "type": "text",
                    "content": "{text}",
                    "x": "center", "y": "center",
                    "font_size": 48,
                    "font_color": "#ffffff",
                    "keyframes": [{"time": 0, "opacity": 1}],
                },
            ],
        }
        html = asyncio.run(g._render_html_no_residuals(
            mg_def, {"text": "价格 {unknown}"},
            description="左右对比动画", text_content="产品A|产品B",
        ))
        assert html.startswith("<!DOCTYPE html>")
        _assert_no_placeholder(html)

    def test_no_fallback_when_clean(self) -> None:
        """无残留时直接返回首渲 HTML，不触发降级。"""
        import asyncio

        g = MGGenerator()
        mg_def = _def_with_params_and_unknown()
        params = g._build_llm_params(mg_def, "世界")
        html = asyncio.run(g._render_html_no_residuals(
            mg_def, params,
            description="任意需求", text_content="世界",
        ))
        assert "Hello 世界 | " in html
        _assert_no_placeholder(html)
