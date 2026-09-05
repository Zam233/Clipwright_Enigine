"""字幕烧录工具 — 将字幕/文字叠加到视频。"""

from __future__ import annotations

import os
import asyncio
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

from clipwright.config import logger
from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.tool.base import BaseTool


def _mkstemp_txt() -> str:
    """安全临时文本文件（mkstemp 原子创建，防 mktemp 可预测名竞态）。"""
    fd, path = tempfile.mkstemp(suffix=".txt")
    import os
    os.close(fd)
    return path

from clipwright.tool.video import _ensure_output_path
from clipwright.tool.video import resolve_ffmpeg


class SubtitleBurnTool(BaseTool):
    """字幕烧录工具 — 将字幕文本以硬字幕形式叠加到视频。"""
    name = "subtitle_burn"
    description = "将字幕/文本烧录到视频（硬字幕），支持中文"
    dependencies = [resolve_ffmpeg()]

    async def execute(
        self,
        video_path: str,
        subtitles: list[dict[str, Any]],
        output_path: Optional[str] = None,
        font_size: int = 36,
        font_color: str = "white",
        **kwargs: Any,
    ) -> ToolExecResult:
        out = _ensure_output_path(output_path, "sub_", ".mp4")
        if not subtitles:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error="no subtitles")

        # 找中文字体
        font_spec = ""
        if os.name == "nt":
            for fp in [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\SimSun.ttc"]:
                if Path(fp).exists():
                    from clipwright.services.fontconfig import FontConfig
                    font_spec = FontConfig.ffmpeg_fontspec(fp)
                    break

        try:
            # 用 drawtext 逐条叠加（textfile 模式）
            text_file = Path(_mkstemp_txt())
            try:
                # 只写第一条字幕（drawtext 不支持多段文本交替的 textfile）
                # 对多段字幕用多个 drawtext filter
                filters = []
                for i, sub in enumerate(subtitles[:50]):  # 最多 50 段
                    txt = (sub.get("text", "") or "")[:100]
                    if not txt:
                        continue
                    start = sub.get("start_sec", 0)
                    end = start + sub.get("duration_sec", 3)
                    safe_txt = txt.replace("'", "'\\''").replace(":", "\\:")
                    font_color_val = sub.get("font_color", font_color)
                    font_size_val = sub.get("font_size", font_size)
                    enable = f"between(t,{start},{end})"
                    filters.append(
                        f"drawtext=text='{safe_txt}'"
                        f":fontsize={font_size_val}:fontcolor={font_color_val}"
                        f":x=(w-text_w)/2:y=h-{font_size_val * 2}{font_spec}"
                        f":enable='{enable}'"
                    )

                if not filters:
                    return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error="no valid subtitles")

                cmd = [resolve_ffmpeg(), "-y", "-loglevel", "error",
                       "-i", video_path,
                       "-vf", ",".join(filters),
                       "-c:v", "libx264", "-pix_fmt", "yuv420p",
                       "-c:a", "copy", out]
                result = await asyncio.to_thread(subprocess.run,cmd, capture_output=True, text=True, timeout=300)
                if result.returncode != 0:
                    return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                          error=f"subtitle burn error: {result.stderr[:300]}")
                return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name,
                                      output={"output_path": out, "subtitles": len(filters)}, output_path=out)
            finally:
                if text_file.exists():
                    text_file.unlink(missing_ok=True)
        except FileNotFoundError:
            logger.warning("FFmpeg 不可用: %s", self.name)
            return ToolExecResult(status=ToolStatus.DEPENDENCY_MISSING, tool_name=self.name, error="ffmpeg not found")
        except subprocess.TimeoutExpired:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error="subtitle burn timeout")
