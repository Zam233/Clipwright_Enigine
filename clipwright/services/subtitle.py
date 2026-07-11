"""字幕服务 — SRT/ASS 格式的导入与导出。"""

from __future__ import annotations

import re
from typing import Any


class SubtitleSegment:
    """一个字幕片段。"""
    def __init__(self, index: int, start_sec: float, end_sec: float, text: str):
        self.index = index
        self.start_sec = start_sec
        self.end_sec = end_sec
        self.text = text


# ── SRT 解析 ──

def parse_srt(content: str) -> list[SubtitleSegment]:
    """将 SRT 格式文本解析为字幕片段列表。"""
    segments: list[SubtitleSegment] = []
    blocks = re.split(r'\n\s*\n', content.strip())
    for block in blocks:
        lines = block.strip().split('\n')
        if len(lines) < 3:
            continue
        try:
            index = int(lines[0].strip())
        except ValueError:
            continue
        time_match = re.match(
            r'(\d+):(\d+):(\d+)[.,](\d+)\s*-->\s*(\d+):(\d+):(\d+)[.,](\d+)',
            lines[1],
        )
        if not time_match:
            continue
        start = _srt_time(*time_match.group(1, 2, 3, 4))
        end = _srt_time(*time_match.group(5, 6, 7, 8))
        text = '\n'.join(lines[2:]).strip()
        segments.append(SubtitleSegment(index, start, end, text))
    return segments


def to_srt(segments: list[SubtitleSegment]) -> str:
    """将字幕片段列表导出为 SRT 格式。"""
    lines: list[str] = []
    for i, seg in enumerate(segments, 1):
        start = _to_srt_time(seg.start_sec)
        end = _to_srt_time(seg.end_sec)
        lines.append(str(i))
        lines.append(f"{start} --> {end}")
        lines.append(seg.text)
        lines.append("")
    return "\n".join(lines)


def segments_to_timeline_clips(
    segments: list[SubtitleSegment],
    track_id: str = "caption_track",
) -> list[dict[str, Any]]:
    """将字幕片段转为 Timeline clip 格式。"""
    clips: list[dict[str, Any]] = []
    for i, seg in enumerate(segments):
        clips.append({
            "id": f"cap_{i}",
            "kind": "caption",
            "asset_id": "",
            "track_id": track_id,
            "start_sec": seg.start_sec,
            "duration_sec": round(seg.end_sec - seg.start_sec, 2),
            "text": seg.text,
            "font": "sans-serif",
            "font_size": 36,
            "font_color": "#ffffff",
        })
    return clips


def timeline_clips_to_segments(clips: list[dict[str, Any]]) -> list[SubtitleSegment]:
    """将 Timeline caption clip 列表导出为字幕片段。"""
    segments: list[SubtitleSegment] = []
    for i, clip in enumerate(clips):
        if clip.get("kind") not in ("caption", "text"):
            continue
        text = clip.get("text", "")
        if not text:
            continue
        start = clip.get("start_sec", 0)
        dur = clip.get("duration_sec", 2)
        segments.append(SubtitleSegment(i + 1, start, start + dur, text))
    return segments


def _srt_time(h: str, m: str, s: str, ms: str) -> float:
    """SRT 时间 → 秒。"""
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / (1000 if len(ms) == 3 else 10 ** len(ms))


def _to_srt_time(sec: float) -> str:
    """秒 → SRT 时间格式。"""
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int(round((sec % 1) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
