"""EDL / FCPXML 导入导出 — 与其他剪辑软件互操作。"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
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


_EDL_TRANS_MAP = {
    # 常见 xfade 名 → 合法 EDL 转场码（其余未知名降级 C 硬切）
    "dissolve": "D", "crossfade": "D", "fade": "C", "wipeleft": "W", "wiperight": "W",
}


def _edl_reel_and_name(asset_id: str, title: str = "") -> tuple[str, str]:
    """资产 → (reel, clip name)。reel 为清洗后 ≤8 字符大写标识（非路径片段）。"""
    name = (title or "").strip() or Path(str(asset_id or "")).stem or "CLIP"
    reel_src = Path(str(asset_id or "")).stem or name
    reel = re.sub(r"[^A-Za-z0-9]", "", reel_src)[:8].upper() or "AX"
    return reel, name


def to_edl(clips: list[dict[str, Any]], fps: float = 30.0) -> str:
    """将 Timeline clip 列表导出为 EDL 格式。

    批6.4 修复：事件编号连续（跳过类型不再占号）；reel 为清洗资产标识
    （旧实现取本地路径前 8 字符，NLE 无法 relink）；FROM CLIP NAME 为
    可读资产名；转场名映射合法 EDL 码（未知降级 C）。
    """
    lines: list[str] = ["TITLE: ClipWright Export", "FCM: NON-DROP FRAME", ""]
    event_no = 0
    for clip in clips:
        if clip.get("kind") not in ("video", "image"):
            continue
        event_no += 1
        dur = clip.get("duration_sec", 5)
        start = clip.get("start_sec", 0)
        src_off = clip.get("source_offset_sec", 0)
        reel, name = _edl_reel_and_name(
            clip.get("asset_id", ""), str(clip.get("title", "") or ""))
        raw_trans = str(clip.get("transition_in") or "C")
        trans = _EDL_TRANS_MAP.get(raw_trans.lower(), "C")

        src_s = _to_edl_time(src_off, fps)
        src_e = _to_edl_time(src_off + dur, fps)
        dest_s = _to_edl_time(start, fps)
        dest_e = _to_edl_time(start + dur, fps)

        lines.append(f"{event_no:03d}  {reel:8s} V     {trans}        {src_s} {src_e} {dest_s} {dest_e}")
        lines.append(f"* FROM CLIP NAME: {name}")
        lines.append("")
    return "\n".join(lines)


# ── FCPXML (FCP 7 XML) ────────────────────────────────

def parse_fcpxml(content: str) -> list[dict[str, Any]]:
    """解析 FCPXML 为 Timeline clip 列表。"""
    clips: list[dict[str, Any]] = []
    # 安全：拒绝带 DTD/实体的 XML——标准库 ElementTree 会展开内部实体，
    # 恶意文件可借实体扩张耗尽内存（FCPXML 正常文件无需 DTD）
    # 批6.4：仅拒绝内部实体声明（实体扩张攻击向量）；标准 FCPXML 自带
    # <!DOCTYPE xmeml> 声明，属正常内容不应拒绝
    head = content[:4096].lstrip()
    if "<!ENTITY" in head:
        raise ValueError("不支持包含实体的 XML（疑似实体扩张攻击）")
    try:
        root = ET.fromstring(content)
        fps_default = 30.0
        rate_el = root.find(".//rate/timebase")
        if rate_el is not None and rate_el.text:
            try:
                fps_default = float(rate_el.text)
            except ValueError:
                pass
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

            # 批6.4：保留时间位置（旧实现恒 0，导入丢失时间轴布局）
            start_el = item.find("start")
            if start_el is not None and start_el.text:
                try:
                    clip["start_sec"] = round(float(start_el.text) / fps_default, 2)
                except ValueError:
                    clip["start_sec"] = 0
            end_el = item.find("end")
            if end_el is not None and end_el.text:
                try:
                    end_sec = float(end_el.text) / fps_default
                    clip["duration_sec"] = round(max(0.1, end_sec - clip.get("start_sec", 0)), 2)
                except ValueError:
                    pass

            clips.append(clip)
    except ET.ParseError:
        pass
    return clips


def _xml_escape(text: str) -> str:
    """XML 文本转义（& < >）——资产名/路径含特殊字符时不再破坏文档结构。"""
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _pathurl(asset_id: str) -> str:
    """资产 → pathurl：本地绝对路径转规范 file URI（旧实现 file://J:\\... 为
    非法 URI，NLE 无法解析），URL 原样保留。"""
    raw = str(asset_id or "")
    if raw.startswith(("http://", "https://", "file://")):
        return raw
    try:
        return Path(raw).resolve().as_uri()
    except Exception:
        return "file://localhost/" + raw.lstrip("/")


def to_fcpxml(clips: list[dict[str, Any]], timeline: dict | None = None) -> str:
    """将 Timeline 导出为 FCPXML。"""
    tl = timeline or {}
    width = tl.get("width", 1920)
    height = tl.get("height", 1080)
    fps = tl.get("fps", 30)
    duration_frames = int((tl.get("duration_sec", 60) * fps))
    ntsc = "TRUE" if abs(fps - 29.97) < 0.01 else "FALSE"  # 批6.4：非 29.97 不再误标 NTSC

    xml_parts: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<!DOCTYPE xmeml>',
        f'<xmeml version="4">',
        f'  <sequence>',
        f'    <name>ClipWright Export</name>',
        f'    <duration>{duration_frames}</duration>',
        f'    <rate><timebase>{int(fps)}</timebase><ntsc>{ntsc}</ntsc></rate>',
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
    video_clips = [c for c in clips if c.get("kind") in ("video", "image")]
    text_clips = [c for c in clips if c.get("kind") in ("caption", "text")]
    for n, clip in enumerate(video_clips, 1):
        dur = int(clip.get("duration_sec", 5) * fps)
        start = int(clip.get("start_sec", 0) * fps)
        asset = _xml_escape(clip.get("asset_id", "") or "Unknown")
        xml_parts.extend([
            f'          <clipitem id="clipitem_{n}">',
            f'            <name>{_xml_escape(clip.get("title", "") or clip.get("asset_id", "Unknown"))}</name>',
            f'            <duration>{dur}</duration>',
            f'            <rate><timebase>{int(fps)}</timebase></rate>',
            f'            <start>{start}</start>',
            f'            <end>{start + dur}</end>',
            f'            <in>{int(clip.get("source_offset_sec", 0) * fps)}</in>',
            f'            <out>{int(clip.get("source_offset_sec", 0) * fps) + dur}</out>',
            '            <file>',
            f'              <name>{asset}</name>',
            f'              <pathurl>{_xml_escape(_pathurl(clip.get("asset_id", "")))}</pathurl>',
            '            </file>',
            '          </clipitem>',
        ])
    # 批6.4：字幕/文字轨导出（文本入 comments，NLE 可参考；此前整轨静默丢失）
    if text_clips:
        xml_parts.extend(['      <audio>', '        <track>'])
        for n, clip in enumerate(text_clips, 1):
            dur = int(clip.get("duration_sec", 2) * fps)
            start = int(clip.get("start_sec", 0) * fps)
            xml_parts.extend([
                f'          <clipitem id="captionitem_{n}">',
                f'            <name>{_xml_escape((clip.get("text", "") or "caption")[:60])}</name>',
                f'            <duration>{dur}</duration>',
                f'            <rate><timebase>{int(fps)}</timebase></rate>',
                f'            <start>{start}</start>',
                f'            <end>{start + dur}</end>',
                '            <file>',
                f'              <name>caption_{n}</name>',
                '            </file>',
                f'            <comments>{_xml_escape(clip.get("text", "") or "")}</comments>',
                '          </clipitem>',
            ])
        xml_parts.extend(['        </track>', '      </audio>'])
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
