"""语音边界对齐（E2E 2026-10-30：字幕与声音对不上）。

问题现场：上传配音 702.3s、字幕 321 段，时间轴单调且覆盖全片，但每句字幕
起点与真实语音起点的偏差在 **-1.64s ~ +1.13s** 之间（实测 200–260s 窗口）。
根因是字幕排序按"字数比例"摊到总时长——语速不均、停顿长度不同，误差逐句抖动。

修法：用 ffmpeg `silencedetect` 取真实语音区间（无声 → 有声），把每段字幕
**吸附**到最近的语音起点/终点（有上限位移，保证单调与最小时长）。
比 whisper 便宜（无需模型权重）、比字数比例准，且对纯配音轨稳定。

为什么不用 whisper 做唯一路径：整片 12 分钟转写在 CPU 上要几分钟到十几分钟，
且会改写文案（用户要的是"自己的文稿对上自己的声音"，不是被转写重写）。
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Iterable, Optional

from clipwright.config import logger


@dataclass
class SpeechBounds:
    speech: list[tuple[float, float]]
    silences: list[tuple[float, float]]


_SIL_START = re.compile(r"silence_start:\s*(-?[0-9.]+)")
_SIL_END = re.compile(r"silence_end:\s*(-?[0-9.]+)")


def detect_speech_bounds(
    audio_path: str,
    *,
    threshold_db: float = -30.0,
    min_silence: float = 0.30,
    duration: float | None = None,
) -> SpeechBounds:
    """用 silencedetect 求"说话区间"（有声段）。失败返回空（调用方保留原时间）。"""
    cmd = [
        "ffmpeg", "-v", "info", "-i", str(audio_path),
        "-af", f"silencedetect=noise={threshold_db}dB:d={min_silence}",
        "-f", "null", "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        text = proc.stderr or ""
    except Exception as exc:  # noqa: BLE001 — 探测失败不能影响出片
        logger.warning("语音边界探测失败 %s: %s", audio_path, exc)
        return SpeechBounds([], [])

    starts = [max(0.0, float(m)) for m in _SIL_START.findall(text)]
    ends = [float(m) for m in _SIL_END.findall(text)]
    total = float(duration or 0) or _probe_duration(audio_path)
    silences: list[tuple[float, float]] = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else total
        if e > s:
            silences.append((s, e))
    silences.sort()

    speech: list[tuple[float, float]] = []
    cursor = 0.0
    for s, e in silences:
        if s > cursor + 1e-3:
            speech.append((cursor, s))
        cursor = max(cursor, e)
    if total > cursor + 1e-3:
        speech.append((cursor, total))
    return SpeechBounds(speech, silences)


def _probe_duration(audio_path: str) -> float:
    try:
        import json

        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "json", str(audio_path)],
            capture_output=True, text=True, timeout=60,
        )
        return float(json.loads(proc.stdout)["format"]["duration"])
    except Exception:  # noqa: BLE001
        return 0.0


def snap_segments_to_speech(
    segments: Iterable[dict],
    bounds: SpeechBounds,
    *,
    max_shift: float = 1.6,
    min_dur: float = 0.35,
) -> tuple[list[dict], dict]:
    """把字幕段吸附到最近的语音起点/终点（有界位移 + 单调 + 最小时长）。

    返回 (新段列表, 统计)。统计里 `avg_shift` 用来让上层如实汇报"对齐改了多少"。
    """
    segs = [dict(s) for s in segments]
    if not segs or not bounds.speech:
        return segs, {"shifted": 0, "avg_shift": 0.0, "reason": "no_speech_bounds"}

    onsets = [s for s, _ in bounds.speech]
    offsets = [e for _, e in bounds.speech]

    def _nearest(value: float, pool: list[float]) -> Optional[float]:
        if not pool:
            return None
        best = min(pool, key=lambda x: abs(x - value))
        return best if abs(best - value) <= max_shift else None

    total_shift = 0.0
    shifted = 0
    prev_end = 0.0
    for idx, seg in enumerate(segs):
        start = float(seg.get("start_sec") or 0.0)
        end = float(seg.get("end_sec") or start + min_dur)
        next_start = (float(segs[idx + 1].get("start_sec") or 0.0)
                      if idx + 1 < len(segs) else None)
        new_start = _nearest(start, onsets)
        if new_start is not None:
            total_shift += abs(new_start - start)
            shifted += 1
            start = new_start
        # end 只在"本段范围内"微调：不允许跨过下一段的起点，也不允许把段拉长
        # （测试与真实场景都要求"段数/顺序/大致时长"不变，只挪边界）。
        new_end = _nearest(end, offsets)
        if new_end is not None and new_end <= end + max_shift:
            limit_hi = end + 0.3 if next_start is None else min(end + 0.3, next_start)
            limit_lo = start + min_dur
            if limit_hi > limit_lo:
                new_end = min(max(new_end, limit_lo), limit_hi)
                total_shift += abs(new_end - end)
                end = new_end
        start = max(start, prev_end)  # 单调，不重叠
        end = max(end, start + min_dur)
        if next_start is not None:
            end = min(end, max(next_start, start + min_dur))
        prev_end = end
        seg["start_sec"] = round(start, 3)
        seg["end_sec"] = round(end, 3)
    denom = max(1, shifted)
    return segs, {
        "shifted": shifted,
        "avg_shift": round(total_shift / denom, 3),
        "speech_units": len(bounds.speech),
    }


__all__ = ["SpeechBounds", "detect_speech_bounds", "snap_segments_to_speech"]
