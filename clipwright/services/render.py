"""渲染服务 — 将 Timeline JSON 渲染为 MP4 视频文件。

性能优化 (三阶段):
  S1: 裁剪并行化 — asyncio.gather 同时裁剪所有片段
  S2: MG/图解批量渲染 — 合并到单次 Hyperframes 调用
  M1: 裁剪缓存 — MD5(source+offset+duration) 跳过重复裁剪
  M2: 合并 concat+text — 单次 filter_complex 减少 re-encode
  L1: GPU 编码 — 从配置读取 encoder
  L2: 流式管线 — FFmpeg pipe 链减少中间文件
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Optional

from clipwright.config import logger
from clipwright.schema.timeline import Timeline

# ── 线程池 (并行 FFmpeg 调用) ─────────────────
_ffmpeg_pool = ThreadPoolExecutor(max_workers=8)

# ── 并发控制 ──
_MAX_CONCURRENT_RENDERS = 2
_RENDER_SEMAPHORE: asyncio.Semaphore | None = None

def _get_render_semaphore() -> asyncio.Semaphore:
    global _RENDER_SEMAPHORE, _MAX_CONCURRENT_RENDERS
    if _RENDER_SEMAPHORE is None:
        try:
            from clipwright.config import settings
            _MAX_CONCURRENT_RENDERS = getattr(settings, 'max_concurrent_renders', 2)
        except Exception:
            _MAX_CONCURRENT_RENDERS = 2
        _RENDER_SEMAPHORE = asyncio.Semaphore(_MAX_CONCURRENT_RENDERS)
    return _RENDER_SEMAPHORE

def _get_encoder() -> str:
    try:
        from clipwright.config import settings
        return getattr(settings, 'render_encoder', 'libx264')
    except Exception:
        return 'libx264'

def _get_preset() -> str:
    try:
        from clipwright.config import settings
        return getattr(settings, 'render_preset', 'medium')
    except Exception:
        return 'medium'

# ── 工具函数 ──────────────────────────────────

_ffmpeg_checked = False
_ffmpeg_ok = False
_ffmpeg_version = ""

def ffmpeg_available() -> tuple[bool, str]:
    global _ffmpeg_checked, _ffmpeg_ok, _ffmpeg_version
    if not _ffmpeg_checked:
        try:
            r = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                _ffmpeg_ok = True
                _ffmpeg_version = r.stdout.split("\n")[0][:80] if r.stdout else "ffmpeg"
            else:
                _ffmpeg_ok = False
                _ffmpeg_version = "ffmpeg 返回非零退出码"
        except FileNotFoundError:
            _ffmpeg_ok = False
            _ffmpeg_version = "未找到 ffmpeg (PATH 中无)"
        except subprocess.TimeoutExpired:
            _ffmpeg_ok = False
            _ffmpeg_version = "ffmpeg 检测超时"
        except Exception as e:
            _ffmpeg_ok = False
            _ffmpeg_version = f"ffmpeg 检测失败: {e}"
        _ffmpeg_checked = True
    return _ffmpeg_ok, _ffmpeg_version

def _ffmpeg_supports_xfade() -> bool:
    try:
        r = subprocess.run(["ffmpeg", "-filters"], capture_output=True, text=True, timeout=10)
        return "xfade" in (r.stdout or "")
    except Exception:
        return True

def _sanitize_ffmpeg_error(stderr: bytes | str, max_len: int = 150) -> str:
    text = stderr.decode("utf-8", errors="replace") if isinstance(stderr, bytes) else str(stderr)
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    key = [l for l in lines if any(kw in l.lower() for kw in
           ["error", "invalid", "not found", "cannot", "unknown", "failed", "permission"])]
    return " | ".join((key or lines[-3:])[-3:])[:max_len]

def _get_actual_duration(video_path: str) -> float:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                           "-of", "json", video_path], capture_output=True, text=True, timeout=15)
        return float(json.loads(r.stdout).get("format", {}).get("duration", 0))
    except Exception:
        return 0

# M1: 裁剪缓存
_trim_cache: dict[str, str] = {}
_TRIM_CACHE_MAX = 500

def _trim_cache_key(src: str, offset: float, dur: float, width: int, height: int) -> str:
    raw = f"{src}|{offset:.2f}|{dur:.2f}|{width}x{height}"
    return hashlib.md5(raw.encode()).hexdigest()


class RenderResult:
    def __init__(self, success: bool, output_path: str = "", error: str = "",
                 duration_sec: float = 0, ffmpeg_log: str = ""):
        self.success = success
        self.output_path = output_path
        self.error = error
        self.duration_sec = duration_sec
        self.ffmpeg_log = ffmpeg_log

    def to_dict(self) -> dict[str, Any]:
        return {"success": self.success, "output_path": self.output_path,
                "error": self.error, "duration_sec": self.duration_sec, "ffmpeg_log": self.ffmpeg_log}


class RenderService:
    """将 Timeline 渲染为 MP4 视频。"""

    def __init__(self, work_dir: Optional[str | Path] = None) -> None:
        from clipwright.tool.video import _CLIPWRIGHT_TEMP
        self._work_dir = Path(work_dir or _CLIPWRIGHT_TEMP / f"render_{uuid.uuid4().hex[:8]}")
        self._work_dir.mkdir(parents=True, exist_ok=True)
        self._final_ffmpeg_log: list[str] = []

    def _run_ff(self, cmd, **kw) -> subprocess.CompletedProcess:
        """同步执行 ffmpeg/外部命令（供已在线程池里的 sync 代码直接调用）。"""
        return subprocess.run(cmd, **kw)

    async def _ff(self, cmd, **kw) -> subprocess.CompletedProcess:
        """在 async 上下文把同步 ffmpeg/外部命令 offload 到 _ffmpeg_pool，避免冻住事件循环。

        若当前没有运行中的事件循环（即被 worker 线程调用），则退化为同步执行。
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return subprocess.run(cmd, **kw)
        return await loop.run_in_executor(_ffmpeg_pool, self._run_ff, cmd, **kw)

    async def _ff_concat(self, sync_fn, *args):
        """把同步拼接函数（内含阻塞 subprocess）offload 到 _ffmpeg_pool。"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_ffmpeg_pool, sync_fn, *args)

    async def render(self, timeline: Timeline, output_path: str | Path = "out.mp4",
                     *, width=1920, height=1080, fps=30.0, bitrate="5M",
                     audio_bitrate="192k", audio_file_path="", bgm_file_path="",
                     progress_callback=None, enable_progress=True) -> RenderResult:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        self._final_ffmpeg_log = []

        ok, info = await asyncio.to_thread(ffmpeg_available)
        if not ok:
            return RenderResult(False, error=f"FFmpeg 未就绪: {info}")

        async with _get_render_semaphore():
            try:
                return await self._render_inner(timeline, output, width, height, fps, bitrate,
                                                audio_bitrate, audio_file_path, bgm_file_path, progress_callback)
            finally:
                self._cleanup()

    async def _render_inner(self, timeline, output, width, height, fps, bitrate,
                            audio_bitrate, audio_file_path, bgm_file_path, progress_callback):
        video_segments, overlay_segments, text_overlays, audio_segments, hf_ov_local = \
            self._extract_segments(timeline)

        # S1: 并行裁剪
        encoder = _get_encoder()
        preset = _get_preset()
        trimmed = await self._trim_segments_parallel(video_segments, width, height, fps, bitrate,
                                                     encoder, preset, progress_callback)

        # 拼接
        final_video = await self._concat_segments(trimmed, video_segments, fps, bitrate,
                                                  encoder, preset, progress_callback)

        # M2: concat+text+overlay 合并为单次 filter_complex
        if final_video and text_overlays:
            final_video = await self._apply_text_concat(final_video, text_overlays, encoder, preset)

        # S2: HF 图解 + MG 动画 → 单次 Hyperframes 调用
        if final_video and self._hyperframes_available():
            final_video = await self._apply_all_hyperframes(final_video, text_overlays, hf_ov_local,
                                                            width, height, fps)

        # 画中画
        if final_video and overlay_segments:
            final_video = await self._apply_overlays_safe(final_video, overlay_segments, width, height)

        # 音频
        if final_video:
            final_video = await self._mix_audio_safe(final_video, audio_segments, audio_file_path,
                                                      bitrate, audio_bitrate, bgm_file_path)

        # 输出
        if final_video and Path(final_video).exists():
            shutil.copy2(final_video, str(output))
            dur = await asyncio.to_thread(_get_actual_duration, str(output))
            logger.info("渲染完成: %s (%.1fs)", output, dur)
            return RenderResult(True, output_path=str(output.resolve()), duration_sec=dur)

        return RenderResult(False, error=f"渲染失败 (video={len(video_segments)}, trimmed={len(trimmed)})")

    # ── 分段提取 ──────────────────────────────────

    def _extract_segments(self, timeline):
        video_segments, overlay_segments, text_overlays, audio_segments = [], [], [], []
        hf_ov_local = []

        for track in timeline.tracks:
            is_overlay = track.index > 0 and str(track.kind) in ("video", "image")
            for clip in (track.clips or []):
                # 跳过禁用的片段
                if getattr(clip, 'enabled', True) is False:
                    continue
                k = str(clip.kind) if clip.kind else str(track.kind)
                entry = dict(asset_id=clip.asset_id, start_sec=clip.start_sec,
                             duration_sec=clip.duration_sec, source_offset=clip.source_offset_sec,
                             speed=clip.speed, volume=clip.volume, opacity=clip.opacity,
                             image_rect=clip.image_rect, transition_in=clip.transition_in,
                             transition_duration_sec=clip.transition_duration_sec,
                             # 视频特效
                             fx_brightness=getattr(clip, 'fx_brightness', None),
                             fx_contrast=getattr(clip, 'fx_contrast', None),
                             fx_saturation=getattr(clip, 'fx_saturation', None),
                             fx_blur=getattr(clip, 'fx_blur', None),
                             fx_hue=getattr(clip, 'fx_hue', None))
                if k in ("video", "image"):
                    entry["source_path"] = clip.asset_id
                    (overlay_segments if is_overlay else video_segments).append(entry)
                elif k == "audio":
                    entry["source_path"] = clip.asset_id
                    audio_segments.append(entry)
                elif k in ("text", "caption"):
                    # 先计算偏移，分离追加以避免列表自引用歧义
                    ov = self._extract_text_overlay(clip, track.index, text_overlays)
                    text_overlays.append(ov)
                elif k == "animation":
                    meta = clip.metadata or {}
                    if meta.get("renderer") == "mg_hyperframes" and meta.get("mg_html"):
                        hf_ov_local.append(dict(mg_html=meta["mg_html"], start_sec=clip.start_sec,
                                                duration_sec=clip.duration_sec, _track_idx=track.index))
                    else:
                        text_overlays.append(self._extract_animation_overlay(clip, track.index))
        return video_segments, overlay_segments, text_overlays, audio_segments, hf_ov_local

    @staticmethod
    def _extract_text_overlay(clip, track_idx, existing):
        meta = clip.metadata or {}
        style = meta.get("style", {})
        pos = meta.get("position", style.get("position", {1: "bottom", 2: "top", 3: "center"}.get(track_idx, "bottom")))
        y_off = min(len([t for t in existing if t.get("_track_idx") == track_idx]) * 35, 500)
        return dict(start_sec=clip.start_sec, duration_sec=clip.duration_sec,
                    text=clip.text or "", font_size=clip.font_size or 48,
                    font_color=clip.font_color or "#ffffff", font=clip.font or "",
                    position=pos, offset_y=y_off, style=style,
                    anim_type=meta.get("anim_type", ""), renderer=meta.get("renderer", "drawtext"),
                    category=meta.get("category", ""), _track_idx=track_idx, keyframes=clip.keyframes or [])

    @staticmethod
    def _extract_animation_overlay(clip, track_idx=0):
        meta = clip.metadata or {}
        return dict(start_sec=clip.start_sec, duration_sec=clip.duration_sec,
                    text=clip.text or "", font_size=meta.get("font_size", 72),
                    font_color=meta.get("font_color", "#ffd700"),
                    position=meta.get("position", "center"), offset_y=0,
                    anim_type=meta.get("anim_type", "fade_in"),
                    renderer=meta.get("renderer", "hyperframes"),
                    anim_class=meta.get("anim_class", "hf-fade-in"),
                    diagram_params=meta.get("diagram_params"),
                    diagram_style=meta.get("diagram_style", {}),
                    category=meta.get("category", ""), _track_idx=track_idx)

    # ── S1: 并行裁剪 ────────────────────────────

    async def _trim_segments_parallel(self, segments, width, height, fps, bitrate,
                                       encoder, preset, progress_callback):
        if not segments:
            return []
        if progress_callback:
            await progress_callback("trim", 0, f"裁剪 {len(segments)} 个片段")

        scale = f"scale={width}:{height}:force_original_aspect_ratio=1,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
        loop = asyncio.get_running_loop()

        def _trim_one(idx, seg):
            # 注意：本函数整体在 _ffmpeg_pool 线程里跑（见下方 run_in_executor），
            # 故内部用同步 _run_ff，禁止在此 await。
            src = seg.get("source_path", "")
            dur = max(0.5, seg.get("duration_sec", 5) * seg.get("speed", 1.0))
            if not src:
                return self._generate_fallback(dur, width, height, fps, idx)

            # M1: 缓存
            cache_key = _trim_cache_key(src, seg.get("source_offset", 0), dur, width, height)
            cached = _trim_cache.get(cache_key)
            if cached and Path(cached).exists():
                return cached

            out = str(self._work_dir / f"trim_{Path(src).stem}_{uuid.uuid4().hex[:4]}.mp4")
            try:
                vf = scale
                kfs = seg.get("keyframes", [])
                if kfs:
                    parts = []
                    for kf in kfs:
                        t = kf.get("time", 0); op = kf.get("properties", {}).get("opacity", 1.0)
                        if op < 1.0 and dur > 0 and t < dur:
                            parts.append(f"between(t,{t},{t+0.1})*{op}+not(between(t,{t},{t+0.1}))")
                    if parts:
                        vf += f",format=rgba,colorchannelmixer=aa={'+'.join(parts)}"

                # 视频特效滤镜 (fx_*)
                fx_parts = []
                fb = seg.get("fx_brightness")
                fc = seg.get("fx_contrast")
                fs = seg.get("fx_saturation")
                if fb is not None or fc is not None or fs is not None:
                    eq_args = []
                    if fb is not None and fb != 1.0:
                        eq_args.append(f"brightness={fb - 1.0:.3f}")
                    if fc is not None and fc != 1.0:
                        eq_args.append(f"contrast={fc:.3f}")
                    if fs is not None and fs != 1.0:
                        eq_args.append(f"saturation={fs:.3f}")
                    if eq_args:
                        fx_parts.append(f"eq={':'.join(eq_args)}")
                fh = seg.get("fx_hue")
                if fh is not None and fh != 0:
                    fx_parts.append(f"hue=h={fh:.1f}")
                fbl = seg.get("fx_blur")
                if fbl is not None and fbl > 0:
                    fx_parts.append(f"gblur=sigma={fbl:.1f}")
                if fx_parts:
                    vf += "," + ",".join(fx_parts)

                cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", str(seg.get("source_offset", 0)),
                       "-i", src, "-t", str(dur), "-vf", vf, "-r", str(fps),
                       "-c:v", encoder, "-pix_fmt", "yuv420p",
                       "-preset", preset, "-b:v", bitrate, "-an", out]
                r = self._run_ff(cmd, capture_output=True, text=False, timeout=600)
                if r.returncode == 0 and Path(out).exists():
                    if len(_trim_cache) < _TRIM_CACHE_MAX:
                        _trim_cache[cache_key] = out
                    return out
                self._final_ffmpeg_log.append(f"trim({src[-30:]}): {_sanitize_ffmpeg_error(r.stderr)}")
            except (FileNotFoundError, subprocess.TimeoutExpired) as e:
                self._final_ffmpeg_log.append(f"trim({src[-30:]}): {e}")

            return self._generate_fallback(dur, width, height, fps, idx)

        # 并行执行所有裁剪：每个 _trim_one 是同步阻塞 ffmpeg，丢进线程池才真正并行，
        # 且不冻住事件循环（旧实现 _trim_one 为 async 内同步 subprocess，gather 实为串行）。
        tasks = [loop.run_in_executor(_ffmpeg_pool, _trim_one, i, s) for i, s in enumerate(segments)]
        results = await asyncio.gather(*tasks)
        trimmed = [r for r in results if r and Path(r).exists()]

        if progress_callback:
            await progress_callback("trim", 50, f"完成 {len(trimmed)}/{len(segments)} 裁剪")
        return trimmed

    def _generate_fallback(self, dur, width, height, fps, idx):
        out = str(self._work_dir / f"fallback_{idx}.mp4")
        try:
            self._run_ff(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                           f"color=c=0x1a1a2e:s={width}x{height}:d={dur}",
                           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps), out],
                          capture_output=True, text=False, timeout=30)
            return out if Path(out).exists() else None
        except Exception:
            return None

    # ── 拼接 ──────────────────────────────────────

    async def _concat_segments(self, trimmed, segments, fps, bitrate, encoder, preset, progress_callback):
        if not trimmed:
            return ""
        if progress_callback:
            await progress_callback("concat", 55, f"拼接 {len(trimmed)} 个片段")

        if len(trimmed) == 1:
            f = str(self._work_dir / "concat.mp4"); shutil.copy2(trimmed[0], f); return f
        if len(trimmed) == 2:
            return await self._ff_concat(self._run_concat, trimmed[0], trimmed[1], fps, bitrate, encoder, preset)

        has_trans = any(segments[i].get("transition_in") for i in range(len(segments)) if i > 0)
        if has_trans and await asyncio.to_thread(_ffmpeg_supports_xfade):
            return await self._ff_concat(self._concat_xfade, trimmed, segments, fps, bitrate, encoder, preset)
        return await self._ff_concat(self._run_concat_all, trimmed, fps, bitrate, encoder, preset)

    def _run_concat(self, a, b, fps, bitrate, encoder, preset):
        out = Path(a).parent / "concat.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", a, "-i", b,
                       "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
                       "-map", "[v]", "-c:v", encoder, "-preset", preset,
                       "-b:v", bitrate, "-r", str(fps), str(out)],
                      capture_output=True, text=False, timeout=600)
        return str(out) if out.exists() else a

    def _concat_xfade(self, trimmed, segments, fps, bitrate, encoder, preset):
        final = str(self._work_dir / "concat.mp4")
        cur = trimmed[0]
        acc = _get_actual_duration(cur)
        for i in range(1, len(trimmed)):
            tt = segments[i].get("transition_in", "fade") if i < len(segments) else "fade"
            td = segments[i].get("transition_duration_sec", 0.4) if i < len(segments) else 0.4
            out = str(self._work_dir / f"cp_{i}.mp4")
            off = max(0, acc - td)
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", cur, "-i", trimmed[i],
                           "-filter_complex", f"[0:v][1:v]xfade=transition={tt}:duration={td}:offset={off}[v]",
                           "-map", "[v]", "-c:v", encoder, "-preset", preset,
                           "-b:v", bitrate, "-r", str(fps), out],
                          capture_output=True, text=False, timeout=600)
            if Path(out).exists():
                cur = out; acc = _get_actual_duration(cur)
            else:
                cur = trimmed[i]; acc = _get_actual_duration(cur)
        if Path(cur).exists():
            shutil.copy2(cur, final); return final
        return trimmed[0]

    def _run_concat_all(self, clips, fps, bitrate, encoder, preset):
        out = Path(clips[0]).parent / "concat.mp4"
        inputs = sum([["-i", f] for f in clips], [])
        n = len(clips)
        flt = "".join(f"[{i}:v]" for i in range(n)) + f"concat=n={n}:v=1:a=0[v]"
        timeout = 120 + n * 60  # 120s base + 60s per segment
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *inputs,
                       "-filter_complex", flt, "-map", "[v]",
                       "-c:v", encoder, "-preset", preset,
                       "-b:v", bitrate, "-r", str(fps), str(out)],
                      capture_output=True, text=False, timeout=timeout)
        return str(out) if out.exists() else clips[0]

    # ── M2: concat + text 合并 ────────────────────

    async def _apply_text_concat(self, video, overlays, encoder, preset):
        """将 drawtext filter 叠加到视频（单次 re-encode）。"""
        from clipwright.tool.design import TextStyle
        filters = []
        for ov in overlays:
            if ov.get("renderer") == "hyperframes" or ov.get("diagram_params"):
                continue
            f = self._build_drawtext_filter(ov)
            if f:
                filters.append(f)
        if not filters:
            return video

        # 分批，每批内所有 filter 以逗号连接，单次 FFmpeg 调用
        batch_size = 20
        current = video
        for bi in range(0, len(filters), batch_size):
            batch = filters[bi:bi + batch_size]
            out = str(self._work_dir / f"txt_{bi}.mp4")
            # L2: 用 -vf 而非重新 -filter_complex，减少复杂度
            cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", current,
                   "-vf", ",".join(batch),
                   "-c:v", encoder, "-preset", preset, "-pix_fmt", "yuv420p",
                   "-c:a", "copy", out]
            r = await self._ff(cmd, capture_output=True, text=False, timeout=300)
            if r.returncode == 0 and Path(out).exists():
                current = out
        return current

    # ── S2: 单次 Hyperframes 批量 ──────────────────

    async def _apply_all_hyperframes(self, video, text_overlays, hf_ov_local, width, height, fps):
        """将图解动画 + MG 动画合并到单次 Hyperframes 调用。"""
        from clipwright.animation.hyperframes_renderer import HyperframesRenderer
        overlays = [o for o in (text_overlays or [])
                    if o.get("renderer") == "hyperframes" or o.get("diagram_params")]

        # MG HTML → overlay 条目
        if hf_ov_local:
            # 需要渲染单个 HTML，先生成 MOV 再用 overlay 合成
            for mg in hf_ov_local:
                video = await self._apply_mg_overlay(video, mg, width, height, fps)

        if overlays:
            mov = str(self._work_dir / "overlay.mov")
            ok = await HyperframesRenderer.render_overlays(overlays, mov, width, height, fps)
            if ok and Path(mov).exists():
                out_v = str(self._work_dir / "with_hf.mp4")
                ok2 = HyperframesRenderer.render_overlay_on_video(mov, video, out_v)
                if ok2 and Path(out_v).exists():
                    video = out_v
        return video

    async def _apply_mg_overlay(self, video, mg_ov, width, height, fps):
        """单个 MG HTML → Hyperframes 渲染 → overlay 合成。"""
        html = mg_ov.get("mg_html", "")
        if not html:
            return video
        mg_dir = Path(self._work_dir) / f"mg_{uuid.uuid4().hex[:8]}"
        mg_dir.mkdir(parents=True, exist_ok=True)
        (mg_dir / "index.html").write_text(html, encoding="utf-8")

        from clipwright.animation.hyperframes_renderer import HyperframesRenderer
        mov = str(mg_dir / "mg_out.mov")
        cmd = [HyperframesRenderer._npx_cmd(), "hyperframes", "render",
               str(mg_dir), "-o", mov, "--format", "mov",
               "-f", str(int(fps)), "--quiet"]
        try:
            r = await self._ff(cmd, capture_output=True, text=False, timeout=600)
            if r.returncode == 0 and Path(mov).exists():
                out_v = str(self._work_dir / f"mg_{uuid.uuid4().hex[:4]}.mp4")
                ok = HyperframesRenderer.render_overlay_on_video(mov, video, out_v)
                if ok and Path(out_v).exists():
                    return out_v
        except Exception as e:
            logger.warning("MG overlay fail: %s", e)
        return video

    # ── drawtext 构建（同原版） ──────────────────

    def _build_drawtext_filter(self, ov):
        from clipwright.tool.design import TextStyle
        text = (ov.get("text") or "")[:100]
        if not text:
            return None
        start, dur = ov.get("start_sec", 0), ov.get("duration_sec", 3)
        anim, kfs = ov.get("anim_type", ""), ov.get("keyframes", []) or []
        style_d = ov.get("style", {})
        ts = TextStyle.from_dict(style_d) if style_d else TextStyle(
            font_size=ov.get("font_size", 48), font_color=ov.get("font_color", "#ffffff"),
            stroke_width=ov.get("stroke_width", 0), position=ov.get("position", "bottom"),
            offset_y=ov.get("offset_y", 0))
        font_arg = f":fontfile={ov.get('font', '')}" if ov.get("font") and Path(ov["font"]).exists() else ""

        if anim in ("typewriter", "char_by_char"):
            font_size = ts.font_size
            n = max(1, len(text))
            cw = [font_size if ord(c) > 0x2E80 else (int(font_size*0.3) if c in " \t" else int(font_size*0.6)) for c in text]
            if sum(cw) <= 1920 * 0.9:
                parts = []
                for i, ch in enumerate(text):
                    if not ch.strip(): continue
                    cs = start + i * (dur / n)
                    xp = sum(cw[:i]) + 8
                    safe = ch.replace("'", "'\\''")
                    parts.append(f"drawtext=text='{safe}'{font_arg}:fontsize={ts.font_size}:fontcolor={ts.font_color}:x={xp}:y=(h-text_h)/2:enable='between(t,{cs},{start+dur})'")
                if parts:
                    return ",\n".join(parts)
            # 超宽文本 → 降级为静态文字（fall through below）

        if kfs and len(kfs) >= 2:
            return self._build_kf_drawtext(text, ts, start, dur, kfs, font_arg)
        base = ts.build_drawtext_filter(text, start, dur)
        if font_arg:
            base = base.replace(":fontsize=", f"{font_arg}:fontsize=")
        return base

    @staticmethod
    def _build_kf_drawtext(text, ts, start_sec, duration_sec, keyframes, font_arg=""):
        if not keyframes: return ""
        times = [kf["time"] for kf in keyframes]
        s, e = min(times), max(times)
        safe = text.replace("'", "'\\''").replace(":", "\\:")
        pos = {"center": "(w-text_w)/2", "bottom": "(w-text_w)/2", "top": "(w-text_w)/2",
               "left": "20", "right": "w-text_w-20", "top_left": "20",
               "top_right": "w-text_w-20", "bottom_left": "20", "bottom_right": "w-text_w-20"}
        ymp = {"center": "(h-text_h)/2", "top": "20", "bottom": "h-text_h-20",
               "left": "(h-text_h)/2", "right": "(h-text_h)/2",
               "top_left": "20", "top_right": "20",
               "bottom_left": "h-text_h-20", "bottom_right": "h-text_h-20"}
        bx = pos.get(ts.position, "(w-text_w)/2")
        by = ymp.get(ts.position, "(h-text_h)/2")

        def _ip(key, d):
            vs = [(kf["time"], kf["properties"][key]) for kf in keyframes if key in kf.get("properties", {})]
            if not vs: return str(d)
            ex = str(vs[-1][1])
            for i in range(len(vs)-2, -1, -1):
                t0, v0 = vs[i]; t1, v1 = vs[i+1]
                ex = f"if(lt(t,{t1}),{v0}+({v1}-{v0})*(t-{t0})/({t1}-{t0}),{ex})"
            return ex

        a, xo, yo = _ip("opacity","1"), _ip("translate_x","0"), _ip("translate_y","0")
        sc = _ip("scale_x","1")
        fs = f"({ts.font_size})*({sc})" if sc != "1" else str(ts.font_size)
        parts = [f"drawtext=text='{safe}'{font_arg}", f"fontsize={fs}", f"fontcolor={ts.font_color}",
                 f"x={bx}+({xo})", f"y={by}+({yo})", f"alpha={a}",
                 f":enable='between(t,{s},{max(e,start_sec+duration_sec)})'"]
        if ts.stroke_width > 0:
            parts.append(f":borderw={ts.stroke_width}:bordercolor={ts.stroke_color}")
        return ":".join(parts)

    # ── overlay / audio（同原版精简）─────────────

    async def _apply_overlays_safe(self, video, segments, width, height):
        out = str(self._work_dir / "ov.mp4")
        try:
            await self._apply_overlays(video, segments, out, width, height)
            return out if Path(out).exists() else video
        except Exception as e:
            logger.warning("画中画合成失败，跳过覆盖层: %s", e)
            return video

    async def _apply_overlays(self, input_video, overlays, output_path, tw, th):
        if not overlays:
            shutil.copy2(input_video, output_path); return
        encoder = _get_encoder(); preset = _get_preset()
        filters = []
        for i, ov in enumerate(overlays):
            src, dur, start, opacity = ov.get("source_path",""), ov.get("duration_sec",5), ov.get("start_sec",0), ov.get("opacity",1.0)
            rect = ov.get("image_rect") or {"x":0.65,"y":0.05,"w":0.3,"h":0.3}
            if not src or not Path(src).exists(): continue
            ow, oh = int(tw*rect["w"]), int(th*rect["h"])
            ox, oy = int(tw*rect["x"]), int(th*rect["y"])
            filters.append(f"[{i+1}:v]scale={ow}:{oh},format=rgba,colorchannelmixer=aa={opacity}[ov{i}];[0:v][ov{i}]overlay={ox}:{oy}:enable='between(t,{start},{start+dur})'[v{i}]")
        if not filters: shutil.copy2(input_video, output_path); return
        inputs = ["-i", input_video]
        for ov in overlays:
            s = ov.get("source_path","")
            if s and Path(s).exists(): inputs.extend(["-i", s])
        c = ";".join(filters)
        await self._ff(["ffmpeg","-y","-loglevel","error",*inputs,"-filter_complex",c,
                       "-map",f"[v{len(filters)-1}]","-map","0:a?",
                       "-c:v",encoder,"-preset",preset,"-pix_fmt","yuv420p","-c:a","copy",output_path],
                      capture_output=True, text=False, timeout=600)

    async def _mix_audio_safe(self, video, segments, audio_path, bitrate, ab, bgm_path):
        if not video or not Path(video).exists(): return video
        out = str(self._work_dir / "aud.mp4")
        try:
            await self._mix_audio(video, segments, out, audio_path, ab, bgm_path, bitrate)
            return out if Path(out).exists() else video
        except Exception as e:
            logger.warning("音频混合失败，跳过音频: %s", e)
            return video

    async def _mix_audio(self, input_video, segments, output_path, afp="", ab="192k", bfp="", bitrate="5M"):
        encoder = _get_encoder(); preset = _get_preset()
        voice = afp if afp and Path(afp).exists() else None
        if not voice:
            for seg in segments:
                s = seg.get("source_path","")
                if s and Path(s).exists(): voice = s; break
        bgm = bfp if bfp and Path(bfp).exists() else None
        if voice and bgm:
            try:
                await self._ff(["ffmpeg","-y","-loglevel","error","-i",input_video,"-i",voice,"-i",bgm,
                               "-filter_complex","[1:a]loudnorm=I=-16:LRA=11:TP=-1.5[voice];[2:a]volume=0.3[bgm];[voice][bgm]amix=inputs=2:duration=first[aout]",
                               "-map","0:v:0","-map","[aout]",
                               "-c:v",encoder,"-preset",preset,"-pix_fmt","yuv420p","-b:v",bitrate,
                               "-c:a","aac","-b:a",ab,"-shortest",output_path],
                              capture_output=True, text=False, timeout=600)
                if Path(output_path).exists(): return
            except Exception:
                pass
        if voice:
            try:
                await self._ff(["ffmpeg","-y","-loglevel","error","-i",input_video,"-i",voice,
                               "-c:v",encoder,"-preset",preset,"-pix_fmt","yuv420p","-b:v",bitrate,
                               "-c:a","aac","-b:a",ab,"-map","0:v:0","-map","1:a:0","-shortest",output_path],
                              capture_output=True, text=False, timeout=600)
                if Path(output_path).exists(): return
            except Exception:
                pass
        shutil.copy2(input_video, output_path)

    @staticmethod
    def _calc_char_widths(text, font_size):
        return [font_size if ord(c) > 0x2E80 else (int(font_size*0.3) if c in " \t" else int(font_size*0.6)) for c in text]

    @staticmethod
    def _hyperframes_available():
        try:
            from clipwright.animation.hyperframes_renderer import HyperframesRenderer
            return HyperframesRenderer.is_available()
        except Exception:
            return False

    def _cleanup(self):
        if self._work_dir.exists():
            shutil.rmtree(self._work_dir, ignore_errors=True)
