"""文字动画统一 MG HTML：适配层测试。"""

from __future__ import annotations

from plugins.text_motion_core.providers.core import build_text_mgdef
from clipwright.animation.mg_renderer import MGRenderer

def render_text_mg_html(mg_def, *, width, height, fps=30.0):
    return MGRenderer.render(mg_def, params={}, width=width, height=height, fps=fps)


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


def test_gradient_is_supported_via_background_clip():
    mg = build_text_mgdef(
        _base(), text="渐变", style={"fill": {"type": "gradient", "colors": ["#f0f", "#0ff"]}},
        motion={"type": "fade_in"}, width=1920, height=1080, display_sec=2.0,
    )
    assert any(e.get("text_gradient") for e in mg["elements"] if e.get("type") == "text")


def test_all_motion_types_render_to_html():
    for motion in ("fade_in", "fade_out", "slide_in", "scale_in", "typewriter",
                   "word_by_word", "highlight", "glitch", "neon"):
        mg = build_text_mgdef(
            _base(), text="效果", style={"font_size": 64, "fill": "#ffffff"},
            motion={"type": motion, "reveal_sec": 0.4}, width=1920, height=1080,
            display_sec=2.0,
        )
        html = render_text_mg_html(mg, width=1920, height=1080)
        assert "<!DOCTYPE html>" in html, motion


def test_gradient_text_uses_background_clip():
    mg = build_text_mgdef(
        _base(), text="渐变", style={"font_size": 64, "fill": {"type": "gradient", "colors": ["#ff00ff", "#00ffff"]}},
        motion={"type": "fade_in"}, width=1920, height=1080, display_sec=2.0,
    )
    html = render_text_mg_html(mg, width=1920, height=1080)
    assert "background-clip:text" in html


def test_highlight_text_has_default_shadow():
    mg = build_text_mgdef(
        _base(), text="高亮", style={"font_size": 64, "fill": "#ffffff"},
        motion={"type": "highlight", "reveal_sec": 0.8},
        width=1920, height=1080, display_sec=2.0,
    )
    text = next(e for e in mg["elements"] if e.get("type") == "text")
    assert "rgba(0,0,0,0.78)" in text["text_shadow"]


def test_boxed_text_has_default_shadow():
    mg = build_text_mgdef(
        _base(), text="方块", style={"font_size": 64, "fill": "#ffffff", "box": True,
                                     "box_color": "#4f8cff"},
        motion={"type": "fade_in"}, width=1920, height=1080, display_sec=2.0,
    )
    text = next(e for e in mg["elements"] if e.get("type") == "text")
    assert "rgba(0,0,0,0.78)" in text["text_shadow"]
