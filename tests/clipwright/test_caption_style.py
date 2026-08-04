"""任务 30/31 — 字幕样式渲染测试（TextStyle 扩展 + render.py 渲染）。

- T30: clipwright/tool/design.py TextStyle 扩展（stroke/shadow/glow/bold/letter_spacing）
- T31: clipwright/services/render.py 两条 drawtext 路径（静态 + 关键帧）支持全部新样式
"""

from __future__ import annotations

import pytest

from clipwright.schema.timeline import Clip, ClipKind, Track
from clipwright.services.render import RenderService
from clipwright.tool.design import TextStyle


def color_to_drawtext(color: str) -> str:
    """延迟导入 — 实现落地前集合阶段也能加载测试。"""
    from clipwright.tool.design import color_to_drawtext as _impl
    return _impl(color)

# ── T30: TextStyle ────────────────────────────────────────────

STYLE_ALL = {
    "font_size": 60,
    "font_color": "#ffffff",
    "stroke_width": 3.0,
    "stroke_color": "#FF0000",
    "position": "bottom",
    "offset_y": 10,
    "shadow_x": 4.0,
    "shadow_y": -4.0,
    "shadow_color": "#000000",
    "shadow_blur": 6.0,
    "font_weight": "bold",
    "font_italic": True,
    "letter_spacing": 2.5,
    "glow_color": "#FFFF00",
    "glow_width": 8.0,
}


class TestTextStyleExtension:
    def test_from_dict_new_fields(self) -> None:
        ts = TextStyle.from_dict(STYLE_ALL)
        assert ts.font_size == 60
        assert ts.stroke_width == 3.0
        assert ts.stroke_color == "#FF0000"
        assert ts.shadow_x == 4.0
        assert ts.shadow_y == -4.0
        assert ts.shadow_color == "#000000"
        assert ts.shadow_blur == 6.0
        assert ts.font_weight == "bold"
        assert ts.font_italic is True
        assert ts.letter_spacing == 2.5
        assert ts.glow_color == "#FFFF00"
        assert ts.glow_width == 8.0

    def test_from_dict_defaults(self) -> None:
        ts = TextStyle.from_dict(None)
        assert ts.font_weight == "normal"
        assert ts.font_italic is False
        assert ts.letter_spacing == 0
        assert ts.shadow_blur == 0
        assert ts.glow_color == ""
        assert ts.glow_width == 0
        assert ts.stroke_width == 0

    def test_build_stroke_and_shadow(self) -> None:
        ts = TextStyle.from_dict({
            "font_color": "#ffffff", "stroke_width": 3.0, "stroke_color": "#FF0000",
            "shadow_x": 4.0, "shadow_y": -4.0, "shadow_color": "#000000",
        })
        f = ts.build_drawtext_filter("Hi", 1.0, 3.0, font_file="_fonts/msyh.ttc")
        assert "bordercolor=0xFF0000" in f
        assert "borderw=3" in f
        assert "shadowx=4" in f
        assert "shadowy=-4" in f
        assert "shadowcolor=0x000000" in f

    def test_build_no_stroke_shadow(self) -> None:
        ts = TextStyle()
        f = ts.build_drawtext_filter("Hi", 1.0, 3.0)
        assert "borderw=" not in f
        assert "bordercolor=" not in f
        assert "shadowx=" not in f
        assert "shadowy=" not in f
        assert "shadowcolor=" not in f

    def test_color_conversion(self) -> None:
        assert color_to_drawtext("#ffffff") == "0xffffff"
        assert color_to_drawtext("#FF0000") == "0xFF0000"
        assert color_to_drawtext("#00000080") == "0x000000@0.502"
        assert color_to_drawtext("") == ""
        assert color_to_drawtext("0xffffff") == "0xffffff"

    def test_fontcolor_converted(self) -> None:
        ts = TextStyle.from_dict({"font_color": "#ffffff"})
        f = ts.build_drawtext_filter("Hi", 1.0, 3.0)
        assert "fontcolor=0xffffff" in f
        assert "fontcolor=#ffffff" not in f

    def test_letter_spacing_stored_but_not_emitted(self) -> None:
        """drawtext 无 letter_spacing 参数：字段保留，但滤镜串不输出（前端生效）。"""
        ts = TextStyle.from_dict({"letter_spacing": 2.5, "font_color": "#ffffff"})
        assert ts.letter_spacing == 2.5
        f = ts.build_drawtext_filter("Hi", 1.0, 3.0)
        assert "spacing" not in f

    def test_glow_not_emitted_by_textstyle(self) -> None:
        """glow 双通道由 render.py 实现；TextStyle.build_drawtext_filter 仅输出主文本参数。"""
        ts = TextStyle.from_dict({"glow_color": "#FFFF00", "glow_width": 8.0, "font_color": "#ffffff"})
        f = ts.build_drawtext_filter("Hi", 1.0, 3.0)
        assert f.count("drawtext=") == 1
        assert "borderw=" not in f  # glow 的描边参数不在主文本通道输出


