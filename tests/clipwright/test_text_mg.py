"""文字动画统一 MG HTML：适配层测试。"""

from __future__ import annotations

from clipwright.animation.text_mg import build_text_mgdef, render_text_mg_html


def _base():
    return {
        "animation_id": "text_test", "duration_sec": 3.0, "width": 1920, "height": 1080,
        "elements": [{"type": "text", "content": "离线下载功能", "x": "center", "y": "center",
                      "keyframes": [{"time": 0, "opacity": 0}, {"time": 1, "opacity": 1}]}],
    }


def test_typewriter_becomes_per_char_mg_elements():
    mg = build_text_mgdef(
        _base(), text="离线下载功能",
        style={"font_size": 64, "fill": "#ffffff", "box": True, "box_color": "#dc1414"},
        motion={"type": "typewriter", "reveal_sec": 1.2},
        width=1920, height=1080, display_sec=3.0,
    )
    texts = [e for e in mg["elements"] if e.get("type") == "text"]
    assert len(texts) == 6
    assert all(e.get("content") in "离线下载功能" for e in texts)
    assert all(len(e.get("keyframes") or []) >= 3 for e in texts)
    assert any(e.get("type") == "shape" for e in mg["elements"])  # box


def test_rendered_html_is_hyperframes_document():
    mg = build_text_mgdef(
        _base(), text="测试",
        style={"font_size": 56, "fill": "#ffd400"},
        motion={"type": "slide_in", "reveal_sec": 0.5},
        width=1920, height=1080, display_sec=2.0,
    )
    html = render_text_mg_html(mg, width=1920, height=1080)
    assert "<!DOCTYPE html>" in html
    assert "data-duration=\"2.00\"" in html
    assert "测试" in html
