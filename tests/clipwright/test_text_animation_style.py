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


async def test_ass_typewriter_reveals_per_char_and_centers(tmp_path, monkeypatch):
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
    assert len(lines) == 6
    # 每个字独立出现、使用固定 left-top 位置；不能每个字各自居中
    assert all(r"\pos(" in ln and r"\an7" in ln for ln in lines)
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
    # 第一个样式是 box：BorderStyle=3；第二个是普通描边
    style_lines = [ln for ln in ass.splitlines() if ln.startswith("Style:")]
    assert style_lines[0].split(",")[15] == "3"
    assert style_lines[1].split(",")[15] == "1"
