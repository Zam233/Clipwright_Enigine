"""视频稳定工具 — FFmpeg vidstab。"""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.tool.base import BaseTool
from clipwright.tool.video import _ensure_output_path
from clipwright.tool.video import resolve_ffmpeg, resolve_ffprobe


class VideoStabilizeTool(BaseTool):
    """视频稳定工具（防抖）。"""
    name = "video_stabilize"
    description = "视频防抖稳定（基于 FFmpeg vidstab）"
    dependencies = ["ffmpeg"]

    async def execute(
        self,
        input_path: str,
        smoothing: int = 10,
        max_shift: int = 90,
        output_path: Optional[str] = None,
        **kwargs: Any,
    ) -> ToolExecResult:
        out = _ensure_output_path(output_path, "stab_", ".mp4")
        transforms = _ensure_output_path(None, "transforms_", ".trf")
        try:
            # Phase 1: 分析运动
            detect = await asyncio.to_thread(subprocess.run,
                [resolve_ffmpeg(), "-y", "-i", input_path,
                 "-vf", f"vidstabdetect=shakiness={smoothing}:accuracy=15:result={transforms}",
                 "-f", "null", "-"],
                capture_output=True, text=True, timeout=300,
            )
            if detect.returncode != 0:
                return ToolExecResult(
                    status=ToolStatus.ERROR, tool_name=self.name,
                    error=f"Stabilization detection failed: {detect.stderr[:300]}",
                )

            # Phase 2: 应用稳定
            result = await asyncio.to_thread(subprocess.run,
                [resolve_ffmpeg(), "-y", "-i", input_path,
                 "-vf", f"vidstabtransform=input={transforms}:maxshift={max_shift}:crop=black",
                 "-c:a", "copy", out],
                capture_output=True, text=True, timeout=300,
            )
            # 清理临时 transforms 文件
            Path(transforms).unlink(missing_ok=True)

            if result.returncode != 0:
                return ToolExecResult(
                    status=ToolStatus.ERROR, tool_name=self.name,
                    error=f"FFmpeg error: {result.stderr[:500]}",
                )
            return ToolExecResult(
                status=ToolStatus.SUCCESS, tool_name=self.name,
                output={"smoothing": smoothing, "output_path": out},
                output_path=out,
            )
        except FileNotFoundError:
            return ToolExecResult(status=ToolStatus.DEPENDENCY_MISSING, tool_name=self.name, error="ffmpeg not found")
        except subprocess.TimeoutExpired:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error="ffmpeg timed out")
