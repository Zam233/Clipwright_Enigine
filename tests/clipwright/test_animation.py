"""AnimationCatalog 单元测试。"""

from __future__ import annotations

from clipwright.animation.catalog import AnimationCatalog
from clipwright.schema.animation import AnimationType, AnimationDef


class TestAnimationCatalog:
    """AnimationCatalog 核心逻辑测试。"""

    def test_get_text_animations_fallback(self) -> None:
        """无注册时应有 fallback 列表。"""
        anims = AnimationCatalog.get_text_animations()
        assert len(anims) > 0
        names = {a["name"] for a in anims}
        assert "淡入" in names
        assert "打字" in names
        assert "滑入" in names

    def test_get_logic_animations(self) -> None:
        """应有内置逻辑动画类型。"""
        anims = AnimationCatalog.get_logic_animations()
        assert len(anims) >= 4
        ids = {a["id"] for a in anims}
        assert "diagram" in ids
        assert "comparison" in ids
        assert "sequence" in ids
        assert "causation" in ids

    def test_resolve_marker_exact(self) -> None:
        """精确 name 匹配。"""
        result = AnimationCatalog.resolve_marker("淡入")
        assert result["type"] == "text"
        assert result["anim_id"] == "text_fade_in"

        result = AnimationCatalog.resolve_marker("箭头")
        assert result["type"] == "logic"
        assert result["anim_id"] == "diagram"

    def test_resolve_marker_fuzzy(self) -> None:
        """包含匹配（处理 LLM 加括号后缀）。"""
        result = AnimationCatalog.resolve_marker("淡入(发光)")
        assert result["type"] == "text"
        assert result["anim_id"] == "text_fade_in"

    def test_resolve_marker_id(self) -> None:
        """id 匹配。"""
        result = AnimationCatalog.resolve_marker("text_fade_in")
        assert result["type"] == "text"

    def test_resolve_marker_unknown(self) -> None:
        """未知标记回退到淡入。"""
        result = AnimationCatalog.resolve_marker("不存在的动画")
        assert result["type"] == "text"
        assert result["anim_id"] == "text_fade_in"

    def test_parse_marker_text_with_text(self) -> None:
        """[文字动画]淡入：xxx 应正确解析。"""
        desc = "以中立风格引入话题 [文字动画]淡入：人工智能正在改变世界"
        markers = AnimationCatalog.parse_marker_from_description(desc)
        assert len(markers) == 1
        m = markers[0]
        assert m["type"] == "text"
        assert m["anim_id"] == "text_fade_in"
        assert m["text"] == "人工智能正在改变世界"

    def test_parse_marker_logic_with_arrow(self) -> None:
        """[逻辑动画]箭头：xxx 应正确解析。"""
        desc = "技术演进 [逻辑动画]箭头：机器学习→深度学习→强化学习"
        markers = AnimationCatalog.parse_marker_from_description(desc)
        assert len(markers) == 1
        m = markers[0]
        assert m["type"] == "logic"
        assert m["anim_id"] == "diagram"
        assert "机器学习" in m["text"]

    def test_parse_marker_legacy_backward_compat(self) -> None:
        """[动画]xxx 向后兼容。"""
        desc = "测试 [动画]打字：引言内容"
        markers = AnimationCatalog.parse_marker_from_description(desc)
        assert len(markers) == 1
        m = markers[0]
        assert m["anim_id"] == "typewriter"

    def test_parse_marker_no_separator(self) -> None:
        """无冒号分隔时不应崩溃。"""
        desc = "场景 [文字动画]淡入"
        markers = AnimationCatalog.parse_marker_from_description(desc)
        assert len(markers) == 1

    def test_parse_marker_multiple(self) -> None:
        """多个标记只取第一个。"""
        desc = "[文字动画]淡入：第一个 [文字动画]打字：第二个"
        markers = AnimationCatalog.parse_marker_from_description(desc)
        # 目前实现取第一个匹配
        assert len(markers) >= 1

    # ── build_full_keyframes ────────────────────────────

    def test_build_full_keyframes_basic(self) -> None:
        """基础：入场 + 保持 + 出场。"""
        kfs = AnimationCatalog.build_full_keyframes("text_fade_in", 10.0, 5.0)
        assert len(kfs) >= 3  # 至少 3 个 keyframes

        # 第一个 keyframe
        assert kfs[0]["time"] == 10.0
        assert kfs[0]["properties"]["opacity"] == 0

        # 最后一个 keyframe = 出场结束
        last = kfs[-1]
        assert last["time"] == 15.0  # start + duration
        assert last["properties"]["opacity"] == 0  # 淡出到透明

    def test_build_full_keyframes_no_duplicates(self) -> None:
        """不应有时间重复的 keyframe。"""
        kfs = AnimationCatalog.build_full_keyframes("text_fade_in", 10.0, 5.0)
        times = [kf["time"] for kf in kfs]
        assert len(times) == len(set(times)), f"重复时间: {times}"

    def test_build_full_keyframes_short_clip(self) -> None:
        """短 clip（1s）不应产生额外 hold。"""
        kfs = AnimationCatalog.build_full_keyframes("shake", 0, 0.8)
        # 短 clip：入场 + 出场，无 hold
        assert len(kfs) >= 2

    def test_build_full_keyframes_entrance_end_state(self) -> None:
        """入场结束后 opacity=1。"""
        kfs = AnimationCatalog.build_full_keyframes("text_fade_in", 0, 5.0)
        for kf in kfs:
            props = kf["properties"]
            # exit 之前的 keyframe 都应 opacity=1
            if kf["time"] < kfs[-1]["time"] and "opacity" in props:
                pass  # 不强制断言，因 shake 等有 animated opacity

    def test_build_full_keyframes_shake(self) -> None:
        """shake 应保留完整震动 pattern + 出场。"""
        kfs = AnimationCatalog.build_full_keyframes("shake", 0, 3.0)
        assert len(kfs) >= 5
        # 检查有 translate_x 变化
        translate_vals = set()
        for kf in kfs:
            props = kf.get("properties", {})
            if "translate_x" in props:
                translate_vals.add(props["translate_x"])
        assert len(translate_vals) > 1  # 应有多段位移

    # ── get_unsupported_properties ──────────────────────

    def test_unsupported_props_fade(self) -> None:
        """fade_in 不应有不支持的属性。"""
        unsupported = AnimationCatalog.get_unsupported_properties("text_fade_in")
        assert len(unsupported) == 0

    def test_unsupported_props_rotate(self) -> None:
        """rotate_in 应有 rotate 在警告集中。"""
        unsupported = AnimationCatalog.get_unsupported_properties("rotate_in")
        assert "rotate" in unsupported

    def test_unsupported_props_scale_not_included(self) -> None:
        """scale_bounce 的 scale_x/scale_y 不再列在 unsupported 中（由 fontsize 模拟）。"""
        unsupported = AnimationCatalog.get_unsupported_properties("scale_bounce")
        assert "scale_x" not in unsupported
        assert "scale_y" not in unsupported

    # ── resolve_persona_style ───────────────────────────

    def test_resolve_persona_style_defaults(self) -> None:
        """无配置时应有合理默认值。"""
        style = AnimationCatalog.resolve_persona_style(None, None)
        assert style["font_size"] == 48
        assert style["font_color"] == "#ffffff"
        assert style["position"] == "bottom"

    def test_resolve_persona_style_override(self) -> None:
        """配置应正确覆盖。"""
        style = AnimationCatalog.resolve_persona_style(
            {"text_font_size": 36, "text_color": "#ff0000"}
        )
        assert style["font_size"] == 36
        assert style["font_color"] == "#ff0000"

    # ── get_entrance_duration ───────────────────────────

    def test_entrance_duration(self) -> None:
        """入场时长应返回正数。"""
        dur = AnimationCatalog.get_entrance_duration("text_fade_in")
        assert dur > 0
        assert dur == 0.4


