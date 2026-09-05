"""质检工具 — 自动检测视频质量问题。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Optional

from clipwright.config import logger
from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.tool.base import BaseTool


class BlackFrameDetectTool(BaseTool):
    """黑帧/全白帧检测 — 检查视频中是否有过暗或过亮的帧。"""
    name = "black_frame_detect"
    description = "检测视频中的黑帧/全白帧，返回异常帧的时间位置"
    dependencies = ["ffmpeg", "ffprobe"]

    async def execute(
        self,
        video_path: str,
        black_threshold: float = 0.1,
        pixel_threshold: float = 0.15,
        **kwargs: Any,
    ) -> ToolExecResult:
        if not Path(video_path).exists():
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error="file not found")
        try:
            result = subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", video_path,
                 "-vf", f"blackdetect=d=0.5:pic_th={black_threshold}:pix_th={pixel_threshold}",
                 "-f", "null", "-"],
                capture_output=True, text=True, timeout=120,
            )
            import re
            starts = re.findall(r"black_duration:([\d.]+)", result.stderr or "")
            black_segments = [float(d) for d in starts]

            # 检测全白（过曝）— T2: signalstats YAVG 替代全帧 ffprobe
            probe = subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", video_path,
                 "-vf", "signalstats,metadata=print:key=lavfi.signalstats.YAVG",
                 "-f", "null", "-"],
                capture_output=True, text=True, timeout=120,
            )
            white_segments = []
            try:
                import re as _re
                pat = _re.compile(r"pts_time:([\d.]+)\s*\n?.*?YAVG=([\d.]+)")
                for t, yavg in pat.findall(probe.stderr or ""):
                    if float(yavg) > 250:
                        white_segments.append(float(t))
            except Exception:
                pass

            issues = []
            if black_segments:
                issues.append({"type": "black_frame", "count": len(black_segments), "durations": black_segments[:10]})
            if white_segments:
                issues.append({"type": "overexposed", "count": len(white_segments), "positions": white_segments[:10]})

            return ToolExecResult(
                status=ToolStatus.SUCCESS,
                tool_name=self.name,
                output={"issues": issues, "total_black": len(black_segments), "passed": len(issues) == 0},
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error=str(e))


class AudioSilenceDetectTool(BaseTool):
    """静音检测 — 检查音频中是否有过长的静音段。"""
    name = "audio_silence_detect"
    description = "检测音频中的静音/爆音段"
    dependencies = ["ffmpeg"]

    async def execute(
        self,
        audio_path: str,
        silence_threshold: float = -35.0,
        min_duration: float = 0.5,
        **kwargs: Any,
    ) -> ToolExecResult:
        if not Path(audio_path).exists():
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error="file not found")
        try:
            result = subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", audio_path,
                 "-af", f"silencedetect=n={silence_threshold}dB:d={min_duration}",
                 "-f", "null", "-"],
                capture_output=True, text=True, timeout=120,
            )
            import re
            starts = [float(s) for s in re.findall(r"silence_start: ([\d.]+)", result.stderr or "")]
            ends = [float(s) for s in re.findall(r"silence_end: ([\d.]+)", result.stderr or "")]
            durations = [float(s) for s in re.findall(r"silence_duration: ([\d.]+)", result.stderr or "")]
            silences = [{"start": s, "end": e, "duration": d} for s, e, d in zip(starts, ends, durations)]

            return ToolExecResult(
                status=ToolStatus.SUCCESS,
                tool_name=self.name,
                output={
                    "silences": silences[:20],
                    "total_silences": len(silences),
                    "total_silence_sec": round(sum(durations), 1),
                    "note": f"检测到 {len(silences)} 段静音，共 {round(sum(durations), 1)}s",
                },
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error=str(e))


class SubtitleOverflowCheckTool(BaseTool):
    """字幕溢出检查 — 检查字幕文本是否超出画面宽度。"""
    name = "subtitle_overflow"
    description = "检查字幕文本是否超出视频画面宽度"
    dependencies = []

    async def execute(
        self,
        subtitles: list[dict[str, Any]],
        max_chars_per_line: int = 20,
        **kwargs: Any,
    ) -> ToolExecResult:
        issues = []
        for sub in subtitles:
            text = sub.get("text", "") or ""
            if len(text) > max_chars_per_line:
                issues.append({
                    "text": text[:40],
                    "length": len(text),
                    "max_chars": max_chars_per_line,
                    "position": sub.get("start_sec", 0),
                })
        return ToolExecResult(
            status=ToolStatus.SUCCESS,
            tool_name=self.name,
            output={
                "issues": issues[:20],
                "total_issues": len(issues),
                "passed": len(issues) == 0,
                "note": f"{len(issues)} 段字幕可能溢出" if issues else "字幕长度正常",
            },
        )
