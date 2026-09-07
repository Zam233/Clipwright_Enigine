"""文字转视频工具 — 从文本生成纯色背景 + 文字的视频片段。"""

from __future__ import annotations

import os
import asyncio
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any, Optional

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
from clipwright.config import logger


def _font_filter_arg() -> str:
    """drawtext fontfile 参数。

    本项目 ffmpeg 的过滤器解析器不识别反斜杠冒号转义（见
    render._resolve_system_font 注释），Windows 盘符路径会被冒号截断——
    复制字体到项目 ``_fonts/`` 用无盘符相对路径；Unix 直接绝对路径。
    解析失败返回空串（drawtext 用默认字体，中文可能豆腐块，但占位视频仍可交付）。
    """
    try:
        from clipwright.services.fontconfig import FontConfig
        fpath = FontConfig.get_font_path()
        if not fpath:
            return ""
        sp = Path(fpath)
        if os.name == "nt":
            import shutil
            dest_dir = Path.cwd() / "_fonts"
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / sp.name
            if not dest.exists():
                shutil.copy2(sp, dest)
            return ":fontfile=_fonts/" + sp.name
        return ":fontfile=" + sp.as_posix()
    except Exception:
        return ""


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
            text_file = Path(_mkstemp_txt())
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
            # 批1：uuid 命名——旧 getpid 固定名在 EditAgent 并发场景下互相覆盖
            local_text = Path(f"__text_{uuid.uuid4().hex[:8]}.txt")
            try:
                local_text.write_text(text[:200], encoding="utf-8")
                local_path = str(local_text).replace("\\", "/")

                # 批1：字体路径改走 _fonts/ 复制方案（ffmpeg_fontspec 的 \: 转义
                # 会被本机构建的过滤器解析器截断 → drawtext 必败 → 纯色无字回退）
                font_file = _font_filter_arg()
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
