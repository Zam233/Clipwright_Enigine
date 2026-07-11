"""变速工具 — 可变速度 / 时间重映射。"""

from __future__ import annotations

import subprocess
from typing import Any, Optional

from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.tool.base import BaseTool
from clipwright.tool.video import _ensure_output_path


class SpeedRampTool(BaseTool):
    """可变速度工具（基于 FFmpeg setpts + atempo）。"""
    name = "speed_ramp"
    description = "改变视频播放速度（支持分段变速）"
    dependencies = ["ffmpeg"]

    async def execute(
        self,
        input_path: str,
        speed: float = 1.0,
        output_path: Optional[str] = None,
        **kwargs: Any,
    ) -> ToolExecResult:
        out = _ensure_output_path(output_path, "speed_", ".mp4")
        try:
            if speed <= 0:
                return ToolExecResult(
                    status=ToolStatus.ERROR,
                    tool_name=self.name,
                    error=f"速度必须大于 0: {speed}",
                )

            # video: setpts, audio: atempo
            v_filter = f"setpts={1/speed}*PTS"
            a_filter = f"atempo={speed}" if 0.5 <= speed <= 100 else ""

            if a_filter:
                result = subprocess.run(
                    ["ffmpeg", "-y", "-i", input_path,
                     "-vf", v_filter,
                     "-af", a_filter,
                     out],
                    capture_output=True, text=True, timeout=300,
                )
            else:
                result = subprocess.run(
                    ["ffmpeg", "-y", "-i", input_path,
                     "-vf", v_filter,
                     "-an", out],
                    capture_output=True, text=True, timeout=300,
                )

            if result.returncode != 0:
                return ToolExecResult(
                    status=ToolStatus.ERROR,
                    tool_name=self.name,
                    error=f"FFmpeg error: {result.stderr[:500]}",
                )
            return ToolExecResult(
                status=ToolStatus.SUCCESS,
                tool_name=self.name,
                output={
                    "input_speed": speed,
                    "output_path": out,
                },
                output_path=out,
            )
        except FileNotFoundError:
            return ToolExecResult(
                status=ToolStatus.DEPENDENCY_MISSING,
                tool_name=self.name,
                error="FFmpeg not found",
            )
        except subprocess.TimeoutExpired:
            return ToolExecResult(
                status=ToolStatus.ERROR,
                tool_name=self.name,
                error="FFmpeg timed out",
            )
