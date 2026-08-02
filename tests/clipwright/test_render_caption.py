"""RenderService 字幕可读性修复 — 单元测试 (Todo 3 C1c)。

覆盖:
- _extract_text_overlay: caption 叠加 offset_y 强制为 0（防止多字幕堆叠推出屏幕）
- caption 无显式 style 时注入默认描边 {"stroke_width": 2, "stroke_color": "#000000"}
- _build_drawtext_filter: 默认描边流入 drawtext filter（borderw=2）
- 非 caption 文字叠加 offset_y 行为保持不变
- caption 走 drawtext/text 路径，不被 hyperframes 门跳过
"""

from __future__ import annotations

from clipwright.services.render import RenderService


def _mk_clip(metadata=None, text="字幕", font_size=48, font=None, keyframes=None):
    """构造一个最小 TextClip 样式的对象（不依赖真实 schema）。"""

    class _Clip:
        pass

    c = _Clip()
    c.metadata = metadata or {}
    c.text = text
    c.font_size = font_size
    c.font = font
    c.font_color = "#ffffff"
    c.start_sec = 1.0
    c.duration_sec = 3.0
    c.keyframes = keyframes or []
    return c


def _svc(tmp_path):
    return RenderService(tmp_path)


class TestCaptionOffsetY:
    """caption 叠加 offset_y 强制为 0。"""

    def test_caption_offset_y_forced_zero(self, tmp_path) -> None:
        """caption 即使同一轨道第 N 条也必须是 offset_y == 0。"""
        first = RenderService._extract_text_overlay(
            _mk_clip(metadata={"category": "caption", "renderer": "drawtext"}), 2, [])
        second = RenderService._extract_text_overlay(
            _mk_clip(metadata={"category": "caption", "renderer": "drawtext"}), 2, [first])
        assert first["offset_y"] == 0
        assert second["offset_y"] == 0

    def test_non_caption_offset_y_unchanged(self, tmp_path) -> None:
        """非 caption 文字叠加保持既有 35px/行 堆叠行为（固定既有行为）。"""
        c = _mk_clip(metadata={"category": ""})
        first = RenderService._extract_text_overlay(c, 2, [])
        second = RenderService._extract_text_overlay(c, 2, [first])
        third = RenderService._extract_text_overlay(c, 2, [first, second])
        assert first["offset_y"] == 0
        assert second["offset_y"] == 35
        assert third["offset_y"] == 70


class TestCaptionStroke:
    """caption 无显式 style 时注入默认描边。"""

    def test_caption_default_stroke_injected(self, tmp_path) -> None:
        """caption 无 style → 注入 stroke_width=2 / stroke_color=#000000。"""
        ov = RenderService._extract_text_overlay(
            _mk_clip(metadata={"category": "caption", "renderer": "drawtext"}), 2, [])
        style = ov["style"]
        assert style.get("stroke_width") == 2
        assert style.get("stroke_color") == "#000000"

    def test_caption_explicit_style_preserved(self, tmp_path) -> None:
        """caption 有显式 style → 不覆盖用户配置。"""
        ov = RenderService._extract_text_overlay(
            _mk_clip(metadata={"category": "caption", "style": {"font_size": 40}}), 2, [])
        assert ov["style"] == {"font_size": 40}

    def test_build_drawtext_filter_contains_borderw(self, tmp_path) -> None:
        """默认描边流入 drawtext filter：真实字符串包含 borderw=2。"""
        ov = RenderService._extract_text_overlay(
            _mk_clip(metadata={"category": "caption", "renderer": "drawtext"}), 2, [])
        f = _svc(tmp_path)._build_drawtext_filter(ov)
        assert f is not None
        assert "borderw=2" in f
        assert "bordercolor=#000000" in f


class TestCaptionPath:
    """caption 走 drawtext/text 路径，不被 hyperframes 门跳过。"""

    def test_caption_not_skipped_by_hyperframes_gate(self, tmp_path) -> None:
        """_apply_text_concat 的跳过条件对 caption 为 False。"""
        ov = RenderService._extract_text_overlay(
            _mk_clip(metadata={"category": "caption", "renderer": "drawtext"}), 2, [])
        assert ov["renderer"] == "drawtext"
        assert not ov.get("diagram_params")
        # _apply_text_concat: if ov.get("renderer") == "hyperframes" or ov.get("diagram_params"): continue
        assert not (ov.get("renderer") == "hyperframes" or ov.get("diagram_params"))


class TestCaptionMalformed:
    """畸形输入：无字体配置不能崩溃。"""

    def test_no_font_does_not_crash(self, tmp_path, monkeypatch) -> None:
        """_resolve_system_font 为空时 font_arg 为空串，不抛异常。"""
        monkeypatch.setattr("clipwright.services.render._resolve_system_font", lambda: "")
        ov = RenderService._extract_text_overlay(
            _mk_clip(metadata={"category": "caption", "renderer": "drawtext"}, font=""), 2, [])
        f = _svc(tmp_path)._build_drawtext_filter(ov)
        assert f is not None
        assert "fontfile=" not in f
        assert "borderw=2" in f
