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


class ColorMatchTool(BaseTool):
    """跨片段色彩匹配（P8）— 以参考片段为基准自动匹配。

    用 ffmpeg signalstats 提取参考片段的平均亮度，对比目标片段的平均亮度，
    计算 eq brightness/gamma 偏移并应用到目标片段，使两者观感接近。
    """
    name = "color_match"
    description = "跨片段色彩匹配：以参考片段为基准自动匹配目标片段的亮度/对比度"
    dependencies = ["ffmpeg"]

    async def execute(
        self,
        input_path: str,
        reference_path: str,
        output_path: Optional[str] = None,
        strength: float = 1.0,
        **kwargs: Any,
    ) -> ToolExecResult:
        out = _ensure_output_path(output_path, "cm_", ".mp4")
        try:
            # 1. 提取参考片段平均亮度（YAVG）
            ref_probe = subprocess.run(
                ["ffmpeg", "-i", reference_path,
                 "-vf", "signalstats,metadata=print:key=lavfi.signalstats.YAVG",
                 "-frames:v", "30", "-f", "null", "-"],
                capture_output=True, text=True, timeout=120,
            )
            ref_yavg = _extract_yavg(ref_probe.stderr)
            # 2. 提取目标片段平均亮度
            tgt_probe = subprocess.run(
                ["ffmpeg", "-i", input_path,
                 "-vf", "signalstats,metadata=print:key=lavfi.signalstats.YAVG",
                 "-frames:v", "30", "-f", "null", "-"],
                capture_output=True, text=True, timeout=120,
            )
            tgt_yavg = _extract_yavg(tgt_probe.stderr)
            # 3. 计算偏移（0-1 亮度域；eq brightness 偏移域约 -1..1）
            delta = 0.0
            if ref_yavg is not None and tgt_yavg is not None:
                delta = (ref_yavg - tgt_yavg) * 1.2 * float(strength)
                delta = max(-0.5, min(0.5, delta))
            eq_filter = f"eq=brightness={delta:.4f}:contrast=1.0:saturation=1.0"
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
                        "reference_yavg": ref_yavg, "target_yavg": tgt_yavg,
                        "brightness_delta": round(delta, 4)},
                output_path=out,
            )
        except FileNotFoundError:
            return ToolExecResult(status=ToolStatus.DEPENDENCY_MISSING, tool_name=self.name, error="ffmpeg not found")


def _extract_yavg(stderr_text: str) -> float | None:
    """从 ffmpeg metadata print 输出中提取 YAVG（取最后一次出现的值）。"""
    import re
    vals = re.findall(r"lavfi\.signalstats\.YAVG=([\d.]+)", stderr_text)
    if not vals:
        return None
    return float(vals[-1])
