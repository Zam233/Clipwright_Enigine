"""EDL / FCPXML 导入导出 — 与其他剪辑软件互操作。"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any


# ── EDL (Edit Decision List) ──────────────────────────

class EDLSegment:
    """EDL 中的一个片段。"""
    def __init__(self, reel: str, src_start: float, src_end: float,
                 dest_start: float, dest_end: float, transition: str = "C"):
        self.reel = reel
        self.src_start = src_start
        self.src_end = src_end
        self.dest_start = dest_start
        self.dest_end = dest_end
        self.duration = dest_end - dest_start
        self.transition = transition


def parse_edl(content: str) -> list[dict[str, Any]]:
    """解析 EDL 格式为 Timeline clip 列表。"""
    clips: list[dict[str, Any]] = []
    # EDL format:
    # 001  AX       V     C        00:00:00:00 00:00:10:00 00:00:00:00 00:00:10:00
    # * FROM CLIP NAME: example.mp4
    pattern = re.compile(
        r"(\d{3})\s+(\S+)\s+\S+\s+(\S+)\s+"
        r"(\d{2}:\d{2}:\d{2}[:;]\d{2})\s+(\d{2}:\d{2}:\d{2}[:;]\d{2})\s+"
        r"(\d{2}:\d{2}:\d{2}[:;]\d{2})\s+(\d{2}:\d{2}:\d{2}[:;]\d{2})"
    )
    lines = content.split("\n")
    for i, line in enumerate(lines):
        m = pattern.match(line)
        if not m:
            continue
        reel = m.group(2)
        transition = m.group(3)
        src_start = _edl_time(m.group(4))
        src_end = _edl_time(m.group(5))
        dest_start = _edl_time(m.group(6))
        dest_end = _edl_time(m.group(7))

        # Check next line for clip name
        clip_name = reel
        if i + 1 < len(lines) and lines[i + 1].startswith("* FROM CLIP NAME:"):
            clip_name = lines[i + 1].split(":", 1)[1].strip()

        clips.append({
            "id": f"edl_{len(clips)}",
            "kind": "video",
            "asset_id": clip_name,
            "track_id": "v1",
            "start_sec": round(dest_start, 2),
            "duration_sec": round(dest_end - dest_start, 2),
            "source_offset_sec": round(src_start, 2),
            "transition_in": transition if transition != "C" else None,
        })
    return clips


def to_edl(clips: list[dict[str, Any]], fps: float = 30.0) -> str:
    """将 Timeline clip 列表导出为 EDL 格式。"""
    lines: list[str] = ["TITLE: ClipWright Export", "FCM: NON-DROP FRAME", ""]
    for i, clip in enumerate(clips, 1):
        if clip.get("kind") not in ("video", "image"):
            continue
        dur = clip.get("duration_sec", 5)
        start = clip.get("start_sec", 0)
        src_off = clip.get("source_offset_sec", 0)
        reel = clip.get("asset_id", "AX")[:8]
        trans = clip.get("transition_in", "C") or "C"

        src_s = _to_edl_time(src_off, fps)
        src_e = _to_edl_time(src_off + dur, fps)
        dest_s = _to_edl_time(start, fps)
        dest_e = _to_edl_time(start + dur, fps)

        lines.append(f"{i:03d}  {reel:8s} V     {trans}        {src_s} {src_e} {dest_s} {dest_e}")
        lines.append(f"* FROM CLIP NAME: {reel}")
        lines.append("")
    return "\n".join(lines)


# ── FCPXML (FCP 7 XML) ────────────────────────────────

def parse_fcpxml(content: str) -> list[dict[str, Any]]:
    """解析 FCPXML 为 Timeline clip 列表。"""
    clips: list[dict[str, Any]] = []
    try:
        root = ET.fromstring(content)
        ns = {"fcpxml": "http://www.apple.com/FCPXML/2007/"}
        # FCP 7 XML uses <clipitem> elements
        for item in root.iter("clipitem"):
            clip = {"id": f"fcpxml_{len(clips)}", "kind": "video", "track_id": "v1"}

            name_el = item.find("name")
            if name_el is not None:
                clip["asset_id"] = name_el.text or ""

            file_el = item.find("file")
            if file_el is not None:
                pathurl = file_el.get("pathurl", "")
                if pathurl:
                    clip["asset_id"] = pathurl.replace("file://", "")

            # 时长解析
            dur_el = item.find("duration")
            if dur_el is not None:
                try:
                    clip["duration_sec"] = float(dur_el.text) / 30000 * 29.97 / 30.0 if dur_el.text else 5
                except ValueError:
                    clip["duration_sec"] = 5

            # 入点/出点
            start_el = item.find("start")
            if start_el is not None:
                clip["start_sec"] = 0  # 简化处理

            clips.append(clip)
    except ET.ParseError:
        pass
    return clips


def to_fcpxml(clips: list[dict[str, Any]], timeline: dict | None = None) -> str:
    """将 Timeline 导出为 FCPXML。"""
    tl = timeline or {}
    width = tl.get("width", 1920)
    height = tl.get("height", 1080)
    fps = tl.get("fps", 30)
    duration_frames = int((tl.get("duration_sec", 60) * fps))

    xml_parts: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<!DOCTYPE xmeml>',
        f'<xmeml version="4">',
        f'  <sequence>',
        f'    <name>ClipWright Export</name>',
        f'    <duration>{duration_frames}</duration>',
        f'    <rate><timebase>{int(fps)}</timebase><ntsc>TRUE</ntsc></rate>',
        f'    <media>',
        f'      <video>',
        f'        <format>',
        f'          <samplecharacteristics>',
        f'            <width>{width}</width>',
        f'            <height>{height}</height>',
        f'            <rate><timebase>{int(fps)}</timebase></rate>',
        f'          </samplecharacteristics>',
        f'        </format>',
        f'        <track>',
    ]
    for clip in clips:
        if clip.get("kind") not in ("video", "image"):
            continue
        dur = int(clip.get("duration_sec", 5) * fps)
        start = int(clip.get("start_sec", 0) * fps)
        xml_parts.extend([
            f'          <clipitem id="clip_{clip.get("id", "0")}">',
            f'            <name>{clip.get("asset_id", "Unknown")}</name>',
            f'            <duration>{dur}</duration>',
            f'            <rate><timebase>{int(fps)}</timebase></rate>',
            f'            <start>{start}</start>',
            f'            <end>{start + dur}</end>',
            f'            <file>',
            f'              <name>{clip.get("asset_id", "Unknown")}</name>',
            f'              <pathurl>file://{clip.get("asset_id", "")}</pathurl>',
            f'            </file>',
            f'          </clipitem>',
        ])
    xml_parts.extend([
        f'        </track>',
        f'      </video>',
        f'    </media>',
        f'  </sequence>',
        f'</xmeml>',
    ])
    return "\n".join(xml_parts)


def timeline_clips_to_edl_segments(clips: list[dict]) -> list[EDLSegment]:
    """Timeline clips → EDLSegment 列表。"""
    segs: list[EDLSegment] = []
    for clip in clips:
        if clip.get("kind") not in ("video", "image"):
            continue
        dur = clip.get("duration_sec", 5)
        start = clip.get("start_sec", 0)
        src_off = clip.get("source_offset_sec", 0)
        segs.append(EDLSegment(
            reel=clip.get("asset_id", "AX")[:8],
            src_start=src_off,
            src_end=src_off + dur,
            dest_start=start,
            dest_end=start + dur,
            transition=clip.get("transition_in", "C") or "C",
        ))
    return segs


def _edl_time(t: str) -> float:
    """EDL 时间 → 秒。"""
    parts = re.split(r"[:;]", t)
    if len(parts) == 4:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2]) + int(parts[3]) / 30
    return 0


def _to_edl_time(sec: float, fps: float = 30) -> str:
    """秒 → EDL 时间格式。"""
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    f = int(round((sec % 1) * fps))
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"
