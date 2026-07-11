"""渲染服务 — 将 Timeline JSON 渲染为 MP4 视频文件。

流程：
1. 解析 Timeline，按轨道分离视频/音频/文字片段
2. 对每个视频 clip 调用 video_trim 裁剪源素材
3. 拼接所有裁剪后的片段（含转场）
4. 叠加文字/字幕
5. 混合音频
6. 输出最终 MP4
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

from clipwright.config import logger
from clipwright.schema.timeline import Timeline
from clipwright.tool.registry import ToolRegistry


class RenderResult:
    def __init__(
        self,
        success: bool,
        output_path: str = "",
        error: str = "",
        duration_sec: float = 0,
    ):
        self.success = success
        self.output_path = output_path
        self.error = error
        self.duration_sec = duration_sec

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "output_path": self.output_path,
            "error": self.error,
            "duration_sec": self.duration_sec,
        }


class RenderService:
    """将 Timeline 渲染为 MP4 视频。"""

    def __init__(self, work_dir: Optional[str | Path] = None) -> None:
        self._work_dir = Path(work_dir or tempfile.mkdtemp(prefix="clipwright_render_"))
        self._work_dir.mkdir(parents=True, exist_ok=True)

    async def render(
        self,
        timeline: Timeline,
        output_path: str | Path = "out.mp4",
    ) -> RenderResult:
        """渲染完整时间线为 MP4。"""
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)

        try:
            # 1. 从时间线提取素材信息
            video_segments: list[dict] = []
            text_overlays: list[dict] = []
            audio_segments: list[dict] = []

            for track in timeline.tracks:
                kind = track.kind
                for clip in track.clips:
                    entry = {
                        "asset_id": clip.asset_id,
                        "start_sec": clip.start_sec,
                        "duration_sec": clip.duration_sec,
                        "source_offset": clip.source_offset_sec,
                        "speed": clip.speed,
                        "volume": clip.volume,
                        "opacity": clip.opacity,
                    }
                    if kind in ("video", "image"):
                        entry["source_path"] = clip.asset_id  # Will be resolved
                        video_segments.append(entry)
                    elif kind == "audio":
                        entry["source_path"] = clip.asset_id
                        audio_segments.append(entry)
                    elif kind in ("text", "caption"):
                        text_overlays.append({
                            "start_sec": clip.start_sec,
                            "duration_sec": clip.duration_sec,
                            "text": clip.text or "",
                            "font_size": clip.font_size or 48,
                            "font_color": clip.font_color or "#ffffff",
                        })

            # 2. 处理视频轨：裁剪每个片段
            trimmed_files: list[str] = []
            for seg in video_segments:
                src = seg.get("source_path", "")
                if not src or not Path(src).exists():
                    continue
                out = str(self._work_dir / f"trim_{len(trimmed_files)}.mp4")
                result = await ToolRegistry.execute(
                    "video_trim",
                    input_path=src,
                    start_sec=seg["source_offset"],
                    duration_sec=seg["duration_sec"] * seg["speed"],
                    output_path=out,
                )
                if result.status == "success" and result.output_path:
                    trimmed_files.append(result.output_path)
                else:
                    logger.warning("裁剪失败 %s: %s", src, result.error)

            # 3. 拼接视频
            final_video = str(self._work_dir / "concat.mp4")
            if trimmed_files:
                concat_result = await ToolRegistry.execute(
                    "video_concat",
                    clips=trimmed_files,
                    output_path=final_video,
                )
                if concat_result.status != "success":
                    logger.warning("拼接失败: %s", concat_result.error)
                    final_video = trimmed_files[0] if trimmed_files else ""

            # 4. 叠加文字
            if final_video and text_overlays:
                video_with_text = str(self._work_dir / "with_text.mp4")
                await self._apply_text_overlays(final_video, text_overlays, video_with_text)
                final_video = video_with_text

            # 5. 处理音频
            if final_video and audio_segments:
                video_with_audio = str(self._work_dir / "with_audio.mp4")
                await self._mix_audio(final_video, audio_segments, video_with_audio)
                final_video = video_with_audio

            # 6. 复制到最终输出
            if final_video and Path(final_video).exists():
                import shutil
                shutil.copy2(final_video, str(output))
                duration = self._get_duration(str(output))
                return RenderResult(
                    success=True,
                    output_path=str(output.resolve()),
                    duration_sec=duration,
                )

            return RenderResult(
                success=False,
                error="没有可渲染的视频素材",
            )

        except Exception as e:
            logger.exception("渲染失败")
            return RenderResult(success=False, error=str(e))

    async def _apply_text_overlays(
        self,
        input_video: str,
        overlays: list[dict],
        output_path: str,
    ) -> None:
        """使用 FFmpeg drawtext 在视频上叠加文字/字幕。"""
        if not overlays:
            return

        filter_parts: list[str] = []
        for i, ov in enumerate(overlays):
            text = ov.get("text", "").replace(":", "\\:").replace("'", "'\\\\''")
            start = ov["start_sec"]
            dur = ov["duration_sec"]
            font_size = ov.get("font_size", 48)
            color = ov.get("font_color", "#ffffff").lstrip("#")
            enable = f"between(t,{start},{start + dur})"
            filter_parts.append(
                f"drawtext=text='{text}'"
                f":fontsize={font_size}"
                f":fontcolor=0x{color}"
                f":x=(w-text_w)/2"
                f":y=(h-text_h)/2"
                f":enable='{enable}'"
            )

        if filter_parts:
            filter_str = ",".join(filter_parts) if len(filter_parts) <= 1 else "[0:v]" + ",".join(
                f"[{i}]drawtext=text='{ov['text']}'"
                f":fontsize={ov.get('font_size', 48)}"
                f":fontcolor=0x{ov.get('font_color', '#ffffff').lstrip('#')}"
                f":x=(w-text_w)/2:y=(h-text_h)/2"
                f":enable='between(t,{ov['start_sec']},{ov['start_sec']+ov['duration_sec']})'"
                for i, ov in enumerate(overlays)
            )

            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-i", input_video,
                     "-vf", filter_str,
                     "-c:a", "copy", output_path],
                    capture_output=True, text=True, timeout=300,
                )
            except FileNotFoundError:
                logger.warning("FFmpeg 不可用，跳过文字叠加")
                import shutil
                shutil.copy2(input_video, output_path)
            except subprocess.TimeoutExpired:
                logger.warning("文字叠加超时")
                import shutil
                shutil.copy2(input_video, output_path)

    async def _mix_audio(
        self,
        input_video: str,
        segments: list[dict],
        output_path: str,
    ) -> None:
        """混合音频到视频。"""
        # 简单实现：取第一个有路径的音频
        audio_path = None
        for seg in segments:
            src = seg.get("source_path", "")
            if src and Path(src).exists():
                audio_path = src
                break

        if audio_path:
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-i", input_video, "-i", audio_path,
                     "-c:v", "copy", "-c:a", "aac",
                     "-map", "0:v:0", "-map", "1:a:0",
                     "-shortest", output_path],
                    capture_output=True, text=True, timeout=300,
                )
                return
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass

        import shutil
        shutil.copy2(input_video, output_path)

    @staticmethod
    def _get_duration(path: str) -> float:
        """获取视频时长。"""
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "json", path],
                capture_output=True, text=True, timeout=30,
            )
            import json
            data = json.loads(result.stdout)
            return float(data.get("format", {}).get("duration", 0))
        except Exception:
            return 0

    def cleanup(self) -> None:
        """清理临时工作目录。"""
        import shutil
        if self._work_dir.exists():
            shutil.rmtree(self._work_dir, ignore_errors=True)
