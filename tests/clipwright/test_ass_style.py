"""T2 (C6a) — ASS/libass 字幕渲染：TextStyle ASS 构建 + render.py `-vf ass=` 路径。

覆盖：
- color_to_ass 颜色转换（#RRGGBB / #RRGGBBAA → &HAABBGGRR）
- build_ass_style 14 字段映射（Spacing/Outline/Bold/Italic/Shadow/Alignment）
- build_ass_dialogue override tags（\\an2 底部 / \\i1 斜体 / \\fsp5 / \\blur8 / \\bord3）
- Dialogue end 裁剪（Bug：xfade 缩短成片 → 最后几秒字幕消失）
- drawtext 回退路径（settings.caption_renderer = "drawtext"）
"""

from __future__ import annotations

import types

from clipwright.services.render import RenderService
from clipwright.tool.design import TextStyle, color_to_ass


def _ass_file(svc: RenderService) -> str:
    return (svc._work_dir / "subs_0.ass").read_text(encoding="utf-8")


def _dialogue_lines(ass_text: str) -> list[str]:
    return [l for l in ass_text.splitlines() if l.startswith("Dialogue:")]


async def _render_ass(svc: RenderService, overlays: list[dict], monkeypatch, actual_dur: float = 0.0):
    """跑 ASS 路径：mock 掉 ffprobe 时长探测 + ffmpeg 调用，返回 (返回结果, 捕获的 cmd)。"""
    import clipwright.services.render as render_mod
    monkeypatch.setattr(render_mod, "_get_actual_duration", lambda p: actual_dur)
    captured: dict = {}
    async def fake_ff(cmd, **kw):
        captured["cmd"] = cmd
        return types.SimpleNamespace(returncode=0)
    monkeypatch.setattr(svc, "_ff", fake_ff)
    out = await svc._apply_text_concat("video.mp4", overlays, "libx264", "medium",
                                       width=1920, height=1080)
    return out, captured


class TestColorToAss:
    def test_white(self) -> None:
        assert color_to_ass("#FFFFFF") == "&H00FFFFFF"

    def test_blue(self) -> None:
        assert color_to_ass("#4F8CFF") == "&H00FF8C4F"

    def test_with_alpha(self) -> None:
        assert color_to_ass("#00000080") == "&H80000000"
        assert color_to_ass("#FF000080") == "&H800000FF"

    def test_lowercase(self) -> None:
        assert color_to_ass("#ffffff") == "&H00FFFFFF"

    def test_invalid_passthrough(self) -> None:
        assert color_to_ass("") == ""
        assert color_to_ass("0xffffff") == "0xffffff"
        assert color_to_ass("#FFF") == "#FFF"


class TestBuildAssStyle14Fields:
    def test_style_line_full_mapping(self) -> None:
        ts = TextStyle.from_dict({
            "font_size": 60, "font_color": "#FFFFFF", "stroke_width": 3.0,
            "stroke_color": "#FF0000", "shadow_x": 4.0, "shadow_y": 4.0,
            "shadow_color": "#000000", "shadow_blur": 6.0,
            "font_weight": "bold", "font_italic": True, "letter_spacing": 5.0,
        })
        style = ts.build_ass_style(1920, 1080)
        assert "[Script Info]" in style
        assert "PlayResX: 1920" in style
        assert "PlayResY: 1080" in style
        style_line = [l for l in style.splitlines() if l.startswith("Style:")][0]
        expected = ("Style: Default,MSYH,60,&H00FFFFFF,&H00FFFFFF,&H000000FF,&H00000000,"
                    "-1,-1,0,0,100,100,5,0,1,3,1,2,10,10,10,1")
        assert style_line == expected

    def test_default_style_no_bold_no_italic_no_shadow(self) -> None:
        ts = TextStyle()
        style_line = [l for l in ts.build_ass_style().splitlines() if l.startswith("Style:")][0]
        expected = ("Style: Default,MSYH,48,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,"
                    "0,0,0,0,100,100,0,0,1,0,0,2,10,10,10,1")
        assert style_line == expected

    def test_shadow_flag_zero_when_no_shadow(self) -> None:
        ts = TextStyle.from_dict({"font_color": "#ffffff", "stroke_width": 2.0,
                                  "shadow_x": 0, "shadow_y": 0, "shadow_blur": 0})
        style_line = [l for l in ts.build_ass_style().splitlines() if l.startswith("Style:")][0]
        assert ",1,2,0,2," in style_line  # BorderStyle=1, Outline=2, Shadow=0, Alignment=2

    def test_position_alignments(self) -> None:
        cases = {"bottom": 2, "top": 8, "center": 5, "left": 4, "right": 6,
                 "bottom_left": 1, "bottom_right": 3, "top_left": 7, "top_right": 9}
        for pos, n in cases.items():
            style_line = [l for l in TextStyle(position=pos).build_ass_style().splitlines()
                          if l.startswith("Style:")][0]
            assert style_line.rstrip().endswith(f",{n},10,10,10,1")


