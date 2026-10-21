"""负时间片头（preroll）：把画面与音频整体后移，前面补一段片头（验收 V32）。

**为什么叫"负时间"**：片头里的画面在音乐时间轴上其实是负数——音乐 t=0 对应成片的
第 ``preroll`` 秒，片头这 ``preroll`` 秒里"歌还没开始"。做错的方式是只给画面加片头、
不给音频加延迟，成片会整体漂 ``preroll`` 秒（`tools/check_sync.py` 量得出来）。

本模块只做三件确定的事：

1. :func:`apply_preroll` —— 时间线整体后移 + 片头段补齐（纯函数，可单测）；
2. :func:`delay_audio` —— 把整轨音频延后同样的时长（ffmpeg adelay），保证音画不漂；
3. :func:`plan` —— 给出片头方案与预期时长，便于落日志与验收。
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from clipwright.schema.timeline import Clip, ClipKind, Timeline, Track

#: 片头上限（秒）——超过这个量级就不是"片头"而是另一条片子了。
MAX_PREROLL_SEC = 15.0


@dataclass(frozen=True)
class PrerollPlan:
    preroll_sec: float
    lead_segment_sec: float
    audio_delay_sec: float
    expected_video_sec: float

    def as_dict(self) -> dict[str, float]:
        return {
            "preroll_sec": round(self.preroll_sec, 3),
            "lead_segment_sec": round(self.lead_segment_sec, 3),
            "audio_delay_sec": round(self.audio_delay_sec, 3),
            "expected_video_sec": round(self.expected_video_sec, 3),
        }


def plan(timeline: Timeline, preroll_sec: float) -> PrerollPlan:
    """片头方案：视频 = 片头 + 原时长；音频延后 = 片头。"""
    duration = float(getattr(timeline, "duration_sec", 0) or 0)
    preroll = max(0.0, min(float(preroll_sec or 0.0), MAX_PREROLL_SEC))
    return PrerollPlan(
        preroll_sec=preroll,
        lead_segment_sec=preroll,
        audio_delay_sec=preroll,
        expected_video_sec=duration + preroll,
    )


def _first_visual_clip(timeline: Timeline) -> Clip | None:
    for track in timeline.tracks or []:
        if track.kind == ClipKind.AUDIO:
            continue
        for clip in track.clips or []:
            if clip.start_sec <= 1e-9 and clip.duration_sec > 0:
                return clip
    for track in timeline.tracks or []:
        if track.kind == ClipKind.AUDIO:
            continue
        for clip in track.clips or []:
            return clip
    return None


def apply_preroll(
    timeline: Timeline,
    preroll_sec: float,
    *,
    lead_color: str = "#000000",
) -> Timeline:
    """把时间线整体后移 ``preroll_sec``，并在片头区间补一段可见内容。

    - **所有轨道**（含音频轨）的片段统一后移，保证内部相对关系不变；
    - 片头段放在第一条非音频轨的最前面：优先复用第一个画面片段的素材（同一素材、
      源内 0 起），没有素材时退化成纯色垫片，避免"片头是黑的但时长够了"这种糊弄；
    - ``preroll_sec <= 0`` 时原样返回（零行为变化）。
    """
    preroll = float(preroll_sec or 0.0)
    if preroll <= 0:
        return timeline

    tracks: list[Track] = []
    lead_placed = False
    for track in timeline.tracks or []:
        clips = [
            clip.model_copy(update={"start_sec": round(clip.start_sec + preroll, 6)})
            for clip in (track.clips or [])
        ]
        if not lead_placed and track.kind != ClipKind.AUDIO:
            donor = _first_visual_clip(timeline)
            lead = Clip(
                id=f"preroll_{track.id}",
                kind=donor.kind if donor is not None else ClipKind.SHAPE,
                asset_id=(donor.asset_id if donor is not None else ""),
                track_id=track.id,
                start_sec=0.0,
                duration_sec=preroll,
                source_offset_sec=(donor.source_offset_sec if donor is not None else 0.0),
                fill=(donor.fill if donor is not None and donor.fill else lead_color),
                metadata={"preroll": True, "lead_color": lead_color},
            )
            clips.insert(0, lead)
            lead_placed = True
        tracks.append(track.model_copy(update={"clips": clips}))

    duration = float(getattr(timeline, "duration_sec", 0) or 0)
    return timeline.model_copy(
        update={"tracks": tracks, "duration_sec": round(duration + preroll, 6)}
    )


def delay_audio(src: str | Path, preroll_sec: float, out_path: str | Path) -> Path:
    """把整轨音频延后 ``preroll_sec``（前面补静音），返回新文件路径。

    这一步是"音画不漂"的关键：只给画面加片头、不给音频加延迟，成片会整体偏
    ``preroll`` 秒——`tools/check_sync.py` 就是为量这个而写的。
    """
    preroll = float(preroll_sec or 0.0)
    if preroll <= 0:
        return Path(src)
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("未找到 ffmpeg，无法延迟音频")
    source = Path(src)
    if not source.exists():
        raise FileNotFoundError(source)
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    delay_ms = int(round(preroll * 1000))
    result = subprocess.run(
        [
            ffmpeg, "-v", "error", "-y", "-i", str(source),
            "-af", f"adelay={delay_ms}:all=1",
            "-c:a", "pcm_s16le", str(target),
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0 or not target.exists():
        raise RuntimeError(f"音频延迟失败：{result.stderr[-400:]}")
    return target


__all__ = ["MAX_PREROLL_SEC", "PrerollPlan", "apply_preroll", "delay_audio", "plan"]
