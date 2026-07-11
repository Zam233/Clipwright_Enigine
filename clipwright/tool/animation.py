"""动画生成工具 — FFmpeg drawtext 封装（占位）。

当前阶段：使用 FFmpeg drawtext filter 做基础文字动画。
完整 Manim/Motion Canvas 集成推迟到 Phase 2。
"""

from __future__ import annotations

from typing import Any, Optional

from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.tool.base import BaseTool
from clipwright.tool.video import _ensure_output_path, _ffmpeg


class TypewriterAnimationTool(BaseTool):
    """打字机文字动画工具（FFmpeg drawtext 单帧文字叠加）。"""
    name = "typewriter_animation"
    description = "在视频上叠加打字机效果的文字动画"
    dependencies = ["ffmpeg"]

    async def execute(
        self,
        text: str,
        input_path: Optional[str] = None,
        output_path: Optional[str] = None,
        font_size: int = 48,
        color: str = "#ffffff",
        **kwargs: Any,
    ) -> ToolExecResult:
        """在没有输入视频时生成纯色背景 + 文字视频。"""
        out = _ensure_output_path(output_path, "typewriter_", ".mp4")

        try:
            if input_path and input_path.strip():
                # 在已有视频上叠加文字
                result = _ffmpeg(
                    "-i", input_path,
                    "-vf", f"drawtext=text='{text}':fontsize={font_size}:fontcolor={color}:x=(w-text_w)/2:y=(h-text_h)/2",
                    "-c:a", "copy", out,
                )
            else:
                # 生成纯色背景 + 文字的短视频（5 秒）
                escaped = text.replace(":", "\\:").replace("'", "'\\\\''")
                result = _ffmpeg(
                    "-f", "lavfi", "-i", f"color=c=#1a1a1a:s=1920x1080:d=5",
                    "-vf", f"drawtext=text='{escaped}':fontsize={font_size}:fontcolor={color}:x=(w-text_w)/2:y=(h-text_h)/2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", out,
                )
            if result.returncode != 0:
                return ToolExecResult(
                    status=ToolStatus.ERROR,
                    tool_name=self.name,
                    error=f"ffmpeg error: {result.stderr[:500]}",
                )
            return ToolExecResult(
                status=ToolStatus.SUCCESS,
                tool_name=self.name,
                output={"text": text, "output_path": out},
                output_path=out,
            )
        except FileNotFoundError:
            return ToolExecResult(
                status=ToolStatus.DEPENDENCY_MISSING,
                tool_name=self.name,
                error="ffmpeg not found",
            )


class TrackingTextTool(BaseTool):
    """文字追踪/标题动画工具。

    Phase 1 占位。真实实现需要 Manim 或 Motion Canvas。
    """
    name = "tracking_text"
    description = "生成文字追踪/标题动画（Phase 1 占位）"

    async def execute(
        self,
        text: str,
        output_path: Optional[str] = None,
        **kwargs: Any,
    ) -> ToolExecResult:
        return ToolExecResult(
            status=ToolStatus.SUCCESS,
            tool_name=self.name,
            output={"text": text, "note": "placeholder — Manim not yet integrated"},
            warning="Tracking text requires Manim — not yet integrated",
        )