# ── T31: render.py ────────────────────────────────────────────

def _caption_clip(**kw) -> Clip:
    base = dict(id="c1", kind=ClipKind.CAPTION, asset_id="a1", track_id="v1",
                start_sec=1.0, duration_sec=3.0, text="Hello")
    base.update(kw)
    return Clip(**base)


def _kf() -> list[dict]:
    return [
        {"time": 0.0, "properties": {"opacity": 1.0, "translate_x": 0, "translate_y": 0, "scale_x": 1.0}},
        {"time": 2.0, "properties": {"opacity": 1.0, "translate_x": 0, "translate_y": 0, "scale_x": 1.0}},
    ]


class TestExtractTextOverlayMerge:
    def test_merges_new_clip_fields_into_style(self) -> None:
        clip = _caption_clip(font_weight="bold", font_italic=True, letter_spacing=2.5,
                             stroke_width=3.0, stroke_color="#FF0000",
                             shadow_x=4.0, shadow_y=-4.0, shadow_color="#000000",
                             shadow_blur=6.0, glow_color="#FFFF00", glow_width=8.0)
        ov = RenderService._extract_text_overlay(clip, 1, [])
        style = ov["style"]
        assert style["font_weight"] == "bold"
        assert style["font_italic"] is True
        assert style["letter_spacing"] == 2.5
        assert style["stroke_width"] == 3.0
        assert style["stroke_color"] == "#FF0000"
        assert style["shadow_x"] == 4.0
        assert style["shadow_y"] == -4.0
        assert style["shadow_color"] == "#000000"
        assert style["shadow_blur"] == 6.0
        assert style["glow_color"] == "#FFFF00"
        assert style["glow_width"] == 8.0

    def test_metadata_style_fallback_when_clip_fields_absent(self) -> None:
        clip = _caption_clip(metadata={"style": {"stroke_width": 2.0, "stroke_color": "#00FF00"}})
        ov = RenderService._extract_text_overlay(clip, 1, [])
        style = ov["style"]
        assert style["stroke_width"] == 2.0
        assert style["stroke_color"] == "#00FF00"
        assert "glow_color" not in style

    def test_clip_fields_override_metadata_style(self) -> None:
        clip = _caption_clip(stroke_width=5.0,
                             metadata={"style": {"stroke_width": 2.0, "stroke_color": "#00FF00"}})
        ov = RenderService._extract_text_overlay(clip, 1, [])
        style = ov["style"]
        assert style["stroke_width"] == 5.0  # clip 字段优先
        assert style["stroke_color"] == "#00FF00"  # 缺失新字段回退 meta.style

    def test_legacy_caption_no_new_fields(self) -> None:
        clip = _caption_clip()  # 无 metadata.style、无新字段
        ov = RenderService._extract_text_overlay(clip, 1, [])
        style = ov["style"]
        for k in ("font_weight", "glow_color", "glow_width", "stroke_width",
                  "shadow_x", "shadow_y", "shadow_color", "shadow_blur",
                  "letter_spacing", "font_italic", "stroke_color"):
            assert k not in style


class TestStaticDrawtextFilter:
    def _ov(self, style: dict, **kw) -> dict:
        ov = dict(text="Hello", start_sec=1.0, duration_sec=3.0, font_size=48,
                  font_color="#ffffff", font="", position="bottom", offset_y=0,
                  style=style, anim_type="", renderer="drawtext", keyframes=[])
        ov.update(kw)
        return ov

    def test_glow_dual_channel(self) -> None:
        ov = self._ov({"font_color": "#ffffff", "glow_color": "#FFFF00", "glow_width": 8.0,
                       "position": "bottom", "offset_y": 0})
        f = RenderService()._build_drawtext_filter(ov)
        assert f.count("drawtext=") == 2
        i2 = f.find("drawtext=", f.find("drawtext=") + 1)
        underlay, main = f[:i2], f[i2:]
        assert "bordercolor=0xFFFF00" in underlay
        assert "borderw=8" in underlay
        assert "fontcolor=0xFFFF00@0.6" in underlay
        assert "enable='between(t,1.0,4.0)'" in underlay
        assert "fontcolor=0xffffff" in main
        assert "borderw=" not in main  # glow 不在主通道输出描边
        assert "enable='between(t,1.0,4.0)'" in main

    def test_glow_underlay_before_main(self) -> None:
        ov = self._ov({"font_color": "#ffffff", "glow_color": "#FFFF00", "glow_width": 8.0})
        f = RenderService()._build_drawtext_filter(ov)
        assert f.index("bordercolor=0xFFFF00") < f.index("fontcolor=0xffffff")

    def test_glow_width_capped_at_20(self) -> None:
        ov = self._ov({"font_color": "#ffffff", "glow_color": "#FFFF00", "glow_width": 100.0})
        f = RenderService()._build_drawtext_filter(ov)
        assert "borderw=20" in f
        assert "borderw=100" not in f

    def test_stroke_and_shadow(self) -> None:
        ov = self._ov({"font_color": "#ffffff", "stroke_width": 3.0, "stroke_color": "#FF0000",
                       "shadow_x": 4.0, "shadow_y": -4.0, "shadow_color": "#000000"})
        f = RenderService()._build_drawtext_filter(ov)
        assert "borderw=3" in f
        assert "bordercolor=0xFF0000" in f
        assert "shadowx=4" in f
        assert "shadowy=-4" in f
        assert "shadowcolor=0x000000" in f

    def test_legacy_caption_no_new_params(self) -> None:
        """无新字段：单条 drawtext，不含 glow/shadow/stroke 参数（回归防护）。"""
        ov = self._ov({"font_color": "#ffffff"})
        f = RenderService()._build_drawtext_filter(ov)
        assert f.count("drawtext=") == 1
        assert "borderw=" not in f
        assert "bordercolor=" not in f
        assert "shadowx=" not in f
        assert "shadowcolor=" not in f

    def test_legacy_caption_pinned_output(self) -> None:
        """基线锁定：无新字段 caption 的滤镜串结构不变（颜色已按要求 #→0x）。"""
        ov = self._ov({"font_color": "#ffffff"})
        f = RenderService()._build_drawtext_filter(ov)
        expected = ("drawtext=text='Hello':fontfile=_fonts/msyh.ttc:fontsize=48:"
                    "fontcolor=0xffffff:x=(w-text_w)/2:y=h-text_h-20-0:"
                    "enable='between(t,1.0,4.0)'")
        assert f == expected

    def test_bold_fontfile(self) -> None:
        ov = self._ov({"font_color": "#ffffff", "font_weight": "bold"})
        f = RenderService()._build_drawtext_filter(ov)
        assert "fontfile=_fonts/msyhbd.ttc" in f


