"""Tests for Todo 16 (C6b) — QualityAgent blank-shot detection + animation-effectiveness checks.

覆盖场景：
- (a) 黑帧素材（mock is_blank=True）→ error(material) + redo_agent=="material"
- (b) renderer=="drawtext" 动画 clip → warning(animation) 且 message 含降级原因
- (c) 正常时间线 → 无新增 error
- (d) frame_validator 抛异常/valid=False → 该 clip 记 warning 而非 error
- (e) 越界 clip（start+duration > timeline.duration）→ warning
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from clipwright.agents.quality_agent import QualityAgent
from clipwright.schema.agent import AgentContext, QualityInput
from clipwright.schema.timeline import Clip, ClipKind, Timeline, Track
from clipwright.schema.tool import ToolExecResult, ToolStatus


def _ctx() -> AgentContext:
    return AgentContext(
        pipeline_id="p_test",
        persona_id="test_persona",
        category_plugin_id="test_plugin",
        topic="test topic",
    )


def _ok(**overrides) -> ToolExecResult:
    """构造 frame_validator 成功输出。"""
    output = {
        "valid": True,
        "is_blank": False,
        "is_white": False,
        "is_overexposed": False,
        "sample_count": 2,
        "match_score": 1.0,
    }
    output.update(overrides)
    return ToolExecResult(status=ToolStatus.SUCCESS, tool_name="frame_validator", output=output)


def _invalid(error: str) -> ToolExecResult:
    """构造 frame_validator 失败输出（valid=False + error，绝不抛异常）。"""
    return ToolExecResult(
        status=ToolStatus.SUCCESS,
        tool_name="frame_validator",
        output={
            "valid": False,
            "is_blank": False,
            "is_white": False,
            "is_overexposed": False,
            "sample_count": 0,
            "match_score": 0.0,
            "error": error,
        },
    )


def _frame_executor(route_map: dict) -> object:
    """构造 ToolRegistry.execute 的 side_effect：按 video_url 子串/回调分发结果。

    route_map: {path_substr 或 callable(path)->bool: ToolExecResult 或 BaseException}
    """

    async def _side_effect(name: str, **kwargs: object) -> ToolExecResult:
        assert name == "frame_validator", f"unexpected tool: {name}"
        path = str(kwargs.get("video_url", ""))
        for key, result in route_map.items():
            matched = key(path) if callable(key) else key in path
            if not matched:
                continue
            if isinstance(result, BaseException):
                raise result
            return result
        raise AssertionError(f"unexpected video_url: {path!r}")

    return _side_effect


async def _run(input_data: QualityInput, route_map: dict) -> object:
    with patch(
        "clipwright.tool.registry.ToolRegistry.execute",
        new=AsyncMock(side_effect=_frame_executor(route_map)),
    ):
        return await QualityAgent().execute(input_data, _ctx())


def _video_track(clips: list[Clip]) -> Track:
    return Track(id="t_video", name="video", kind=ClipKind.VIDEO, index=0, clips=clips)


def _audio_track() -> Track:
    return Track(
        id="t_audio",
        name="audio",
        kind=ClipKind.AUDIO,
        index=1,
        clips=[
            Clip(
                id="a1",
                kind=ClipKind.AUDIO,
                asset_id="music.mp3",
                track_id="t_audio",
                start_sec=0.0,
                duration_sec=60.0,
                volume=1.0,
            )
        ],
    )


@pytest.mark.asyncio
async def test_black_frame_clip_flagged_material_error() -> None:
    """(a) 黑帧素材 → error(category="material") 且 redo_agent=="material"。"""
    black_clip = Clip(
        id="v_black",
        kind=ClipKind.VIDEO,
        asset_id="media/black.mp4",
        track_id="t_video",
        start_sec=0.0,
        duration_sec=30.0,
    )
    timeline = Timeline(
        id="tl",
        duration_sec=60.0,
        tracks=[_video_track([black_clip]), _audio_track()],
    )
    out = await _run(
        QualityInput(context=_ctx(), timeline=timeline, constraints={}),
        {"black.mp4": _ok(is_blank=True, match_score=0.0)},
    )

    material_errors = [i for i in out.issues if i.severity == "error" and i.category == "material"]
    assert material_errors, "应产生 material error"
    assert "v_black" in material_errors[0].message
    assert "空镜头" in material_errors[0].message or "全白" in material_errors[0].message
    assert out.redo_agent == "material"


@pytest.mark.asyncio
async def test_white_frame_clip_flagged_material_error() -> None:
    """(a) 全白帧素材 → error(category="material")，触发 material 重做。"""
    white_clip = Clip(
        id="v_white",
        kind=ClipKind.VIDEO,
        asset_id="media/white.mp4",
        track_id="t_video",
        start_sec=0.0,
        duration_sec=20.0,
    )
    timeline = Timeline(
        id="tl",
        duration_sec=40.0,
        tracks=[_video_track([white_clip]), _audio_track()],
    )
    out = await _run(
        QualityInput(context=_ctx(), timeline=timeline, constraints={}),
        {"white.mp4": _ok(is_white=True, match_score=0.0)},
    )

    material_errors = [i for i in out.issues if i.severity == "error" and i.category == "material"]
    assert material_errors
    assert out.redo_agent == "material"


@pytest.mark.asyncio
async def test_downgraded_animation_clip_flagged_warning() -> None:
    """(b) renderer=="drawtext" 动画 clip → warning(animation) 且 message 含降级原因。"""
    anim_clip = Clip(
        id="anim_dt",
        kind=ClipKind.ANIMATION,
        asset_id="",
        track_id="t_anim",
        start_sec=0.0,
        duration_sec=5.0,
        metadata={
            "anim_type": "mg_dynamic",
            "category": "mg",
            "renderer": "drawtext",
            "mg_fallback_template": "template_basic",
        },
    )
    anim_track = Track(
        id="t_anim", name="anim", kind=ClipKind.ANIMATION, index=0, clips=[anim_clip]
    )
    timeline = Timeline(id="tl", duration_sec=30.0, tracks=[_video_track([
        Clip(id="v1", kind=ClipKind.VIDEO, asset_id="media/normal.mp4", track_id="t_video",
             start_sec=0.0, duration_sec=10.0),
    ]), anim_track])

    out = await _run(
        QualityInput(context=_ctx(), timeline=timeline, constraints={}),
        {"normal.mp4": _ok()},
    )

    anim_warnings = [i for i in out.issues if i.category == "animation"]
    assert anim_warnings
    assert all(i.severity == "warning" for i in anim_warnings)
    assert any("template_basic" in i.message for i in anim_warnings), \
        "message 应包含降级原因 mg_fallback_template"


@pytest.mark.asyncio
async def test_mg_hyperframes_without_html_flagged_warning() -> None:
    """(b) renderer=="mg_hyperframes" 但 mg_html 为空 → warning(category="animation")。"""
    anim_clip = Clip(
        id="anim_nohtml",
        kind=ClipKind.ANIMATION,
        asset_id="",
        track_id="t_anim",
        start_sec=0.0,
        duration_sec=5.0,
        metadata={"category": "mg", "renderer": "mg_hyperframes", "mg_html": ""},
    )
    anim_track = Track(
        id="t_anim", name="anim", kind=ClipKind.ANIMATION, index=0, clips=[anim_clip]
    )
    timeline = Timeline(id="tl", duration_sec=30.0, tracks=[_video_track([
        Clip(id="v1", kind=ClipKind.VIDEO, asset_id="media/normal.mp4", track_id="t_video",
             start_sec=0.0, duration_sec=10.0),
    ]), anim_track])

    out = await _run(
        QualityInput(context=_ctx(), timeline=timeline, constraints={}),
        {"normal.mp4": _ok()},
    )

    warnings = [i for i in out.issues if i.category == "animation" and i.severity == "warning"]
    assert any("anim_nohtml" in i.message for i in warnings)


@pytest.mark.asyncio
async def test_normal_timeline_produces_no_errors() -> None:
    """(c) 正常时间线 → 无 error，passed=True。"""
    video_clip = Clip(
        id="v1",
        kind=ClipKind.VIDEO,
        asset_id="media/normal.mp4",
        track_id="t_video",
        start_sec=0.0,
        duration_sec=30.0,
    )
    timeline = Timeline(
        id="tl",
        duration_sec=60.0,
        tracks=[_video_track([video_clip]), _audio_track()],
    )
    out = await _run(
        QualityInput(context=_ctx(), timeline=timeline, constraints={}),
        {"normal.mp4": _ok()},
    )

    assert not [i for i in out.issues if i.severity == "error"]
    assert out.passed is True
    assert out.redo_agent == ""


@pytest.mark.asyncio
async def test_frame_validator_raise_records_warning_not_error() -> None:
    """(d) frame_validator 抛异常 → 该 clip 记 warning 而非 error，其它检查不受影响。"""
    raise_clip = Clip(
        id="v_raise",
        kind=ClipKind.VIDEO,
        asset_id="media/raise.mp4",
        track_id="t_video",
        start_sec=0.0,
        duration_sec=5.0,
    )
    ok_clip = Clip(
        id="v_ok",
        kind=ClipKind.VIDEO,
        asset_id="media/ok.mp4",
        track_id="t_video",
        start_sec=5.0,
        duration_sec=8.0,
    )
    timeline = Timeline(
        id="tl",
        duration_sec=30.0,
        tracks=[_video_track([raise_clip, ok_clip]), _audio_track()],
    )
    out = await _run(
        QualityInput(context=_ctx(), timeline=timeline, constraints={}),
        {
            "raise.mp4": RuntimeError("frame_validator exploded"),
            "ok.mp4": _ok(),
        },
    )

    # 该 clip 记 warning 而非 error
    material_warnings = [
        i for i in out.issues if i.severity == "warning" and i.category == "material"
    ]
    assert any("v_raise" in i.message for i in material_warnings)
    assert not [i for i in out.issues if i.severity == "error" and i.category == "material"]
    # 其它 clip 未受影响
    assert not [i for i in out.issues if "v_ok" in i.message]
    # 整体仍通过（无 error）
    assert out.passed is True


@pytest.mark.asyncio
async def test_frame_validator_invalid_output_records_warning_not_error() -> None:
    """(d) frame_validator 返回 valid=False（工具不可用/路径缺失）→ warning 而非 error。"""
    missing_clip = Clip(
        id="v_missing",
        kind=ClipKind.VIDEO,
        asset_id="media/missing.mp4",
        track_id="t_video",
        start_sec=0.0,
        duration_sec=5.0,
    )
    timeline = Timeline(
        id="tl",
        duration_sec=30.0,
        tracks=[_video_track([missing_clip]), _audio_track()],
    )
    out = await _run(
        QualityInput(context=_ctx(), timeline=timeline, constraints={}),
        {"missing.mp4": _invalid("file not found: media/missing.mp4")},
    )

    material_warnings = [
        i for i in out.issues if i.severity == "warning" and i.category == "material"
    ]
    assert any("v_missing" in i.message for i in material_warnings)
    assert not [i for i in out.issues if i.severity == "error" and i.category == "material"]
    assert out.passed is True


@pytest.mark.asyncio
async def test_out_of_bounds_animation_clip_flagged_warning() -> None:
    """(e) 动画 clip 越界（start+duration > timeline.duration）→ warning。"""
    anim_clip = Clip(
        id="anim_oob",
        kind=ClipKind.ANIMATION,
        asset_id="",
        track_id="t_anim",
        start_sec=55.0,
        duration_sec=10.0,
        metadata={"category": "mg", "renderer": "hyperframes"},
    )
    anim_track = Track(
        id="t_anim", name="anim", kind=ClipKind.ANIMATION, index=0, clips=[anim_clip]
    )
    timeline = Timeline(id="tl", duration_sec=60.0, tracks=[_video_track([
        Clip(id="v1", kind=ClipKind.VIDEO, asset_id="media/normal.mp4", track_id="t_video",
             start_sec=0.0, duration_sec=30.0),
    ]), anim_track])

    out = await _run(
        QualityInput(context=_ctx(), timeline=timeline, constraints={}),
        {"normal.mp4": _ok()},
    )

    warnings = [i for i in out.issues if i.category == "animation" and i.severity == "warning"]
    assert any("anim_oob" in i.message and "越界" in i.message for i in warnings), \
        f"应产生越界 warning，实际: {warnings}"


@pytest.mark.asyncio
async def test_audio_track_content_not_frame_validated() -> None:
    """MUST NOT 检查音频轨内容：音频 clip 不触发 frame_validator 调用。"""
    audio_clip = Clip(
        id="a_music",
        kind=ClipKind.AUDIO,
        asset_id="media/boom.wav",
        track_id="t_audio",
        start_sec=0.0,
        duration_sec=10.0,
        volume=1.0,
    )
    video_clip = Clip(
        id="v1",
        kind=ClipKind.VIDEO,
        asset_id="media/normal.mp4",
        track_id="t_video",
        start_sec=0.0,
        duration_sec=10.0,
    )
    audio_track = Track(
        id="t_audio", name="audio", kind=ClipKind.AUDIO, index=1, clips=[audio_clip]
    )
    timeline = Timeline(
        id="tl",
        duration_sec=30.0,
        tracks=[_video_track([video_clip]), audio_track],
    )
    out = await _run(
        QualityInput(context=_ctx(), timeline=timeline, constraints={}),
        {"normal.mp4": _ok()},
    )

    assert not [i for i in out.issues if i.severity == "error"]
    # 音频 clip 不应产生任何 material 相关 issue
    assert not [i for i in out.issues if "boom.wav" in i.message or "a_music" in i.message]
