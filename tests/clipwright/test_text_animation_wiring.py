"""文字动画接线与降级（PRD text-animation-llm M2/M3）。

M1 只做了"生成器"，M2/M3 才是关键：`_handle_text_animation` 必须真的走 LLM，
失败回退模板并**如实登记降级**（主 agent 才看得见），位置必须避开字幕带。
"""

from __future__ import annotations

import asyncio

import pytest

from clipwright.agents import animation_agent as aa
from clipwright.schema.timeline import Clip, ClipKind, Track
from clipwright.services import degradation as degradation_mod


def _clip(**kw):
    base = dict(
        id="c1", kind=ClipKind.VIDEO, asset_id="a.mp4", track_id="t1",
        start_sec=10.0, duration_sec=3.0, text="",
        metadata={"scene_title": "四小时内说法层层变形", "narration_text": "一句话"},
    )
    base.update(kw)
    return Clip(**base)


def _track() -> Track:
    return Track(id="text_track", kind=ClipKind.TEXT, index=0, clips=[])


def _fake_result(ok=True, keyframes=None, method="text_llm"):
    class _R:
        def __init__(self):
            self.ok = ok
            self.method = method
            self.prompt_version = "abc123"
            self.error = "" if ok else "boom"
            self.mg_def = {
                "duration_sec": 3.0,
                "elements": [{
                    "type": "text", "content": "没有监管要求", "font_size": 64,
                    "color": "#F5C518",
                    "keyframes": keyframes if keyframes is not None else [
                        {"time": 0.0, "opacity": 0, "scale": 0.9},
                        {"time": 0.4, "opacity": 1, "scale": 1.0, "easing": "back-out"},
                        {"time": 2.4, "opacity": 1, "scale": 1.0},
                    ],
                }],
            }
    return _R()


def test_pick_text_type_heuristics():
    pick = aa.AnimationAgent._pick_text_type
    assert pick("「没有官方通报」") == "quote_block"
    assert pick("75 分钟") == "number_punch"
    assert pick("四小时") == "number_punch"
    assert pick("没有监管") == "emphasis_keyword"
    assert pick("一个传闻长出了三个版本的口径") == "reveal_line"
    assert pick("一个下午一个传闻长出了三个版本而且每个版本都在加码") == "title_stack"
    assert pick("先把结论放在前面") == "reveal_line"


def test_mgdef_to_drawtext_keyframes_offsets_and_style():
    kfs, style = aa.AnimationAgent._mgdef_to_drawtext_keyframes(
        _fake_result().mg_def, clip_start=10.0)
    assert [k["time"] for k in kfs] == [10.0, 10.4, 12.4]
    assert kfs[1]["properties"]["easing"] == "back-out"
    assert style["font_size"] == 64 and style["color"] == "#F5C518"
    assert style["text"] == "没有监管要求"


def test_llm_path_creates_center_clip(monkeypatch):
    agent = aa.AnimationAgent()
    captured = {}

    async def _fake_generate(type_id, **kw):  # noqa: ANN001, ANN003
        captured["type_id"] = type_id
        captured.update(kw)
        return _fake_result()

    monkeypatch.setattr("clipwright.animation.text_generation.generate_for_type", _fake_generate)
    track = _track()
    asyncio.run(agent._handle_text_animation_async(
        track, _clip(), "text_fade_in", "淡入", {"text": "没有监管要求"}, {"position": "bottom"}))

    assert captured["type_id"] == "emphasis_keyword"
    assert captured["caption_band"] is True
    assert len(track.clips) == 1
    clip = track.clips[0]
    assert clip.kind == ClipKind.TEXT
    assert clip.track_id == "text_track"
    assert clip.metadata["position"] == "center"
    assert clip.metadata["text_anim_type"] == "emphasis_keyword"
    assert clip.metadata["text_llm_method"] == "text_llm"
    assert clip.keyframes[0]["time"] == 10.0


def test_llm_failure_falls_back_to_template_with_degradation(monkeypatch):
    agent = aa.AnimationAgent()

    async def _fail(type_id, **kw):  # noqa: ANN001, ANN003
        return _fake_result(ok=False, method="text_failed")

    monkeypatch.setattr("clipwright.animation.text_generation.generate_for_type", _fail)
    track = _track()
    sink: list = []
    with degradation_mod.degradation_scope(sink):
        asyncio.run(agent._handle_text_animation_async(
            track, _clip(), "text_fade_in", "淡入", {"text": "没有监管要求"}, {}))
    kinds = [d.kind for d in sink]
    assert "text_llm_failed" in kinds
    assert "text_llm_fallback_template" in kinds
    # 回退模板仍然产出 clip（不因 LLM 失败丢动画）
    assert len(track.clips) == 1
    assert track.clips[0].metadata["position"] == "center"


def test_weak_motion_is_reported(monkeypatch):
    agent = aa.AnimationAgent()

    async def _weak(type_id, **kw):  # noqa: ANN001, ANN003
        return _fake_result(keyframes=[{"time": 0.0, "opacity": 0},
                                       {"time": 0.5, "opacity": 1}])

    monkeypatch.setattr("clipwright.animation.text_generation.generate_for_type", _weak)
    sink: list = []
    with degradation_mod.degradation_scope(sink):
        asyncio.run(agent._handle_text_animation_async(
            _track(), _clip(), "text_fade_in", "淡入", {"text": "没有监管要求"}, {}))
    assert any(d.kind == "text_motion_weak" for d in sink)


def test_flag_off_uses_template_only(monkeypatch):
    from clipwright.config import settings

    monkeypatch.setattr(settings, "text_animation_llm", False, raising=False)
    agent = aa.AnimationAgent()
    called = {"n": 0}

    async def _should_not_run(type_id, **kw):  # noqa: ANN001, ANN003
        called["n"] += 1
        return _fake_result()

    monkeypatch.setattr("clipwright.animation.text_generation.generate_for_type", _should_not_run)
    track = _track()
    asyncio.run(agent._handle_text_animation_async(
        track, _clip(), "text_fade_in", "淡入", {"text": "没有监管要求"}, {}))
    assert called["n"] == 0
    assert len(track.clips) == 1
    assert track.clips[0].metadata["position"] == "center"
