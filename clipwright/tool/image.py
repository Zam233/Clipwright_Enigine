"""图片 → 视频片段（静帧 / Ken Burns 推拉）。

存在的原因（2026-10-31）：素材源里**图片占很大比例**（Pexels 搜图命中率远高于视频），
但编辑阶段只有一个 `video_trim` 能产出片段，于是图片候选要么被跳过、要么被拿去 trim
（ffmpeg 对图片 `-c copy` 剪出空容器）。补上这个能力后：

- `material` 可以放心把图片也下载下来（不再白下）；
- `edit` 遇到图片素材走 `image_clip`，得到一段有真实时长、可直接进时间线的 mp4。

两种模式：
- `still`（默认）：静帧铺满时长（最省算力，适合图表/截图/文字页）；
- `ken_burns`：缓慢推近（zoompan），适合照片类画面，避免"死画面"观感。
"""

from __future__ import annotations

import os
import subprocess
from typing import Any, Optional

from clipwright.config import logger
from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.tool.base import BaseTool
from clipwright.tool.video import _ensure_output_path, _ffmpeg, resolve_ffprobe

#: 允许的图片扩展名（不做内容嗅探：ffprobe 会在产物校验里兜住坏文件）
_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif")
#: 片段时长上限（秒）——防止把一张图铺成 10 分钟把渲染拖死
MAX_DURATION_SEC = 120.0


class ImageToClipTool(BaseTool):
    """把一张图片变成一段视频片段（静帧或缓慢推近）。"""

    name = "image_to_clip"
    #: 语义能力名：编辑/动画按能力解析到它，不认工具名（插件可接管 image_clip）
    capability = "image_clip"
    #: 权限档位**自声明**（只写工作区产物，可重做）——引擎的 TIER_MAP 不再需要登记
    permission_tier = "write_local"
    description = (
        "把图片转成视频片段（静帧或 Ken Burns 推近），支持指定时长/尺寸/帧率；"
        "输入为本地图片路径，输出 mp4。用于素材是图片（jpg/png/webp…）的场景。"
    )
    dependencies = ["ffmpeg"]

    async def execute(
        self,
        input_path: str,
        duration_sec: float = 4.0,
        output_path: Optional[str] = None,
        mode: str = "still",
        width: int = 1920,
        height: int = 1080,
        fps: int = 30,
        **kwargs: Any,
    ) -> ToolExecResult:
        src = str(input_path or "").strip()
        if not src:
            return ToolExecResult(
                status=ToolStatus.ERROR, tool_name=self.name,
                error="image_to_clip 需要 input_path",
            )
        if not os.path.isfile(src):
            return ToolExecResult(
                status=ToolStatus.ERROR, tool_name=self.name,
                error=f"source missing: {src}",
            )
        try:
            size = os.path.getsize(src)
        except OSError as exc:
            return ToolExecResult(
                status=ToolStatus.ERROR, tool_name=self.name,
                error=f"source unreadable: {src} ({exc})",
            )
        if size <= 500:
            return ToolExecResult(
                status=ToolStatus.ERROR, tool_name=self.name,
                error=f"source missing/corrupt: {src}（{size}B，太小）",
            )
        suffix = os.path.splitext(src)[1].lower()
        if suffix and suffix not in _IMAGE_EXT:
            # 明确拒绝而不是让 ffmpeg 报一堆滤镜错误
            return ToolExecResult(
                status=ToolStatus.ERROR, tool_name=self.name,
                error=(f"不是图片（{suffix}）：视频请用 video_trim，"
                       "文字页请用 text_placeholder"),
            )

        dur = max(0.2, min(float(duration_sec or 4.0), MAX_DURATION_SEC))
        w = max(64, int(width or 1920))
        h = max(64, int(height or 1080))
        rate = max(1, min(int(fps or 30), 120))
        out = _ensure_output_path(output_path, "imgclip_", ".mp4")
        mode_norm = "ken_burns" if str(mode).lower() in ("ken_burns", "kenburns", "zoom") else "still"

        # 统一到目标画幅：先用 scale+pad 保证不变形，再交给 zoompan/静帧
        base = (
            f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black"
        )
        if mode_norm == "ken_burns":
            # 缓慢推近：从 1.0 → 1.08（zoompan 的 z 表达式），d=1 表示每帧算一次
            vf = (
                f"{base},zoompan=z='min(zoom+0.0008,1.08)':d=1:"
                f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={w}x{h}:fps={rate}"
            )
            args = ["-loop", "1", "-i", src, "-t", f"{dur:.3f}", "-vf", vf]
        else:
            vf = f"{base},format=yuv420p"
            args = ["-loop", "1", "-i", src, "-t", f"{dur:.3f}", "-vf", vf,
                    "-r", str(rate)]

        args.extend([
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", out,
        ])
        try:
            result = await _ffmpeg(*args)
        except FileNotFoundError:
            return ToolExecResult(
                status=ToolStatus.DEPENDENCY_MISSING, tool_name=self.name,
                error="ffmpeg not found — install FFmpeg to enable video processing",
            )
        except subprocess.TimeoutExpired:
            return ToolExecResult(
                status=ToolStatus.ERROR, tool_name=self.name,
                error="ffmpeg timed out",
            )
        if result.returncode != 0:
            return ToolExecResult(
                status=ToolStatus.ERROR, tool_name=self.name,
                error=f"ffmpeg error: {result.stderr[:400]}",
            )
        ok, why = self._validate(out)
        if not ok:
            return ToolExecResult(
                status=ToolStatus.ERROR, tool_name=self.name,
                error=f"image clip invalid: {why}",
                output_path=out,
            )
        return ToolExecResult(
            status=ToolStatus.SUCCESS,
            tool_name=self.name,
            output={"input_path": src, "output_path": out, "duration_sec": dur,
                    "mode": mode_norm, "width": w, "height": h},
            output_path=out,
        )

    @staticmethod
    def _validate(path: str) -> tuple[bool, str]:
        """产物校验：存在、非空、含视频流、时长接近请求值（±0.5s）。"""
        try:
            if not os.path.isfile(path) or os.path.getsize(path) <= 2000:
                return False, "空容器"
            import json

            result = subprocess.run(
                [resolve_ffprobe(), "-v", "error", "-print_format", "json",
                 "-show_format", "-show_streams", path],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                return False, "ffprobe 读不出"
            data = json.loads(result.stdout or "{}")
            streams = data.get("streams") or []
            if not any(s.get("codec_type") == "video" for s in streams):
                return False, "没有视频流"
            duration = float(data.get("format", {}).get("duration") or 0)
            if duration <= 0.1:
                return False, f"时长 {duration:.2f}s"
            return True, ""
        except Exception as exc:  # noqa: BLE001 — 校验失败按无效处理
            logger.warning("image_to_clip 产物校验异常 %s: %s", path, exc)
            return False, f"{type(exc).__name__}"


__all__ = ["ImageToClipTool"]

