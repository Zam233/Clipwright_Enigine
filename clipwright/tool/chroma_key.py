"""Chroma Key 工具 — 绿幕/蓝幕抠像。"""

from __future__ import annotations

import subprocess
from typing import Any, Optional

from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.tool.base import BaseTool
from clipwright.tool.video import _ensure_output_path


class ChromaKeyTool(BaseTool):
    """Chroma Key 抠像工具（绿幕/蓝幕）。"""
    name = "chroma_key"
    description = "绿幕/蓝幕抠像：color=0x00FF00 绿色, similarity=0.1-0.5, blend=0.0-0.1"
    dependencies = ["ffmpeg"]

    async def execute(
        self,
        input_path: str,
        color: str = "0x00FF00",
        similarity: float = 0.2,
        blend: float = 0.05,
        output_path: Optional[str] = None,
        **kwargs: Any,
    ) -> ToolExecResult:
        out = _ensure_output_path(output_path, "key_", ".mp4")
        try:
            result = subprocess.run(
                ["ffmpeg", "-y", "-i", input_path,
                 "-vf", f"colorkey={color}:{similarity}:{blend}",
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
                output={"color": color, "similarity": similarity, "output_path": out},
                output_path=out,
            )
        except FileNotFoundError:
            return ToolExecResult(status=ToolStatus.DEPENDENCY_MISSING, tool_name=self.name, error="ffmpeg not found")