class TestBuildAssDialogue:
    def test_basic_bottom(self) -> None:
        d = TextStyle(position="bottom").build_ass_dialogue("Hello", 1.0, 4.0)
        assert d.startswith("Dialogue: 0,0:00:01.00,0:00:04.00,Default,,0,0,0,,")
        assert r"\an2" in d
        assert d.endswith("Hello")

    def test_override_tags_italic_spacing_blur_bord(self) -> None:
        ts = TextStyle.from_dict({"font_italic": True, "letter_spacing": 5.0,
                                  "shadow_blur": 8.0, "stroke_width": 3.0, "position": "bottom"})
        d = ts.build_ass_dialogue("Hi", 0.0, 2.0)
        assert r"\an2" in d
        assert r"\i1" in d
        assert r"\fsp5" in d
        assert r"\blur8" in d
        assert r"\bord3" in d

    def test_letter_spacing_zero_not_emitted(self) -> None:
        d = TextStyle(position="bottom").build_ass_dialogue("Hi", 0.0, 1.0)
        assert r"\fsp" not in d

    def test_top_alignment(self) -> None:
        d = TextStyle(position="top").build_ass_dialogue("Top", 0.0, 1.0)
        assert r"\an8" in d

    def test_glow_override(self) -> None:
        ts = TextStyle.from_dict({"glow_width": 8.0, "glow_color": "#FFFF00", "position": "bottom"})
        d = ts.build_ass_dialogue("Glow", 0.0, 1.0)
        assert r"\bord8" in d
        assert r"\blur8" in d
        assert r"\c&H0000FFFF" in d

    def test_escape_braces(self) -> None:
        d = TextStyle().build_ass_dialogue("a{b}c", 0.0, 1.0)
        assert r"a\{b\}c" in d

    def test_time_format(self) -> None:
        d = TextStyle().build_ass_dialogue("t", 75.5, 90.05)
        assert d.startswith("Dialogue: 0,0:01:15.50,0:01:30.05,Default,,0,0,0,,")


class TestApplyTextAss:
    def _ov(self, **kw) -> dict:
        base = dict(text="Hello", start_sec=1.0, duration_sec=3.0, font_size=48,
                    font_color="#ffffff", font="", position="bottom", offset_y=0,
                    style={"position": "bottom"}, anim_type="", renderer="drawtext", keyframes=[])
        base.update(kw)
        return base

    async def test_dialogue_end_clamped_to_actual_duration(self, tmp_path, monkeypatch) -> None:
        """overlay 结束超过成片实际时长（xfade 缩短成片）→ Dialogue end 裁剪。"""
        svc = RenderService(work_dir=tmp_path)
        ov = self._ov(start_sec=1.0, duration_sec=30.0)
        out, _ = await _render_ass(svc, [ov], monkeypatch, actual_dur=10.0)
        assert out == "video.mp4"
        dialogues = _dialogue_lines(_ass_file(svc))
        assert len(dialogues) == 1
        assert ",0:00:01.00,0:00:10.00," in dialogues[0]  # 30s 结束被裁剪到 10s
        assert "0:00:30.00" not in dialogues[0]

    async def test_dialogue_end_within_duration_unchanged(self, tmp_path, monkeypatch) -> None:
        svc = RenderService(work_dir=tmp_path)
        ov = self._ov(start_sec=1.0, duration_sec=3.0)
        _, _ = await _render_ass(svc, [ov], monkeypatch, actual_dur=10.0)
        d = _dialogue_lines(_ass_file(svc))[0]
        assert ",0:00:01.00,0:00:04.00," in d  # 未超时 → 不裁剪

    async def test_script_info_and_playres(self, tmp_path, monkeypatch) -> None:
        svc = RenderService(work_dir=tmp_path)
        ov = self._ov()
        _, _ = await _render_ass(svc, [ov], monkeypatch, actual_dur=10.0)
        ass_text = _ass_file(svc)
        assert "[Script Info]" in ass_text
        assert "PlayResX: 1920" in ass_text
        assert "PlayResY: 1080" in ass_text
        assert "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text" in ass_text

    async def test_empty_text_no_ass_file(self, tmp_path, monkeypatch) -> None:
        svc = RenderService(work_dir=tmp_path)
        ov = self._ov(text="")
        out, _ = await _render_ass(svc, [ov], monkeypatch, actual_dur=10.0)
        assert out == "video.mp4"
        assert not (svc._work_dir / "subs_0.ass").exists()

    async def test_hyperframes_overlay_skipped(self, tmp_path, monkeypatch) -> None:
        svc = RenderService(work_dir=tmp_path)
        ov = self._ov(renderer="hyperframes", diagram_params={"preset": "diagram"})
        out, _ = await _render_ass(svc, [ov], monkeypatch, actual_dur=10.0)
        assert out == "video.mp4"
        assert not (svc._work_dir / "subs_0.ass").exists()


class TestDrawtextFallback:
    async def test_caption_renderer_drawtext_uses_old_chain(self, tmp_path, monkeypatch) -> None:
        from clipwright.config import settings
        monkeypatch.setattr(settings, "caption_renderer", "drawtext", raising=False)
        svc = RenderService(work_dir=tmp_path)
        ov = dict(text="Hello", start_sec=1.0, duration_sec=3.0, font_size=48,
                  font_color="#ffffff", font="", position="bottom", offset_y=0,
                  style={"font_color": "#ffffff"}, anim_type="", renderer="drawtext", keyframes=[])
        captured: dict = {}
        async def fake_ff(cmd, **kw):
            captured["cmd"] = cmd
            return types.SimpleNamespace(returncode=0)
        monkeypatch.setattr(svc, "_ff", fake_ff)
        await svc._apply_text_concat("video.mp4", [ov], "libx264", "medium", width=1920, height=1080)
        vf_arg = captured["cmd"][captured["cmd"].index("-vf") + 1]
        assert "drawtext=" in vf_arg
        assert "ass=" not in vf_arg
        assert not (svc._work_dir / "subs_0.ass").exists()
