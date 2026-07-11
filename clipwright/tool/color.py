"""调色工具 — 色彩校正 + LUT 应用。"""

from __future__ import annotations

import subprocess
from typing import Any, Optional

from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.tool.base import BaseTool
from clipwright.tool.video import _ensure_output_path


class ColorCorrectTool(BaseTool):
    """色彩校正工具（亮度/对比度/饱和度/色相）。"""
    name = "color_correct"
    description = "调整视频色彩：亮度(brightness)、对比度(contrast)、饱和度(saturation)、色相(hue)"
    dependencies = ["ffmpeg"]

    async def execute(
        self,
        input_path: str,
        brightness: float = 0.0,
        contrast: float = 1.0,
        saturation: float = 1.0,
        gamma: float = 1.0,
        hue: float = 0.0,
        output_path: Optional[str] = None,
        **kwargs: Any,
    ) -> ToolExecResult:
        out = _ensure_output_path(output_path, "cc_", ".mp4")
        try:
            # FFmpeg eq filter: brightness, contrast, saturation, gamma, hue
            eq_filter = (
                f"eq=brightness={brightness}:contrast={contrast}"
                f":saturation={saturation}:gamma={gamma}:hue={hue}"
            )
            result = subprocess.run(
                ["ffmpeg", "-y", "-i", input_path,
                 "-vf", eq_filter,
                 "-c:a", "copy", out],
                capture_output=True, text=True, timeout=300,
            )
            if result.returncode != 0:
                return ToolExecResult(
                    status=ToolStatus.ERROR, tool_name=self.name,
                    error=f"FFmpeg error: {result.stderr[:500]}",
                )
            return ToolExecResult(
                status=ToolStatus.SUCCESS, tool_name=self.name,
                output={"input_path": input_path, "output_path": out,
                        "brightness": brightness, "contrast": contrast,
                        "saturation": saturation},
                output_path=out,
            )
        except FileNotFoundError:
            return ToolExecResult(status=ToolStatus.DEPENDENCY_MISSING, tool_name=self.name, error="ffmpeg not found")


class LutApplyTool(BaseTool):
    """LUT 应用工具（加载 .cube 文件）。"""
    name = "lut_apply"
    description = "应用 LUT (.cube) 文件到视频"
    dependencies = ["ffmpeg"]

    async def execute(
        self,
        input_path: str,
        lut_path: str,
        output_path: Optional[str] = None,
        **kwargs: Any,
    ) -> ToolExecResult:
        out = _ensure_output_path(output_path, "lut_", ".mp4")
        try:
            result = subprocess.run(
                ["ffmpeg", "-y", "-i", input_path,
                 "-vf", f"lut3d={lut_path}",
                 "-c:a", "copy", out],
                capture_output=True, text=True, timeout=300,
            )
            if result.returncode != 0:
                return ToolExecResult(
                    status=ToolStatus.ERROR, tool_name=self.name,
                    error=f"FFmpeg error: {result.stderr[:500]}",
                )
            return ToolExecResult(
                status=ToolStatus.SUCCESS, tool_name=self.name,
                output={"input_path": input_path, "lut_path": lut_path, "output_path": out},
                output_path=out,
            )
        except FileNotFoundError:
            return ToolExecResult(status=ToolStatus.DEPENDENCY_MISSING, tool_name=self.name, error="ffmpeg not found")
