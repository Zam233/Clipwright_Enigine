"""文字动画样式/Typewriter 契约（PRD M1/M3）。"""

from __future__ import annotations

import types

from clipwright.animation.text_style import (
    ensure_text_animation, normalize_motion, normalize_style,
)
from clipwright.services.render import RenderService


def _dialogue_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.startswith("Dialogue:")]


async def _render(svc, overlays, monkeypatch, actual_dur=10.0) -> str:
    import clipwright.services.render as render_mod

    monkeypatch.setattr(render_mod, "_get_actual_duration", lambda p: actual_dur)

    async def fake_ff(cmd, **kw):
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(svc, "_ff", fake_ff)
    await svc._apply_text_concat("video.mp4", overlays, "libx264", "medium",
                                  width=1920, height=1080)
    return (svc._work_dir / "subs_0.ass").read_text(encoding="utf-8")


def test_style_normalization_and_motion_reveal():
    style = normalize_style({"font_size": 999, "fill": "bad", "position": "bottom"})
    assert style["font_size"] == 240
    assert style["fill"] == "#ffffff"
    assert style["position"] == "center"  # 文字动画必须避开字幕带
    motion = normalize_motion({"type": "typewriter"}, text="离线下载功能")
    assert motion["type"] == "typewriter"
    assert 0.6 <= motion["reveal_sec"] <= 2.0


def test_ensure_text_animation_legacy_and_duration_reason():
    mg = {
        "duration_sec": 8,
        "elements": [{"type": "text", "content": "没有通报", "font_size": 56,
                      "font_color": "#ffffff", "keyframes": [{"time": 0, "opacity": 0},
                                                              {"time": 1, "opacity": 1}]}],
    }
    ta = ensure_text_animation(mg, text="没有通报", host_duration=8)
    assert ta["legacy"] is True
    assert ta["display_sec"] <= 8
    assert ta["duration_reason"]


async def test_ass_legacy_typewriter_is_static_fade(tmp_path, monkeypatch):
    svc = RenderService(work_dir=tmp_path)
    ov = {
        "start_sec": 1.0, "duration_sec": 8.0, "text": "离线下载功能",
        "style": {"font_size": 56, "fill": "#ffffff", "stroke_width": 2},
        "position": "center", "category": "text",
        "anim_type": "typewriter",
        "motion": {"type": "typewriter", "reveal_sec": 1.2},
    }
    ass = await _render(svc, [ov], monkeypatch)
    lines = _dialogue_lines(ass)
    # 旧时间线的 motion 已退役：静态文字 + 淡入，不再逐字/逐 pos 输出。
    assert len(lines) == 1
    assert r"\fad(200,200)" in ass
    assert r"\pos(" not in ass
    assert "Style: T0" in ass


async def test_ass_per_overlay_style_and_box(tmp_path, monkeypatch):
    svc = RenderService(work_dir=tmp_path)
    overlays = [
        {"start_sec": 0.0, "duration_sec": 2.0, "text": "红色底板",
         "style": {"font_size": 56, "fill": "#ffffff", "box": True,
                   "box_color": "#dc1414", "box_border": 10},
         "category": "text"},
        {"start_sec": 2.0, "duration_sec": 2.0, "text": "黄色高亮",
         "style": {"font_size": 72, "fill": "#ffd400", "stroke_width": 4},
         "category": "text"},
    ]
    ass = await _render(svc, overlays, monkeypatch)
    assert "Style: T0" in ass and "Style: T1" in ass
    # box 由矢量绘图实现（\p1）；文字样式本身不能再用 BorderStyle=3 叠方框
    style_lines = [ln for ln in ass.splitlines() if ln.startswith("Style:")]
    assert style_lines[0].split(",")[15] == "1"
    assert style_lines[1].split(",")[15] == "1"
    assert r"\p1" in ass and r"\p0" in ass


async def test_ass_slide_and_scale_motion(tmp_path, monkeypatch):
    svc = RenderService(work_dir=tmp_path)
    for motion, needle in (
        ({"type": "slide_in", "reveal_sec": 0.4}, r"\move("),
        ({"type": "scale_in", "reveal_sec": 0.4}, r"\t(0,"),
    ):
        ov = {"start_sec": 1.0, "duration_sec": 3.0, "text": "滑入",
              "style": {"font_size": 56, "fill": "#ffffff"},
              "position": "center", "category": "text", "motion": motion}
        ass = await _render(svc, [ov], monkeypatch)
        # 旧 motion 字段不再生成 ASS 动效；文字按静态 ASS 输出。
        assert r"\move(" not in ass
        assert r"\t(" not in ass
        assert r"\fad(200,200)" in ass


async def test_ass_word_by_word_and_cursor(tmp_path, monkeypatch):
    svc = RenderService(work_dir=tmp_path)
    ov = {"start_sec": 1.0, "duration_sec": 4.0, "text": "离线 下载 功能",
          "style": {"font_size": 56, "fill": "#ffffff"},
          "position": "center", "category": "text",
          "motion": {"type": "word_by_word", "reveal_sec": 1.2, "cursor": True}}
    ass = await _render(svc, [ov], monkeypatch)
    lines = [ln for ln in ass.splitlines() if ln.startswith("Dialogue:")]
    # 旧时间线的 motion 已退役：整段静态文字，无 cursor。
    assert len(lines) == 1
    assert "▍" not in ass
