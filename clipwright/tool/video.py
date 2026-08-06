"""视频处理工具 — FFmpeg 封装。

设计约束：所有 API 的入参必须是纯数值或纯路径。
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.tool.base import BaseTool
from clipwright.config import logger


_CLIPWRIGHT_TEMP = Path("_cache") / "tmp"
_CLIPWRIGHT_TEMP.mkdir(parents=True, exist_ok=True)


def _ensure_output_path(suggested: Optional[str], prefix: str, ext: str) -> str:
    """生成输出路径（建议路径不存在时自动创建）。

    默认使用项目目录下的 _cache/tmp，避免填满系统盘 C。
    """
    if suggested:
        p = Path(suggested)
        p.parent.mkdir(parents=True, exist_ok=True)
        return str(p)
    # 使用项目本地临时目录，而非系统 temp
    import uuid
    name = f"{prefix}{uuid.uuid4().hex[:8]}{ext}"
    path = _CLIPWRIGHT_TEMP / name
    return str(path)


async def _ffmpeg(*args: str, timeout: int = 300) -> subprocess.CompletedProcess:
    """调用 ffmpeg，CommandNotFound 时抛出 FileNotFoundError。

    async 上下文（事件循环线程）里自动 offload 到线程，避免冻住整个服务；
    sync 上下文（worker 线程等）则直接同步执行。调用方需 ``await``。
    """
    cmd = [resolve_ffmpeg(), "-y", *args]
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return await asyncio.to_thread(
        subprocess.run, cmd, capture_output=True, text=True, timeout=timeout
    )


def _resolve_bin(name: str, configured: str) -> str:
    """解析可执行文件路径：配置项 → PATH → 常见安装位置（如 WinGet）。"""
    if configured:
        return configured
    found = shutil.which(name)
    if found:
        return found
    if os.name == "nt":
        # WinGet 默认安装位置（Gyan.FFmpeg）
        try:
            local = os.environ.get("LOCALAPPDATA", "")
            if local:
                import glob as _glob
                pattern = os.path.join(local, "Microsoft", "WinGet", "Packages",
                                       "Gyan.FFmpeg_*", "ffmpeg-*-full_build", "bin", f"{name}.exe")
                matches = _glob.glob(pattern)
                if matches:
                    return matches[0]
        except Exception:
            pass
    return name  # 退回名称，依赖 PATH（找不到时 subprocess 会抛 FileNotFoundError）


def resolve_ffmpeg() -> str:
    from clipwright.config import settings
    return _resolve_bin("ffmpeg", settings.ffmpeg_path)


def resolve_ffprobe() -> str:
    from clipwright.config import settings
    return _resolve_bin("ffprobe", settings.ffprobe_path)


def _check_ffmpeg() -> Optional[str]:
    """检测 ffmpeg 是否可用（解析到真实路径则可用）。"""
    try:
        path = resolve_ffmpeg()
        if path and (os.path.isabs(path) and os.path.exists(path)):
            return path
        if shutil.which(path):
            return path
    except Exception:
        pass
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
        # 输入预检：源文件缺失/过小（损坏）直接报错，不调 ffmpeg
        try:
            if not os.path.isfile(input_path) or os.path.getsize(input_path) < 2000:
                return ToolExecResult(
                    status=ToolStatus.ERROR,
                    tool_name=self.name,
                    error=f"source missing/corrupt: {input_path}",
                )
        except OSError as e:
            return ToolExecResult(
                status=ToolStatus.ERROR,
                tool_name=self.name,
                error=f"source missing/corrupt: {input_path} ({e})",
            )
        try:
            args = ["-ss", str(start_sec), "-i", input_path]
            if duration_sec is not None:
                args.extend(["-t", str(duration_sec)])
            elif end_sec is not None:
                args.extend(["-to", str(end_sec)])
            args.extend(["-c", "copy", out])
            result = await _ffmpeg(*args)
            if result.returncode != 0:
                return ToolExecResult(
                    status=ToolStatus.ERROR,
                    tool_name=self.name,
                    error=f"ffmpeg error: {result.stderr[:500]}",
                )
            # 输出校验：-c copy 对损坏源可能 exit 0 但产出 ~258B 空容器（无流）
            if not self._validate_trim_output(out):
                return ToolExecResult(
                    status=ToolStatus.ERROR,
                    tool_name=self.name,
                    error="trim output invalid (empty/corrupt container)",
                    output_path=out,
                )
            return ToolExecResult(
                status=ToolStatus.SUCCESS,
                tool_name=self.name,
                output={"input_path": input_path, "output_path": out, "start_sec": start_sec},
                output_path=out,
            )
        except FileNotFoundError:
            logger.warning("FFmpeg 不可用: %s", self.name)
            return ToolExecResult(
                status=ToolStatus.DEPENDENCY_MISSING,
                tool_name=self.name,
                error="ffmpeg not found — install FFmpeg to enable video processing",
            )
        except subprocess.TimeoutExpired:
            logger.error("FFmpeg 超时: %s", self.name)
            return ToolExecResult(
                status=ToolStatus.ERROR,
                tool_name=self.name,
                error="ffmpeg timed out",
            )

    @staticmethod
    def _validate_trim_output(path: str) -> bool:
        """校验 trim 产物：存在、非空容器、含视频流、时长可解析且 > 0.2s。"""
        try:
            if not os.path.isfile(path) or os.path.getsize(path) <= 2000:
                return False
            result = subprocess.run(
                [resolve_ffprobe(), "-v", "error", "-print_format", "json",
                 "-show_format", "-show_streams", path],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                return False
            data = json.loads(result.stdout or "{}")
            streams = data.get("streams") or []
            if not any(s.get("codec_type") == "video" for s in streams):
                return False
            duration = float(data.get("format", {}).get("duration", 0) or 0)
            return duration > 0.2
        except Exception:
            return False


class VideoDownloadTool(BaseTool):
    """视频下载工具 — 从 URL 下载素材到本地缓存。"""
    name = "video_download"
    description = "从 HTTP/HTTPS URL 下载视频到本地缓存目录，返回本地路径"
    dependencies = ["ffmpeg"]

    async def execute(
        self,
        url: str,
        output_dir: Optional[str] = None,
        **kwargs: Any,
    ) -> ToolExecResult:
        import tempfile
        base = Path(output_dir) if output_dir else Path(tempfile.mkdtemp(prefix="dl_"))
        base.mkdir(parents=True, exist_ok=True)
        name = url.split("/")[-1].split("?")[0] or f"dl_{hash(url) % 100000}.mp4"
        out = str(base / name)

        if Path(out).exists():
            return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name,
                                  output={"local_path": out, "cached": True}, output_path=out)

        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["ffmpeg", "-y", "-loglevel", "error",
                 "-i", url, "-c", "copy", "-movflags", "+faststart", out],
                capture_output=True, text=True, timeout=600,
            )
            if result.returncode != 0 or not Path(out).exists():
                return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                      error=f"download failed: {result.stderr[:200]}")
            size = Path(out).stat().st_size
            logger.info("VideoDownload: %s → %s (%.1fMB)", url, out, size / 1024 / 1024)
            return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name,
                                  output={"local_path": out, "size_bytes": size}, output_path=out)
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            logger.warning("VideoDownload 失败: %s", e)
            return ToolExecResult(status=ToolStatus.DEPENDENCY_MISSING if isinstance(e, FileNotFoundError) else ToolStatus.ERROR,
                                  tool_name=self.name, error=str(e))


class MediaProbeTool(BaseTool):
    """媒体探测工具 — 用 ffprobe 获取文件的流信息、时长、编码、分辨率等。"""
    name = "media_probe"
    description = "探测媒体文件信息：时长、分辨率、编码、帧率、流详情"
    dependencies = ["ffprobe"]

    async def execute(
        self,
        input_path: str,
        **kwargs: Any,
    ) -> ToolExecResult:
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["ffprobe", "-v", "error", "-print_format", "json",
                 "-show_format", "-show_streams", input_path],
                capture_output=True, text=True, timeout=30,
            )
            data = json.loads(result.stdout)
            fmt = data.get("format", {})
            streams = data.get("streams", [])

            info = {
                "duration_sec": float(fmt.get("duration", 0)),
                "size_bytes": int(fmt.get("size", 0)),
                "bitrate": fmt.get("bit_rate", ""),
                "streams": [],
            }
            for s in streams:
                sinfo = {
                    "index": s.get("index"),
                    "codec_type": s.get("codec_type"),
                    "codec": s.get("codec_name"),
                }
                if s.get("codec_type") == "video":
                    sinfo.update({
                        "width": s.get("width", 0),
                        "height": s.get("height", 0),
                        "fps": s.get("r_frame_rate", ""),
                        "pix_fmt": s.get("pix_fmt", ""),
                    })
                elif s.get("codec_type") == "audio":
                    sinfo.update({
                        "sample_rate": s.get("sample_rate", ""),
                        "channels": s.get("channels", 0),
                    })
                info["streams"].append(sinfo)

            return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name,
                                  output=info)
        except FileNotFoundError:
            logger.warning("ffprobe 不可用")
            return ToolExecResult(status=ToolStatus.DEPENDENCY_MISSING, tool_name=self.name, error="ffprobe not found")
        except Exception as e:
            logger.warning("MediaProbe 失败: %s", e)
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error=str(e))


class VideoCropTool(BaseTool):
    """视频裁切工具 — 将视频裁切为指定宽高比。"""
    name = "video_crop"
    description = "裁切视频画面比例，如 16:9 转 9:16, 1:1 等"
    dependencies = ["ffmpeg"]

    async def execute(
        self,
        input_path: str,
        aspect: str = "9:16",
        output_path: Optional[str] = None,
        **kwargs: Any,
    ) -> ToolExecResult:
        out = _ensure_output_path(output_path, "crop_", ".mp4")
        try:
            # 用 ffmpeg 的 crop 过滤器裁切居中区域
            result = await asyncio.to_thread(
                subprocess.run,
                ["ffmpeg", "-y", "-loglevel", "error", "-i", input_path,
                 "-vf", f"crop={aspect.replace(':', '/')}:ih:iw/({aspect.replace(':', '/')}):(ih-iw/({aspect.replace(':', '/')}))/2",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p",
                 "-c:a", "copy", out],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                      error=f"crop error: {result.stderr[:200]}")
            return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name,
                                  output={"output_path": out, "aspect": aspect}, output_path=out)
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            return ToolExecResult(status=ToolStatus.ERROR if isinstance(e, subprocess.TimeoutExpired) else ToolStatus.DEPENDENCY_MISSING,
                                  tool_name=self.name, error=str(e))


class VideoThumbnailTool(BaseTool):
    """视频缩略图生成 — 提取视频最佳帧 + 叠加标题文字。"""
    name = "video_thumbnail"
    description = "从视频提取关键帧生成封面缩略图，可选叠加标题文字"
    dependencies = ["ffmpeg"]

    async def execute(
        self,
        input_path: str,
        text: str = "",
        time_sec: float = 0.5,
        output_path: Optional[str] = None,
        **kwargs: Any,
    ) -> ToolExecResult:
        import tempfile
        out = output_path or Path(tempfile.mktemp(suffix=".jpg")).name
        try:
            cmd = ["ffmpeg", "-y", "-loglevel", "error",
                   "-ss", str(time_sec), "-i", input_path,
                   "-vframes", "1", "-vf", "scale=1280:-1"]
            if text:
                # 叠加底部标题文字（先截断，再转义 ' : , 避免破坏 drawtext 过滤语法）
                from clipwright.services.fontconfig import FontConfig
                font_spec = FontConfig.ffmpeg_fontspec(FontConfig.get_font_path())
                safe = text[:50].replace("'", "''").replace(":", "\\:").replace(",", "\\,")
                cmd[11] += f",drawtext=text='{safe}':fontsize=48:fontcolor=white:x=(w-text_w)/2:y=h-100{font_spec}"
            cmd.extend(["-q:v", "3", out])
            result = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0 or not Path(out).exists():
                return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                      error=f"thumb error: {result.stderr[:200]}")
            return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name,
                                  output={"output_path": out, "text": text[:50]}, output_path=out)
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            return ToolExecResult(status=ToolStatus.ERROR if isinstance(e, subprocess.TimeoutExpired) else ToolStatus.DEPENDENCY_MISSING,
                                  tool_name=self.name, error=str(e))


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
            result = await _ffmpeg("-f", "concat", "-safe", "0", "-i", file_list, "-c", "copy", out)
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
            logger.warning("FFmpeg 不可用: %s", self.name)
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
            result = await _ffmpeg(
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
            logger.warning("FFmpeg 不可用: %s", self.name)
            return ToolExecResult(
                status=ToolStatus.DEPENDENCY_MISSING,
                tool_name=self.name,
                error="ffmpeg not found",
            )
