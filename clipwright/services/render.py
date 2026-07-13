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
import uuid
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
        from clipwright.tool.video import _CLIPWRIGHT_TEMP
        self._work_dir = Path(work_dir or _CLIPWRIGHT_TEMP / f"render_{uuid.uuid4().hex[:8]}")
        self._work_dir.mkdir(parents=True, exist_ok=True)

    async def render(
        self,
        timeline: Timeline,
        output_path: str | Path = "out.mp4",
        *,  # 关键字参数
        width: int = 1920,
        height: int = 1080,
        fps: float = 30.0,
        bitrate: str = "5M",
        audio_bitrate: str = "192k",
        audio_file_path: str = "",
        bgm_file_path: str = "",
        progress_callback: Optional[callable] = None,
    ) -> RenderResult:
        """渲染完整时间线为 MP4。

        Args:
            timeline: 时间线
            output_path: 输出路径
            width: 目标视频宽度
            height: 目标视频高度
            fps: 目标帧率
            bitrate: 视频码率
            audio_bitrate: 音频码率
            audio_file_path: 配音文件路径（手动指定）
            bgm_file_path: 背景音乐路径（可选）
        """
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)

        try:
            # 1. 从时间线提取素材信息
            video_segments: list[dict] = []
            overlay_segments: list[dict] = []   # 画中画/叠加轨道
            text_overlays: list[dict] = []
            audio_segments: list[dict] = []

            logger.info("开始渲染时间线: %s → %s, 目标=%dx%d@%dfps", output_path,
                        len(timeline.tracks or []), width, height, int(fps))
            for track in timeline.tracks:
                logger.info("  轨道 %s(%s): %d clips (index=%d)", track.name, track.kind,
                            len(track.clips), track.index)
                is_overlay = track.index > 0 and str(track.kind) in ("video", "image")
                for clip in track.clips:
                    clip_kind = str(clip.kind) if clip.kind else str(track.kind)
                    entry = {
                        "asset_id": clip.asset_id,
                        "start_sec": clip.start_sec,
                        "duration_sec": clip.duration_sec,
                        "source_offset": clip.source_offset_sec,
                        "speed": clip.speed,
                        "volume": clip.volume,
                        "opacity": clip.opacity,
                        "image_rect": clip.image_rect,
                    }
                    if clip_kind in ("video", "image"):
                        entry["source_path"] = clip.asset_id
                        if is_overlay:
                            overlay_segments.append(entry)
                        else:
                            video_segments.append(entry)
                    elif clip_kind == "audio":
                        entry["source_path"] = clip.asset_id
                        audio_segments.append(entry)
                    elif clip_kind in ("text", "caption"):
                        meta = clip.metadata or {}
                        style = meta.get("style", {})
                        track_pos_map = {1: "bottom", 2: "top", 3: "center"}
                        pos = meta.get("position", style.get("position", track_pos_map.get(track.index, "bottom")))
                        y_offset = len([t for t in text_overlays if t.get("_track_idx") == track.index]) * 35
                        text_overlays.append({
                            "start_sec": clip.start_sec,
                            "duration_sec": clip.duration_sec,
                            "text": clip.text or "",
                            "font_size": clip.font_size or 48,
                            "font_color": clip.font_color or "#ffffff",
                            "position": pos,
                            "offset_y": y_offset,
                            "style": style,
                            "anim_type": meta.get("anim_type", ""),
                            "anim_class": meta.get("anim_class", "hf-fade-in"),
                            "anim_duration": meta.get("anim_duration", 0.5),
                            "anim_easing": meta.get("anim_easing", "ease-out"),
                            "renderer": meta.get("renderer", "drawtext"),
                            "category": meta.get("category", ""),
                            "_track_idx": track.index,
                        })
                    elif clip_kind == "animation":
                        meta = clip.metadata or {}
                        text_overlays.append({
                            "start_sec": clip.start_sec,
                            "duration_sec": clip.duration_sec,
                            "text": clip.text or "",
                            "font_size": meta.get("font_size", 72),
                            "font_color": meta.get("font_color", "#ffd700"),
                            "position": meta.get("position", "center"),
                            "offset_y": 0,
                            "style": meta.get("style", {}),
                            "anim_type": meta.get("anim_type", "fade_in"),
                            "anim_class": meta.get("anim_class", "hf-fade-in"),
                            "renderer": meta.get("renderer", "hyperframes"),
                            "diagram_params": meta.get("diagram_params"),
                            "category": meta.get("category", ""),
                            "_track_idx": track.index,
                        })

            # 2. 处理视频轨：裁剪每个片段并统一缩放到目标分辨率
            scale_filter = f"scale={width}:{height}:force_original_aspect_ratio=1,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
            trimmed_files: list[str] = []
            total_segs = len(video_segments)
            for seg_idx, seg in enumerate(video_segments):
                if progress_callback:
                    pct = int((seg_idx / max(total_segs, 1)) * 50)
                    await progress_callback("trim", pct, f"裁剪 {seg_idx+1}/{total_segs}")
                src = seg.get("source_path", "")
                dur = max(0.5, seg.get("duration_sec", 5) * seg.get("speed", 1.0))
                scene_label = src.split("/")[-1][:30] if src else f"segment_{len(trimmed_files)}"
                logger.info("渲染片段[%d]: src=%s, dur=%.1fs", seg_idx, scene_label, dur)

                if src:  # 本地文件或 URL 都尝试裁剪
                    out = str(self._work_dir / f"trim_{len(trimmed_files)}.mp4")
                    try:
                        vf = scale_filter
                        # 如果有 keyframes，应用基本动画（当前支持 opacity 关键帧）
                        kfs = seg.get("keyframes", [])
                        if kfs:
                            dur_s = seg.get("duration_sec", dur)
                            kf_parts = []
                            for kf in kfs:
                                t = kf.get("time", 0)
                                opacity = kf.get("properties", {}).get("opacity", 1.0)
                                if opacity < 1.0 and dur_s > 0 and t < dur_s:
                                    kf_parts.append(
                                        f"between(t,{t},{t+0.1})*{opacity}+not(between(t,{t},{t+0.1}))"
                                    )
                            if kf_parts:
                                vf += f",format=rgba,colorchannelmixer=aa={'+'.join(kf_parts)}"

                        cmd = [
                            "ffmpeg", "-y", "-loglevel", "error",
                            "-ss", str(seg["source_offset"]),
                            "-i", src,
                            "-t", str(dur),
                            "-vf", vf,
                            "-r", str(fps),
                            "-c:v", "libx264", "-pix_fmt", "yuv420p",
                            "-b:v", bitrate,
                            "-an", out,
                        ]
                        result = subprocess.run(cmd, capture_output=True, text=False, timeout=300)
                        if result.returncode == 0 and Path(out).exists():
                            trimmed_files.append(out)
                            logger.info("裁剪完成: %s (%s)", scene_label, out)
                            continue
                        else:
                            logger.warning("裁剪失败 %s: %s", scene_label, (result.stderr or b"").decode("utf-8", errors="replace")[:200])
                    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
                        logger.warning("裁剪异常 %s: %s", scene_label, e)
                else:
                    logger.info("素材不存在: %s — 生成文字占位", scene_label)

                # 回退：生成本地纯色占位视频
                try:
                    fallback_out = str(self._work_dir / f"fallback_{len(trimmed_files)}.mp4")
                    subprocess.run(
                        ["ffmpeg", "-y", "-loglevel", "error",
                         "-f", "lavfi", "-i",
                         f"color=c=0x1a1a2e:s={width}x{height}:d={dur}",
                         "-c:v", "libx264", "-pix_fmt", "yuv420p",
                         "-r", str(fps), fallback_out],
                        capture_output=True, text=False, timeout=30,
                    )
                    if Path(fallback_out).exists():
                        trimmed_files.append(fallback_out)
                        logger.info("占位视频生成: %s (%.0fs)", scene_label, dur)
                        continue
                except Exception as e:
                    logger.warning("占位视频生成失败: %s", e)

            # 3. 拼接视频（所有片段已统一分辨率）
            final_video = str(self._work_dir / "concat.mp4")
            if trimmed_files:
                if progress_callback:
                    await progress_callback("concat", 55, f"拼接 {len(trimmed_files)} 个片段")
                logger.info("拼接 %d 个视频片段...", len(trimmed_files))
                # 用 concat demuxer 拼接（已同分辨率）
                file_list = str(self._work_dir / "concat_list.txt")
                try:
                    with open(file_list, "w", encoding="utf-8") as f:
                        for clip in trimmed_files:
                            # 用绝对路径，避免 concat demuxer 相对于文件列表目录解析路径
                            abs_path = str(Path(clip).resolve()).replace(chr(92), chr(47))
                            f.write(f"file '{abs_path}'\n")
                    cmd = [
                        "ffmpeg", "-y", "-loglevel", "error",
                        "-f", "concat", "-safe", "0",
                        "-i", file_list,
                        "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-b:v", bitrate,
                        "-r", str(fps),
                        "-an", final_video,
                    ]
                    result = subprocess.run(cmd, capture_output=True, text=False, timeout=300)
                    if result.returncode == 0 and Path(final_video).exists():
                        logger.info("拼接完成: %s (%d clips)", final_video, len(trimmed_files))
                    else:
                        logger.warning("拼接失败: %s", (result.stderr or b"").decode("utf-8", errors="replace")[:200])
                        final_video = trimmed_files[0] if trimmed_files else ""
                except (FileNotFoundError, subprocess.TimeoutExpired) as e:
                    logger.warning("拼接异常: %s", e)
                    final_video = trimmed_files[0] if trimmed_files else ""
                finally:
                    Path(file_list).unlink(missing_ok=True)

            # 4. 叠加文字（优先 Hyperframes，回退 drawtext）
            if final_video and text_overlays:
                if progress_callback:
                    await progress_callback("text", 65, "叠加文字/字幕")
                logger.info("叠加 %d 个文字/字幕...", len(text_overlays))
                video_with_text = str(self._work_dir / "with_text.mp4")
                if not Path(final_video).exists():
                    logger.warning("文字叠加: 输入视频不存在 %s, 跳过此步", final_video)
                else:
                    # 分流：普通文字→drawtext，逻辑图解→Hyperframes
                    drawtext_ov = [o for o in text_overlays
                                   if o.get("renderer") != "hyperframes" and not o.get("diagram_params")]
                    hf_ov = [o for o in text_overlays
                             if o.get("renderer") == "hyperframes" or o.get("diagram_params")]

                    # 1. drawtext 渲染普通文字/字幕
                    if drawtext_ov:
                        logger.info("drawtext 渲染 %d 个文字/字幕", len(drawtext_ov))
                        await self._apply_text_overlays(final_video, drawtext_ov, video_with_text)
                        final_video = video_with_text if Path(video_with_text).exists() else final_video

                    # 2. Hyperframes 渲染逻辑图解（叠在 drawtext 之上）
                    if hf_ov and self._hyperframes_available():
                        logger.info("Hyperframes 渲染 %d 个逻辑图解", len(hf_ov))
                        video_with_hf = str(self._work_dir / "with_hf.mp4")
                        await self._apply_text_via_hyperframes(
                            final_video, hf_ov, video_with_hf,
                            width, height, fps,
                        )
                        final_video = video_with_hf if Path(video_with_hf).exists() else final_video
                final_video = video_with_text if Path(video_with_text).exists() else final_video

            # 4.5 处理画中画/叠加轨道（index>0 的视频轨）
            if final_video and overlay_segments:
                logger.info("叠加 %d 个画中画/叠加片段...", len(overlay_segments))
                video_with_overlay = str(self._work_dir / "with_overlay.mp4")
                try:
                    await self._apply_overlays(final_video, overlay_segments, video_with_overlay, width, height)
                    final_video = video_with_overlay if Path(video_with_overlay).exists() else final_video
                except Exception as e:
                    logger.warning("画中画叠加跳过: %s", e)

            # 5. 处理音频
            if final_video and audio_segments:
                if progress_callback:
                    await progress_callback("audio", 80, "混合音频")
                logger.info("混合 %d 个音频片段...", len(audio_segments))
                video_with_audio = str(self._work_dir / "with_audio.mp4")
                if not Path(final_video).exists():
                    logger.warning("音频混合: 输入视频不存在 %s, 跳过此步", final_video)
                else:
                    await self._mix_audio(final_video, audio_segments, video_with_audio,
                                          audio_file_path=audio_file_path,
                                          audio_bitrate=audio_bitrate,
                                          bgm_file_path=bgm_file_path)
                final_video = video_with_audio if Path(video_with_audio).exists() else final_video

            # 6. 复制到最终输出
            if final_video and Path(final_video).exists():
                import shutil
                shutil.copy2(final_video, str(output))
                duration = self._get_duration(str(output))
                expected_dur = sum(seg.get("duration_sec", 0) * seg.get("speed", 1.0) for seg in video_segments)
                logger.info("渲染完成: %s (%.1fs, 预期约 %.0fs, trimmed=%d)", output, duration, expected_dur, len(trimmed_files))
                return RenderResult(
                    success=True,
                    output_path=str(output.resolve()),
                    duration_sec=duration,
                )

            # 调试：输出时间线完整结构
            logger.warning("渲染失败: video_segments=%d, audio_segments=%d, trimmed=%d",
                           len(video_segments), len(audio_segments), len(trimmed_files))
            if not video_segments:
                track_details = []
                for t in (timeline.tracks or []):
                    track_details.append(f"{t.name}({t.kind})={len(t.clips)}clips")
                logger.warning("时间线轨道详情: %s", " | ".join(track_details))
                # 输出第一个 clip 的详情（如果有的话）
                for t in (timeline.tracks or []):
                    if t.clips:
                        c = t.clips[0]
                        logger.warning("  首 clip: kind=%s, asset_id=%s, start=%.1f, dur=%.1f",
                                       c.kind, c.asset_id[:80] if c.asset_id else "(空)",
                                       c.start_sec, c.duration_sec)
                        break
            return RenderResult(
                success=False,
                error=f"没有可渲染的视频素材 (video_segments={len(video_segments)}, trimmed={len(trimmed_files)})",
            )

        except Exception as e:
            logger.exception("渲染失败")
            return RenderResult(success=False, error=str(e))

    @staticmethod
    def _text_overlays_need_hyperframes(overlays: list[dict]) -> bool:
        """检查覆盖层中是否有需要 Hyperframes 渲染的（typewriter/逻辑图解/scale/rotate/blur）。"""
        for ov in overlays:
            anim_type = ov.get("anim_type", "")
            kfs = ov.get("keyframes", []) or []
            diagram_params = ov.get("diagram_params")
            if diagram_params:
                return True
            if anim_type in ("typewriter", "char_by_char"):
                return True
            for kf in kfs:
                props = kf.get("properties", {})
                if any(p in props for p in ("scale_x", "scale_y", "rotate", "blur")):
                    return True
        return False

    @staticmethod
    def _hyperframes_available() -> bool:
        """Hyperframes CLI 是否可用。"""
        try:
            from clipwright.animation.hyperframes_renderer import HyperframesRenderer
            return HyperframesRenderer.is_available()
        except Exception:
            return False

    async def _apply_text_via_hyperframes(
        self,
        input_video: str,
        overlays: list[dict],
        output_path: str,
        width: int, height: int, fps: float,
    ) -> None:
        """使用 Hyperframes 渲染文本覆盖层并叠加到主视频。"""
        overlay_mov = str(Path(self._work_dir) / "overlay.mov")
        from clipwright.animation.hyperframes_renderer import HyperframesRenderer
        result = await HyperframesRenderer.render_overlays(
            overlays, overlay_mov, width, height, fps,
        )
        if result and Path(result).exists():
            ok = HyperframesRenderer.render_overlay_on_video(result, input_video, output_path)
            if ok:
                logger.info("Hyperframes 叠加完成: %s", output_path)
                return
            logger.warning("Hyperframes 叠加失败，回退到 drawtext")
        # 回退到 drawtext
        await self._apply_text_overlays(input_video, overlays, output_path)

    async def _apply_text_overlays(
        self,
        input_video: str,
        overlays: list[dict],
        output_path: str,
    ) -> None:
        """使用 FFmpeg drawtext 叠加文字，支持 keyframe 驱动的入场/出场动画。"""
        if not overlays:
            logger.info("文字叠加: 无文字片段")
            import shutil
            shutil.copy2(input_video, output_path)
            return
        logger.info("文字叠加: %d 段文字, 首段='%s'",
                     len(overlays), (overlays[0].get("text","")[:30] if overlays else ""))

        from clipwright.tool.design import TextStyle

        filter_parts = []
        for ov in overlays:
            text = (ov.get("text") or "")[:100]
            if not text:
                continue

            start = ov.get("start_sec", 0)
            dur = ov.get("duration_sec", 3)
            anim_type = ov.get("anim_type", "")
            kfs = ov.get("keyframes", []) or []

            # 构建 TextStyle
            style_dict = ov.get("style", {})
            if style_dict:
                ts = TextStyle.from_dict(style_dict)
            else:
                ts = TextStyle(
                    font_size=ov.get("font_size", 48),
                    font_color=ov.get("font_color", "#ffffff"),
                    stroke_width=ov.get("stroke_width", 0),
                    shadow=ov.get("shadow", False),
                    glow=ov.get("glow", False),
                    position=ov.get("position", "bottom"),
                    offset_y=ov.get("offset_y", 0),
                )

            # ── typewriter/char_by_char：字符级渲染（不走 keyframe 路径） ──
            if anim_type in ("typewriter", "char_by_char"):
                n = max(1, len(text))
                char_widths = self._calc_char_widths(text, ts.font_size)
                for i, ch in enumerate(text):
                    if ch.strip():
                        ch_start = start + i * (dur / n)
                        x_pos = sum(char_widths[:i]) + 8  # 累计所有前导字符宽度
                        filter_parts.append(
                            f"drawtext=text='{ch.replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'"
                            f":fontsize={ts.font_size}:fontcolor={ts.font_color}"
                            f":x={x_pos}:y=(h-text_h)/2"
                            f":enable='between(t,{ch_start},{start + dur})'"
                        )
                continue

            # ── keyframe 驱动的动画（入场 + 保持 + 出场） ──
            if kfs and len(kfs) >= 2:
                kf_filter = self._build_keyframe_drawtext(text, ts, start, dur, kfs)
                if kf_filter:
                    filter_parts.append(kf_filter)
                continue

            # ── 无 keyframes 时，fallback 到 anim_type 硬编码 ──
            if anim_type == "typewriter":
                n = max(1, len(text))
                char_widths = self._calc_char_widths(text, ts.font_size)
                for i, ch in enumerate(text):
                    if ch.strip():
                        ch_start = start + i * (dur / n)
                        x_pos = sum(char_widths[:i]) + 8
                        filter_parts.append(
                            f"drawtext=text='{ch.replace(chr(39),chr(39)+chr(92)+chr(39)+chr(39))}'"
                            f":fontsize={ts.font_size}:fontcolor={ts.font_color}"
                            f":x={x_pos}:y=(h-text_h)/2"
                            f":enable='between(t,{ch_start},{start + dur})'"
                        )
                continue
            elif anim_type == "glow":
                ts.glow = True
            elif anim_type == "rainbow":
                pass  # 普通 filter 即可

            # 默认静态文字叠加
            base_filter = ts.build_drawtext_filter(text, start, dur)
            filter_parts.append(base_filter)

            # 发光叠加层
            if ts.glow:
                glow_ts = TextStyle(
                    font_size=ts.font_size,
                    font_color=ts.glow_color,
                    position=ts.position,
                    opacity=0.4,
                )
                filter_parts.append(glow_ts.build_drawtext_filter(text, start, dur))

        if not filter_parts:
            import shutil
            shutil.copy2(input_video, output_path)
            return

        try:
            cmd = ["ffmpeg", "-y", "-loglevel", "error",
                   "-i", input_video,
                   "-vf", ",".join(filter_parts),
                   "-c:v", "libx264", "-pix_fmt", "yuv420p",
                   "-c:a", "copy", output_path]
            subprocess.run(cmd, capture_output=True, text=False, timeout=300)
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            logger.warning("文字叠加跳过: %s", e)
            import shutil
            shutil.copy2(input_video, output_path)

    @staticmethod
    def _build_keyframe_drawtext(
        text: str, ts: "TextStyle", start_sec: float, duration_sec: float,
        keyframes: list[dict],
    ) -> str:
        """从 keyframes 生成 drawtext filter，用 FFmpeg 表达式实现插值。

        keyframes: [{"time": sec, "properties": {"opacity": 0}}, ...]
        """
        if not keyframes:
            return ""

        # 检查并警告 drawtext 真正不支持的属性（rotate/blur 无法用 drawtext 模拟）
        _TRULY_UNSUPPORTED = {"rotate", "blur"}
        found_unsupported = set()
        for kf in keyframes:
            for p in _TRULY_UNSUPPORTED:
                if p in kf.get("properties", {}):
                    found_unsupported.add(p)
        if found_unsupported:
            logger.warning(
                "RenderService: keyframe 属性 %s 在 drawtext 中无法模拟，"
                "将降级（完整效果需要 Hyperframes）",
                found_unsupported,
            )

        safe_text = text.replace("'", "'\\''").replace(":", "\\:")
        safe_color = ts.font_color

        # 确定 start/end 时间范围（使用 keyframe 中的绝对时间）
        kf_times = [kf.get("time", 0) for kf in keyframes]
        kf_start = min(kf_times)
        kf_end = max(kf_times)
        enable_till = max(kf_end, start_sec + duration_sec)

        # 位置基础参数
        pos_map = {
            "center": "(w-text_w)/2", "top": "(w-text_w)/2",
            "bottom": "(w-text_w)/2", "left": "20",
            "right": "w-text_w-20",
            "top_left": "20", "top_right": "w-text_w-20",
            "bottom_left": "20", "bottom_right": "w-text_w-20",
        }
        base_x = pos_map.get(ts.position, "(w-text_w)/2")
        y_map = {
            "center": "(h-text_h)/2", "top": "20",
            "bottom": "h-text_h-20", "left": "(h-text_h)/2",
            "right": "(h-text_h)/2",
            "top_left": "20", "top_right": "20",
            "bottom_left": "h-text_h-20", "bottom_right": "h-text_h-20",
        }
        base_y = y_map.get(ts.position, "(h-text_h)/2")

        # 构建关键帧插值表达式
        def _interp_expr(key: str, default: float | str) -> str:
            """从 keyframes 中提取属性值变化，生成 FFmpeg 表达式。"""
            values = []
            for kf in keyframes:
                t = kf.get("time", 0)
                props = kf.get("properties", {})
                if key in props:
                    v = props[key]
                    values.append((t, v))
            if not values:
                return str(default)

            # 构建嵌套 if 表达式
            expr = str(values[-1][1])  # 最后一个值（默认值）
            for i in range(len(values) - 2, -1, -1):
                t0, v0 = values[i]
                t1, v1 = values[i + 1]
                interp = f"{v0}+({v1}-{v0})*(t-{t0})/({t1}-{t0})"
                expr = f"if(lt(t,{t1}),{interp},{expr})"
            return expr

        # 提取动画属性
        alpha_expr = _interp_expr("opacity", "1")
        x_offset = _interp_expr("translate_x", "0")
        y_offset = _interp_expr("translate_y", "0")

        # scale_x → fontsize 插值
        # drawtext 不支持 scale 属性，但可通过 fontsize 表达式模拟：
        # fontsize = base_fontsize * scale_interp
        scale_expr = _interp_expr("scale_x", "1")
        if scale_expr != "1":
            # scale_x 被用于 fontsize 乘数
            fontsize_expr = f"({ts.font_size})*({scale_expr})"
        else:
            fontsize_expr = str(ts.font_size)

        # 基础 drawtext + 动态属性
        parts = [
            f"drawtext=text='{safe_text}'",
            f"fontsize={fontsize_expr}",
            f"fontcolor={safe_color}",
            f"x={base_x}+({x_offset})",
            f"y={base_y}+({y_offset})",
            f"alpha={alpha_expr}",
            f":enable='between(t,{kf_start},{enable_till})'",
        ]

        if ts.stroke_width > 0:
            parts.append(f":borderw={ts.stroke_width}:bordercolor={ts.stroke_color}")

        if ts.shadow:
            parts.append(f":shadowx={ts.shadow_offset_x}:shadowy={ts.shadow_offset_y}:shadowcolor={ts.shadow_color}")

        return ":".join(parts)

    @staticmethod
    def _format_diagram_text(text: str, params: dict[str, Any]) -> str:
        """将逻辑动画的 diagram_params 格式化为可显示的文字。"""
        preset = params.get("preset", "")
        items = params.get("items", [])
        title = params.get("title", "")
        lines = [title] if title else []

        if preset in ("diagram", "causation"):
            if items:
                lines.append(" → ".join(items[:5]))
        elif preset == "comparison":
            if len(items) >= 2:
                lines.append(f"{items[0]}  VS  {items[1]}")
        elif preset == "sequence":
            if items:
                for i, item in enumerate(items[:5], 1):
                    lines.append(f"{i}. {item}")
        else:
            lines.append(text)

        return "\n".join(lines)

    @staticmethod
    def _calc_char_widths(text: str, font_size: int) -> list[int]:
        """计算每个字符的显示宽度（像素），区分 CJK 和 ASCII。

        CJK 字符 ≈ font_size 宽
        ASCII 字符 ≈ font_size * 0.6 宽
        空格和标点 ≈ font_size * 0.3 宽
        """
        widths = []
        for ch in text:
            code = ord(ch)
            if code > 0x2E80:  # CJK 统一表意文字区段起点
                widths.append(font_size)
            elif ch in (" ", "\t"):
                widths.append(int(font_size * 0.3))
            else:
                widths.append(int(font_size * 0.6))
        return widths

    async def _apply_overlays(
        self,
        input_video: str,
        overlays: list[dict],
        output_path: str,
        target_width: int = 1920,
        target_height: int = 1080,
    ) -> None:
        """叠加画中画/覆盖层到主视频。"""
        if not overlays:
            import shutil
            shutil.copy2(input_video, output_path)
            return

        try:
            # 为每个 overlay 片段生成 overlay filter
            overlay_filters = []
            for i, ov in enumerate(overlays):
                src = ov.get("source_path", "")
                dur = ov.get("duration_sec", 5)
                start = ov.get("start_sec", 0)
                opacity = ov.get("opacity", 1.0)
                rect = ov.get("image_rect") or {"x": 0.65, "y": 0.05, "w": 0.3, "h": 0.3}

                if not src or not Path(src).exists():
                    continue

                # 计算位置和大小（归一化坐标）
                ow = int(target_width * rect.get("w", 0.3))
                oh = int(target_height * rect.get("h", 0.3))
                ox = int(target_width * rect.get("x", 0.65))
                oy = int(target_height * rect.get("y", 0.05))

                # 用 overlay filter，只在指定时间窗口显示
                enable = f"between(t,{start},{start + dur})"
                overlay_filters.append(
                    f"[{i + 1}:v]scale={ow}:{oh},format=rgba,"
                    f"colorchannelmixer=aa={opacity}[ov{i}];"
                    f"[0:v][ov{i}]overlay={ox}:{oy}:enable='{enable}'[v{i}]"
                )

            if not overlay_filters:
                import shutil
                shutil.copy2(input_video, output_path)
                return

            # 构建完整的 filter_complex
            inputs = ["-i", input_video]
            for ov in overlays:
                src = ov.get("source_path", "")
                if src and Path(src).exists():
                    inputs.extend(["-i", src])

            filter_chain = ";".join(overlay_filters)
            last_output = f"[v{len(overlay_filters) - 1}]"

            cmd = ["ffmpeg", "-y", "-loglevel", "error",
                   *inputs,
                   "-filter_complex", filter_chain,
                   "-map", last_output, "-map", "0:a?",
                   "-c:v", "libx264", "-pix_fmt", "yuv420p",
                   "-c:a", "copy", output_path]
            subprocess.run(cmd, capture_output=True, text=False, timeout=300)
            if not Path(output_path).exists():
                import shutil
                shutil.copy2(input_video, output_path)
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            logger.warning("画中画叠加跳过: %s", e)
            import shutil
            shutil.copy2(input_video, output_path)

    async def _mix_audio(
        self,
        input_video: str,
        segments: list[dict],
        output_path: str,
        audio_file_path: str = "",
        audio_bitrate: str = "192k",
        bgm_file_path: str = "",
    ) -> None:
        """混合音频到视频，支持多轨配音+背景音乐（侧链闪避）。"""
        # 找有效音频文件
        voice_path = audio_file_path if audio_file_path and Path(audio_file_path).exists() else None
        if not voice_path:
            for seg in segments:
                src = seg.get("source_path", "")
                if src and Path(src).exists():
                    voice_path = src
                    break

        # 尝试带 BGM 的侧链混音
        bgm_path = bgm_file_path if bgm_file_path and Path(bgm_file_path).exists() else None

        if voice_path and bgm_path:
            try:
                cmd = [
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-i", input_video, "-i", voice_path, "-i", bgm_path,
                    "-filter_complex",
                    "[1:a]loudnorm=I=-16:LRA=11:TP=-1.5[voice];"
                    "[2:a]volume=0.3[bgm];"
                    "[voice][bgm]amix=inputs=2:duration=first[aout]",
                    "-map", "0:v:0", "-map", "[aout]",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-b:v", "5M",
                    "-c:a", "aac", "-b:a", audio_bitrate,
                    "-shortest", output_path,
                ]
                subprocess.run(cmd, capture_output=True, text=False, timeout=300)
                if Path(output_path).exists():
                    logger.info("音频混合完成: 配音+背景音乐")
                    return
            except (FileNotFoundError, subprocess.TimeoutExpired) as e:
                logger.warning("音频混合失败(含BGM): %s", str(e)[:100])

        # 无 BGM：仅配音
        if voice_path:
            try:
                cmd = [
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-i", input_video, "-i", voice_path,
                    "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-b:v", "5M",
                    "-c:a", "aac", "-b:a", audio_bitrate,
                    "-map", "0:v:0", "-map", "1:a:0",
                    "-shortest", output_path,
                ]
                subprocess.run(cmd, capture_output=True, text=False, timeout=300)
                if Path(output_path).exists():
                    logger.info("音频混合完成: %s", voice_path)
                    return
            except (FileNotFoundError, subprocess.TimeoutExpired) as e:
                logger.warning("音频混合失败: %s", str(e)[:100])

        import shutil
        if not Path(input_video).exists():
            logger.warning("音频混合: 输入视频不存在 %s, 跳过", input_video)
            Path(output_path).write_text("")
            return
        shutil.copy2(input_video, output_path)

    @staticmethod
    def _get_duration(path: str) -> float:
        """获取视频时长。"""
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "json", path],
                capture_output=True, text=False, timeout=30,
            )
            import json
            data = json.loads(result.stdout)
            return float(data.get("format", {}).get("duration", 0))
        except Exception as e:
            logger.warning("获取视频时长失败: %s", str(e)[:100])
            return 0

    def cleanup(self) -> None:
        """清理临时工作目录。"""
        import shutil
        if self._work_dir.exists():
            shutil.rmtree(self._work_dir, ignore_errors=True)