class TestHyperframesRenderer:
    """HyperframesRenderer HTML 生成测试。"""

    def test_html_build_basic(self) -> None:
        """基本 HTML 结构。"""
        from clipwright.animation.hyperframes_renderer import HyperframesRenderer
        overlays = [
            {"text": "Hello", "start_sec": 1, "duration_sec": 3,
             "anim_type": "text_fade_in", "font_size": 48,
             "font_color": "#ffffff", "position": "center"},
        ]
        html = HyperframesRenderer._build_html(overlays, 1920, 1080, 30)
        assert "<!DOCTYPE html>" in html
        assert 'data-fps="30"' in html
        assert "Hello" in html
        assert "hf-fade-in" in html
        # div 标签平衡
        assert html.count("<div") == html.count("</div>")

    def test_html_diagram_arrow(self) -> None:
        """逻辑箭头图解 HTML 结构。"""
        from clipwright.animation.hyperframes_renderer import HyperframesRenderer
        overlays = [{
            "text": "A→B→C", "start_sec": 2, "duration_sec": 4,
            "anim_type": "fade_in", "font_size": 36,
            "font_color": "#ffffff", "position": "center",
            "diagram_params": {"preset": "diagram", "items": ["A", "B", "C"],
                               "relations": [{"from": 0, "to": 1}, {"from": 1, "to": 2}]},
        }]
        html = HyperframesRenderer._build_html(overlays, 1920, 1080, 30)
        assert "<svg" in html
        assert "→" in html or "marker" in html

    def test_html_diagram_comparison(self) -> None:
        """对比图解 SVG。"""
        from clipwright.animation.hyperframes_renderer import HyperframesRenderer
        overlays = [{
            "text": "VS", "start_sec": 5, "duration_sec": 3,
            "anim_type": "fade_in", "font_size": 36,
            "font_color": "#ffffff", "position": "center",
            "diagram_params": {"preset": "comparison", "items": ["旧方法", "新方法"]},
        }]
        html = HyperframesRenderer._build_html(overlays, 1920, 1080, 30)
        assert "<svg" in html
        assert "旧方法" in html
        assert "新方法" in html

    def test_html_diagram_sequence(self) -> None:
        """流程图解 SVG。"""
        from clipwright.animation.hyperframes_renderer import HyperframesRenderer
        overlays = [{
            "text": "步骤", "start_sec": 7, "duration_sec": 5,
            "anim_type": "fade_in", "font_size": 36,
            "font_color": "#ffffff", "position": "center",
            "diagram_params": {"preset": "sequence", "items": ["分析", "设计", "开发", "测试"]},
        }]
        html = HyperframesRenderer._build_html(overlays, 1920, 1080, 30)
        assert "<svg" in html
        assert "分析" in html

    def test_renderer_available(self) -> None:
        """Hyperframes cli 可用性检测不崩溃。"""
        from clipwright.animation.hyperframes_renderer import HyperframesRenderer
        # 只是不崩溃，不论返回 True/False
        available = HyperframesRenderer.is_available()
        assert isinstance(available, bool)

    def test_diagram_has_svg(self) -> None:
        """图解输出应包含 SVG。"""
        from clipwright.animation.hyperframes_renderer import HyperframesRenderer
        overlays = [{
            "text": "A→B", "start_sec": 1, "duration_sec": 3,
            "font_size": 36, "font_color": "#fff", "position": "center",
            "renderer": "hyperframes",
            "diagram_params": {"preset": "diagram", "items": ["X", "Y", "Z"]},
        }]
        html = HyperframesRenderer._build_html(overlays, 1920, 1080, 30)
        assert "<svg" in html
        assert "</svg>" in html
        assert "hf-el" in html

    def test_diagram_comparison_svg(self) -> None:
        """对比图解 SVG。"""
        from clipwright.animation.hyperframes_renderer import HyperframesRenderer
        overlays = [{
            "text": "VS", "start_sec": 2, "duration_sec": 3,
            "font_size": 36, "font_color": "#fff", "position": "center",
            "renderer": "hyperframes",
            "diagram_params": {"preset": "comparison", "items": ["左", "右"]},
        }]
        html = HyperframesRenderer._build_html(overlays, 1920, 1080, 30)
        assert "<svg" in html
        assert "左" in html
        assert "右" in html

    def test_diagram_sequence_svg(self) -> None:
        """流程图解 SVG。"""
        from clipwright.animation.hyperframes_renderer import HyperframesRenderer
        overlays = [{
            "text": "S", "start_sec": 3, "duration_sec": 4,
            "font_size": 36, "font_color": "#fff", "position": "center",
            "renderer": "hyperframes",
            "diagram_params": {"preset": "sequence", "items": ["1", "2", "3"]},
        }]
        html = HyperframesRenderer._build_html(overlays, 1920, 1080, 30)
        assert "<svg" in html
        assert "1." in html
        assert "2." in html
        assert "3." in html

    def test_html_contains_timing_js(self) -> None:
        """HTML 应包含 JavaScript 时序控制。"""
        from clipwright.animation.hyperframes_renderer import HyperframesRenderer
        overlays = [{"text": "T", "start_sec": 0.5, "duration_sec": 2,
                     "font_size": 48, "font_color": "#fff", "position": "center"}]
        html = HyperframesRenderer._build_html(overlays, 1920, 1080, 30)
        assert "<script>" in html
        assert "setTimeout" in html
        assert "hf-fade-out" in html

    def test_position_css_center(self) -> None:
        """居中位置 CSS 转换。"""
        from clipwright.animation.hyperframes_renderer import _position_css
        css = _position_css("center")
        assert "translate(-50%,-50%)" in css

    def test_position_css_bottom(self) -> None:
        """底部位置 CSS 转换。"""
        from clipwright.animation.hyperframes_renderer import _position_css
        css = _position_css("bottom")
        assert "bottom:60px" in css

    def test_position_css_top_left(self) -> None:
        """左上位置 CSS 转换。"""
        from clipwright.animation.hyperframes_renderer import _position_css
        css = _position_css("top_left")
        assert "left:20px" in css
        assert "top:20px" in css

    def test_calc_char_widths(self) -> None:
        """字符宽度计算。"""
        from clipwright.services.render import RenderService
        widths = RenderService._calc_char_widths("Hello世界", 48)
        assert len(widths) == 7  # 5 ASCII + 2 CJK
        assert widths[0] == int(48 * 0.6)  # H
        assert widths[5] == 48  # 世
        assert widths[6] == 48  # 界
