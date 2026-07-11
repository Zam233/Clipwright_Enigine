"""视频处理工具 — FFmpeg 封装。

设计约束：所有 API 的入参必须是纯数值或纯路径。
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.tool.base import BaseTool


def _ensure_output_path(suggested: Optional[str], prefix: str, ext: str) -> str:
    """生成输出路径（建议路径不存在时自动创建）。"""
    if suggested:
        p = Path(suggested)
        p.parent.mkdir(parents=True, exist_ok=True)
        return str(p)
    tmp = tempfile.NamedTemporaryFile(prefix=prefix, suffix=ext, delete=False)
    tmp.close()
    return tmp.name


def _ffmpeg(*args: str, timeout: int = 300) -> subprocess.CompletedProcess:
    """调用 ffmpeg，CommandNotFound 时抛出 FileNotFoundError。"""
    return subprocess.run(
        ["ffmpeg", "-y", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _check_ffmpeg() -> Optional[str]:
    """检测 ffmpeg 是否可用。"""
    if os.name == "nt":
        return "nt"
    return None


class VideoTrimTool(BaseTool):
    """视频裁剪工具。"""
    name = "video_trim"
    description = "裁剪视频片段（支持 start+duration 或 start+end）"
    dependencies = ["ffmpeg"]

    async def execute(
        self,
        input_path: str,
        start_sec: float = 0,
        duration_sec: Optional[float] = None,
        end_sec: Optional[float] = None,
        output_path: Optional[str] = None,
        **kwargs: Any,
    ) -> ToolExecResult:
        out = _ensure_output_path(output_path, "trim_", ".mp4")
        try:
            args = ["-ss", str(start_sec), "-i", input_path]
            if duration_sec is not None:
                args.extend(["-t", str(duration_sec)])
            elif end_sec is not None:
                args.extend(["-to", str(end_sec)])
            args.extend(["-c", "copy", out])
            result = _ffmpeg(*args)
            if result.returncode != 0:
                return ToolExecResult(
                    status=ToolStatus.ERROR,
                    tool_name=self.name,
                    error=f"ffmpeg error: {result.stderr[:500]}",
                )
            return ToolExecResult(
                status=ToolStatus.SUCCESS,
                tool_name=self.name,
                output={"input_path": input_path, "output_path": out, "start_sec": start_sec},
                output_path=out,
            )
        except FileNotFoundError:
            return ToolExecResult(
                status=ToolStatus.DEPENDENCY_MISSING,
                tool_name=self.name,
                error="ffmpeg not found — install FFmpeg to enable video processing",
            )
        except subprocess.TimeoutExpired:
            return ToolExecResult(
                status=ToolStatus.ERROR,
                tool_name=self.name,
                error="ffmpeg timed out",
            )


class VideoConcatTool(BaseTool):
    """视频拼接工具。"""
    name = "video_concat"
    description = "拼接多个视频片段（要求同编码/同分辨率）"
    dependencies = ["ffmpeg"]

    async def execute(
        self,
        clips: list[str],
        output_path: Optional[str] = None,
        **kwargs: Any,
    ) -> ToolExecResult:
        out = _ensure_output_path(output_path, "concat_", ".mp4")
        try:
            # 用 concat demuxer
            file_list = _ensure_output_path(None, "concat_list_", ".txt")
            with open(file_list, "w") as f:
                for clip in clips:
                    f.write(f"file '{clip}'\n")
            result = _ffmpeg("-f", "concat", "-safe", "0", "-i", file_list, "-c", "copy", out)
            os.unlink(file_list)
            if result.returncode != 0:
                return ToolExecResult(
                    status=ToolStatus.ERROR,
                    tool_name=self.name,
                    error=f"ffmpeg error: {result.stderr[:500]}",
                )
            return ToolExecResult(
                status=ToolStatus.SUCCESS,
                tool_name=self.name,
                output={"clip_count": len(clips), "output_path": out},
                output_path=out,
            )
        except FileNotFoundError:
            return ToolExecResult(
                status=ToolStatus.DEPENDENCY_MISSING,
                tool_name=self.name,
                error="ffmpeg not found",
            )


class VideoOverlayTool(BaseTool):
    """视频叠加工具（画中画、图片叠加）。"""
    name = "video_overlay"
    description = "在视频上叠加另一个视频或图像"
    dependencies = ["ffmpeg"]

    async def execute(
        self,
        background_path: str,
        overlay_path: str,
        position: Optional[dict[str, float]] = None,
        output_path: Optional[str] = None,
        **kwargs: Any,
    ) -> ToolExecResult:
        out = _ensure_output_path(output_path, "overlay_", ".mp4")
        pos = position or {"x": 0, "y": 0}
        try:
            result = _ffmpeg(
                "-i", background_path,
                "-i", overlay_path,
                "-filter_complex",
                f"overlay={pos.get('x', 0)}:{pos.get('y', 0)}",
                "-c:a", "copy", out,
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
                output={"output_path": out},
                output_path=out,
            )
        except FileNotFoundError:
            return ToolExecResult(
                status=ToolStatus.DEPENDENCY_MISSING,
                tool_name=self.name,
                error="ffmpeg not found",
            )
