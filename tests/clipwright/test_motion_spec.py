"""MotionSpec schema/interpolation/compiler/validator/plugin。"""

from __future__ import annotations

import json

import pytest

from clipwright.animation.motion_compiler import compile_motion_spec
from clipwright.animation.motion_interpolation import cubic_bezier_y, easing_progress, sample_path
from pathlib import Path

from clipwright.animation.motion_spec import MotionSpec
from clipwright.animation.motion_spec_validator import validate_motion_spec
from clipwright.animation.mg_renderer import MGRenderer


def _spec(**kw):
    data = {
        "schema": "motion_spec/1", "duration_sec": 2,
        "layers": [{"id": "t", "type": "text", "content": "测试",
                    "keyframes": [
                        {"time": 0, "opacity": 0, "translate_y": 30,
                         "easing": {"type": "cubic-bezier", "x1": 0.2, "y1": 0.8, "x2": 0.3, "y2": 1}},
                        {"time": 0.5, "opacity": 1, "translate_y": 0},
                        {"time": 2, "opacity": 1}]}],
    }
    data.update(kw)
    return MotionSpec.model_validate(data)


def test_bezier_endpoints_and_midpoint():
    assert cubic_bezier_y(0, 0.2, 0.8, 0.3, 1) == pytest.approx(0, abs=1e-3)
    assert cubic_bezier_y(1, 0.2, 0.8, 0.3, 1) == pytest.approx(1, abs=1e-3)
    assert 0 < cubic_bezier_y(0.5, 0.2, 0.8, 0.3, 1) < 1
    assert easing_progress({"type": "hold"}, 0.4) == 0


def test_path_line_circle():
    assert sample_path("line", [{"x": 0, "y": 0}, {"x": 1, "y": 1}], 0.5) == (0.5, 0.5)
    x, y = sample_path("circle", [{"x": 0.5, "y": 0.5}, {"x": 0.7, "y": 0.7}], 0)
    assert abs(x - 0.5) < 1e-6 and abs(y - 0.3) < 1e-6


def test_validator_and_compiler():
    spec = _spec()
    assert validate_motion_spec(spec).ok
    plan = compile_motion_spec(spec)
    assert plan.mg_def["elements"][0]["content"] == "测试"
    html = MGRenderer.render(plan.mg_def, params={}, width=640, height=360, fps=30)
    assert "cubic-bezier" in html


def test_spring_spec_compiles():
    spec = _spec(layers=[{"id": "t", "type": "text", "content": "弹", "keyframes": [
        {"time": 0, "opacity": 0, "easing": {"type": "spring", "stiffness": 180, "damping": 18}},
        {"time": 1, "opacity": 1}]}])
    plan = compile_motion_spec(spec)
    assert len(plan.mg_def["elements"][0]["keyframes"]) > 2


@pytest.mark.asyncio
async def test_llm_plugin_author(monkeypatch):
    from plugins.text_motion_llm.author import TextMotionAuthor
    from clipwright.animation.motion_spec import MotionAuthorRequest

    class Resp:
        content = json.dumps({"schema": "motion_spec/1", "duration_sec": 2,
            "layers": [{"id": "t", "type": "text", "content": "测试",
                        "keyframes": [{"time": 0, "opacity": 0}, {"time": 1, "opacity": 1}]}]})

    class FakeLLM:
        def __init__(self, *a, **k): pass
        async def generate(self, **k): return Resp()

    import clipwright.services.llm as llm_mod
    monkeypatch.setattr(llm_mod, "LLMService", FakeLLM)
    spec = await TextMotionAuthor().author(MotionAuthorRequest(
        text="测试", style={}, intent={}, width=640, height=360, duration_sec=2))
    assert spec.layers[0].content == "测试"


def test_engine_applies_motion_spec_to_clip():
    from clipwright.animation.text_engine import TextAnimationEngine
    from clipwright.schema.timeline import Clip, ClipKind
    spec = _spec()
    clip = Clip(id="c", kind=ClipKind.TEXT, asset_id="", track_id="t",
                start_sec=0, duration_sec=2, text="测试",
                metadata={"motion_spec": spec.model_dump(by_alias=True)})
    TextAnimationEngine().apply_style(clip, style={"font_size": 56, "fill": "#fff"},
                                      motion={"type": "llm_motion"}, width=640, height=360)
    assert clip.metadata["text_provider_id"] == "motion_spec"
    assert clip.metadata["mg_html"]


def test_render_module_does_not_import_llm_author():
    text = Path("clipwright/services/render.py").read_text(encoding="utf-8")
    assert "text_motion_llm" not in text
    assert "plugins.text_motion_llm" not in text
