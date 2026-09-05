"""文字转视频工具 — 从文本生成纯色背景 + 文字的视频片段。"""

from __future__ import annotations

import os
import asyncio
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.tool.base import BaseTool
from clipwright.tool.video import _ensure_output_path
from clipwright.tool.video import resolve_ffmpeg
from clipwright.config import logger


class GenerateTextVideoTool(BaseTool):
    """从文本生成视频（纯色背景 + 文字叠加）。"""
    name = "generate_text_video"
    description = "从文字生成视频片段：纯色背景上叠加文字，用于无素材时的占位"
    dependencies = [resolve_ffmpeg()]

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
            # 先写文本到文件（供 FFmpeg 的 textfile 读取）
            text_file = Path(tempfile.mktemp(suffix=".txt"))
            text_file.write_text(text[:200], encoding="utf-8")

            # 生成纯色背景视频，用 ass 字幕叠加文字（避免 drawtext 的参数转义问题）
            # 方案：先生成无声纯色视频 + 用 drawtext 的 textfile 读取
            tf_path = str(text_file).replace("\\", "/")
            # 转义冒号：drawtext filter 中 : 是参数分隔符，路径中的 C: 会被误解析
            # 用单引号括起来让 FFmpeg 把路径整体作为一个值
            drawtext_filter = (
                f"drawtext=textfile='{tf_path}':"
                f"fontsize={font_size}:"
                f"fontcolor={font_color}:"
                f"x=(w-text_w)/2:y=(h-text_h)/2"
            )

            # 避免路径中的 C: 问题：使用简短文件名
            # 直接把文本写入当前目录的临时文件
            local_text = Path(f"__text_{os.getpid()}.txt")
            try:
                local_text.write_text(text[:200], encoding="utf-8")
                local_path = str(local_text).replace("\\", "/")

                # Windows 上 fontconfig 不可用，用 FontConfig 获取字体路径并转义
                from clipwright.services.fontconfig import FontConfig
                font_file = FontConfig.ffmpeg_fontspec(FontConfig.get_font_path())
                cmd = [
                    resolve_ffmpeg(), "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i",
                    f"color=c={bg_color}:s={width}x{height}:d={duration_sec}",
                    "-vf",
                    f"drawtext=textfile={local_path}:fontsize={font_size}:fontcolor={font_color}:x=(w-text_w)/2:y=(h-text_h)/2{font_file}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p",
                ]
                if duration_sec > 1:
                    cmd += ["-c:a", "aac"]
                else:
                    cmd += ["-an"]
                cmd.append(out)

                logger.debug("FFmpeg cmd: %s", " ".join(cmd))
                result = await asyncio.to_thread(subprocess.run,
                    cmd, capture_output=True, text=True, timeout=120,
                )
                if result.returncode != 0:
                    err = (result.stderr or "unknown")[:500]
                    logger.warning("generate_text_video FFmpeg 错误: %s", err)
                    # 回退：生成无文字纯色视频
                    logger.info("generate_text_video: 回退到纯色视频")
                    cmd[8] = f"color=c={bg_color}:s={width}x{height}:d={duration_sec}"
                    cmd[9:11] = []
                    result = await asyncio.to_thread(subprocess.run,
                        cmd, capture_output=True, text=True, timeout=120,
                    )
                    if result.returncode != 0:
                        return ToolExecResult(
                            status=ToolStatus.ERROR, tool_name=self.name,
                            error=f"FFmpeg error: {(result.stderr or 'unknown')[:500]}",
                        )

                logger.info("generate_text_video 成功: %s", text[:40])
                return ToolExecResult(
                    status=ToolStatus.SUCCESS, tool_name=self.name,
                    output={"text": text[:80], "duration_sec": duration_sec, "output_path": out},
                    output_path=out,
                )
            finally:
                if local_text.exists():
                    local_text.unlink(missing_ok=True)
        except FileNotFoundError:
            logger.warning("FFmpeg 不可用: %s", self.name)
            return ToolExecResult(status=ToolStatus.DEPENDENCY_MISSING, tool_name=self.name, error="ffmpeg not found")
        finally:
            if text_file.exists():
                text_file.unlink(missing_ok=True)
