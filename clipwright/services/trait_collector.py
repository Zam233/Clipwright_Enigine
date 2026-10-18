"""类型同一性的指标采集（PRD §I.3.4 / F12 L1，A-09）。

把时间线上能**确定性算出来**的东西翻成 ``signature_traits`` 需要的数字：

| 指标 | 来源 |
|---|---|
| ``avg_shot_ms`` / ``base_shot_ms`` | 视频轨片段时长的均值 / 中位数 |
| ``repeat_shot_ratio`` | 重复 ``asset_id`` 片段占比（鬼畜的机械重复） |
| ``chart_count`` | 场景描述里的数据图表标记数（知识/评测的图表支撑） |
| ``argument_segment_count`` | 场景数（论证段数） |
| ``text_overlay_ratio`` | 文字/字幕轨时长 / 总时长（克制排版） |
| ``illustration_clip_ratio`` | 图片类片段时长占比（二维美术调性） |
| ``beat_align_ratio`` | 视频切点落在拍/半拍网格的比例（卡点驱动） |
| ``keyframe_density`` | 每秒关键帧数（运镜密度） |

**只做度量，不做判断**——判断在 ``genre_conformance``，且 L1 恒开、L2/L3 受开关控制。
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

from clipwright.schema.timeline import Clip, ClipKind, Timeline

#: 场景描述里的"数据图表"标记（与 structure_agent 的硬规则同源）。
CHART_MARKER_RE = re.compile(r"\[(?:逻辑动画|文字动画|过渡动画|动画)\]|mg_[a-z_]+|data_chart")

#: 本模块认识的指标名（供校验器提示"未知指标"）。
KNOWN_TRAITS: tuple[str, ...] = (
    "avg_shot_ms",
    "base_shot_ms",
    "repeat_shot_ratio",
    "chart_count",
    "argument_segment_count",
    "text_overlay_ratio",
    "illustration_clip_ratio",
    "beat_align_ratio",
    "keyframe_density",
)


def _video_clips(timeline: Timeline) -> list[Clip]:
    clips: list[Clip] = []
    for track in timeline.tracks or []:
        if track.kind == ClipKind.VIDEO:
            clips.extend(track.clips or [])
    return clips


def _text_clips(timeline: Timeline) -> list[Clip]:
    clips: list[Clip] = []
    for track in timeline.tracks or []:
        if track.kind in (ClipKind.CAPTION, ClipKind.TEXT):
            clips.extend(track.clips or [])
    return clips


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _cuts(timeline: Timeline) -> list[float]:
    """视频轨的切点（不含 0）。"""
    return sorted(clip.start_sec for clip in _video_clips(timeline) if clip.start_sec > 0)


def collect_traits(
    timeline: Timeline,
    *,
    scenes: Optional[list[dict[str, Any]]] = None,
    grid: Any = None,
    fps: Optional[float] = None,
) -> dict[str, float]:
    """采集全部已知指标；取不到的一律给 0（不许缺键，避免下游 KeyError）。"""
    fps = float(fps if fps is not None else timeline.fps or 30.0)
    total = float(timeline.duration_sec or 0.0)
    videos = _video_clips(timeline)
    durations = [float(clip.duration_sec) * 1000.0 / max(clip.speed or 1.0, 1e-6) for clip in videos]

    traits: dict[str, float] = {name: 0.0 for name in KNOWN_TRAITS}
    if durations:
        traits["avg_shot_ms"] = sum(durations) / len(durations)
        traits["base_shot_ms"] = _median(durations)

    if videos:
        assets = [clip.asset_id for clip in videos if clip.asset_id]
        seen: set[str] = set()
        repeats = 0
        for asset in assets:
            if asset in seen:
                repeats += 1
            else:
                seen.add(asset)
        traits["repeat_shot_ratio"] = repeats / len(assets) if assets else 0.0

    scene_list = list(scenes or [])
    if scene_list:
        traits["argument_segment_count"] = float(len(scene_list))
        hits = 0
        for scene in scene_list:
            text = f"{scene.get('title', '')} {scene.get('description', '')}"
            if CHART_MARKER_RE.search(text):
                hits += 1
        traits["chart_count"] = float(hits)

    if total > 0:
        text_seconds = sum(float(clip.duration_sec) for clip in _text_clips(timeline))
        traits["text_overlay_ratio"] = min(1.0, text_seconds / total)
        image_seconds = sum(
            float(clip.duration_sec) for clip in _image_clips(timeline)
        )
        traits["illustration_clip_ratio"] = min(1.0, image_seconds / total)

    if grid is not None and getattr(grid, "beat_sec", 0) > 0:
        cuts = _cuts(timeline)
        if cuts:
            on_grid = sum(1 for t in cuts if grid.is_on_grid(t, fps=fps, tolerance_frames=1.0))
            traits["beat_align_ratio"] = on_grid / len(cuts)

    keyframes = sum(len(clip.keyframes or []) for clip in videos)
    if total > 0:
        traits["keyframe_density"] = keyframes / total

    return traits


def _image_clips(timeline: Timeline) -> list[Clip]:
    clips: list[Clip] = []
    for track in timeline.tracks or []:
        if track.kind == ClipKind.IMAGE:
            clips.extend(track.clips or [])
    return clips


def trait_snapshot(measured: dict[str, float], names: Iterable[str]) -> dict[str, float]:
    """只取指定指标（缺失补 0），供报告与校验器使用。"""
    return {name: float(measured.get(name, 0.0)) for name in names}


__all__ = ["CHART_MARKER_RE", "KNOWN_TRAITS", "collect_traits", "trait_snapshot"]