class TestKeyframedDrawtextFilter:
    def test_glow_dual_channel(self) -> None:
        ts = TextStyle.from_dict({"font_color": "#ffffff", "glow_color": "#00FFFF",
                                  "glow_width": 6.0, "position": "bottom", "offset_y": 0})
        f = RenderService._build_kf_drawtext("Hello", ts, 1.0, 3.0, _kf(),
                                             font_arg=":fontfile=_fonts/msyh.ttc")
        assert f.count("drawtext=") == 2
        i2 = f.find("drawtext=", f.find("drawtext=") + 1)
        underlay, main = f[:i2], f[i2:]
        assert "bordercolor=0x00FFFF" in underlay
        assert "borderw=6" in underlay
        assert "fontcolor=0x00FFFF@0.6" in underlay
        assert "fontcolor=0xffffff" in main
        assert "enable='between(t,0.0,4.0)'" in underlay
        assert "enable='between(t,0.0,4.0)'" in main

    def test_shadow(self) -> None:
        ts = TextStyle.from_dict({"font_color": "#ffffff", "shadow_x": 4.0, "shadow_y": -4.0,
                                  "shadow_color": "#000000", "position": "bottom"})
        f = RenderService._build_kf_drawtext("Hello", ts, 1.0, 3.0, _kf(),
                                             font_arg=":fontfile=_fonts/msyh.ttc")
        assert "shadowx=4" in f
        assert "shadowy=-4" in f
        assert "shadowcolor=0x000000" in f

    def test_stroke(self) -> None:
        ts = TextStyle.from_dict({"font_color": "#ffffff", "stroke_width": 2.0,
                                  "stroke_color": "#00FF00", "position": "bottom"})
        f = RenderService._build_kf_drawtext("Hello", ts, 1.0, 3.0, _kf(),
                                             font_arg=":fontfile=_fonts/msyh.ttc")
        assert "borderw=2" in f
        assert "bordercolor=0x00FF00" in f

    def test_legacy_no_new_params(self) -> None:
        ts = TextStyle.from_dict({"font_color": "#ffffff", "position": "bottom"})
        f = RenderService._build_kf_drawtext("Hello", ts, 1.0, 3.0, _kf(),
                                             font_arg=":fontfile=_fonts/msyh.ttc")
        assert f.count("drawtext=") == 1
        assert "borderw=" not in f
        assert "shadowx=" not in f


class TestBoldFontResolution:
    def _resolve(self, family: str) -> str:
        from clipwright.services.render import _resolve_bold_font
        return _resolve_bold_font(family)

    def test_known_families(self) -> None:
        assert self._resolve("msyh") == "_fonts/msyhbd.ttc"
        assert self._resolve("微软雅黑") == "_fonts/msyhbd.ttc"
        assert self._resolve("SimSun") == "_fonts/simsunb.ttf"
        assert self._resolve("SimHei") == "_fonts/simhei.ttf"

    def test_unknown_family_falls_back(self) -> None:
        f = self._resolve("no-such-family-xyz")
        assert f == "" or f.startswith("_fonts/")

    def test_empty_family_defaults_to_msyh(self) -> None:
        assert self._resolve("") == "_fonts/msyhbd.ttc"
