"""文字转视频工具 — 从文本生成纯色背景 + 文字的视频片段。"""

from __future__ import annotations

import subprocess
from typing import Any, Optional

from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.tool.base import BaseTool
from clipwright.tool.video import _ensure_output_path


class GenerateTextVideoTool(BaseTool):
    """从文本生成视频（纯色背景 + 文字叠加）。"""
    name = "generate_text_video"
    description = "从文字生成视频片段：纯色背景上叠加文字，用于无素材时的占位"
    dependencies = ["ffmpeg"]

    async def execute(
        self,
        text: str,
        duration_sec: float = 5.0,
        font_size: int = 48,
        width: int = 1920,
        height: int = 1080,
        bg_color: str = "0x1a1a2e",
        font_color: str = "white",
        output_path: Optional[str] = None,
        **kwargs: Any,
    ) -> ToolExecResult:
        out = _ensure_output_path(output_path, "textvid_", ".mp4")
        try:
            escaped = text.replace(":", "\\:").replace("'", "'\\\\''").replace("\n", " ")
            result = subprocess.run(
                ["ffmpeg", "-y",
                 "-f", "lavfi", "-i", f"color=c={bg_color}:s={width}x{height}:d={duration_sec}",
                 "-vf", f"drawtext=text='{escaped}':fontsize={font_size}:fontcolor={font_color}:"
                        f"x=(w-text_w)/2:y=(h-text_h)/2",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p",
                 "-c:a", "aac" if duration_sec > 1 else "-an",
                 out],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                return ToolExecResult(
                    status=ToolStatus.ERROR, tool_name=self.name,
                    error=f"FFmpeg error: {result.stderr[:500]}",
                )
            return ToolExecResult(
                status=ToolStatus.SUCCESS, tool_name=self.name,
                output={"text": text[:80], "duration_sec": duration_sec, "output_path": out},
                output_path=out,
            )
        except FileNotFoundError:
            return ToolExecResult(status=ToolStatus.DEPENDENCY_MISSING, tool_name=self.name, error="ffmpeg not found")
