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
import contextvars
import hashlib
import os
import json
import re
import shutil
import subprocess
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Optional

from clipwright.config import logger
from clipwright.schema.timeline import Timeline
from clipwright.tool.design import color_to_drawtext, escape_drawtext_text
from clipwright.utils.keyframes_expr import normalize_keyframe_times, property_expression

# 任务 31：字幕新样式字段（与 schema/timeline.py Clip 对齐）。
# 渲染时直接从 Clip 读取并合并进传给 TextStyle.from_dict 的 style dict；
# clip.metadata.style 仅作回退（clip 显式字段优先）。
_CLIP_STYLE_FIELDS = (
    "font_weight", "font_italic", "letter_spacing", "stroke_width", "stroke_color",
    "shadow_x", "shadow_y", "shadow_color", "shadow_blur", "glow_color", "glow_width",
)

# ── 线程池 (并行 FFmpeg 调用) ─────────────────
# 生产加固 1.7: 池大小与 CPU 联动（封顶 8），所有 ffmpeg 调用（trim/concat/overlay）
# 共享同一池 → 天然全局并发上限，配合 _MAX_CONCURRENT_RENDERS 防低配机 OOM。
import os as _os
# 生产加固 1.7: 线程池最大并发限制为 min(8, cpu_count) 并且受限于 max_concurrent_renders
_MAX_CONCURRENT_RENDERS = 2
try:
    from clipwright.config import settings
    _MAX_CONCURRENT_RENDERS = getattr(settings, 'max_concurrent_renders', 2)
except Exception:
    pass
_ffmpeg_pool = ThreadPoolExecutor(max_workers=min(_MAX_CONCURRENT_RENDERS * 2, max(4, min(8, _os.cpu_count() or 8))))

# ── 审计 P0 修复：渲染任务取消 + 僵尸进程清理 ─────────────────
# cancel_id（通常为渲染 task_id）→ 活跃 ffmpeg 子进程句柄；
# 取消时 terminate 全部活跃进程并标记，_run_ff 在进程返回后抛 RenderCancelledError。
_CURRENT_CANCEL_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "clipwright_render_cancel_id", default=None
)
_CANCEL_LOCK = threading.Lock()
_CANCELLED_IDS: set[str] = set()
_ACTIVE_PROCS: dict[str, list[subprocess.Popen]] = {}


class RenderCancelledError(Exception):
    """渲染任务被用户取消。"""


def cancel_render(task_id: str) -> bool:
    """请求取消渲染：标记 + terminate 当前活跃 ffmpeg 子进程（幂等）。"""
    with _CANCEL_LOCK:
        _CANCELLED_IDS.add(task_id)
        procs = list(_ACTIVE_PROCS.get(task_id, []))
    for p in procs:
        try:
            p.terminate()
        except Exception:
            pass
    return True


def is_render_cancelled(task_id: str | None) -> bool:
    if not task_id:
        return False
    with _CANCEL_LOCK:
        return task_id in _CANCELLED_IDS


def clear_cancel_state(task_id: str) -> None:
    with _CANCEL_LOCK:
        _CANCELLED_IDS.discard(task_id)
        _ACTIVE_PROCS.pop(task_id, None)


def _track_proc(task_id: str | None, proc: subprocess.Popen) -> None:
    if not task_id:
        return
    with _CANCEL_LOCK:
        _ACTIVE_PROCS.setdefault(task_id, []).append(proc)


def _untrack_proc(task_id: str | None, proc: subprocess.Popen) -> None:
    if not task_id:
        return
    with _CANCEL_LOCK:
        lst = _ACTIVE_PROCS.get(task_id)
        if lst and proc in lst:
            lst.remove(proc)


# 活跃子进程集合（含 npx→chrome 链），供 cancel 时统一进程树击杀
_child_procs: set = set()


def _kill_process_tree(proc) -> None:
    """M5: 杀死进程及其全部子进程（Chrome → node → npx 链条）。

    Windows: taskkill /T /F；POSIX: 进程组 SIGKILL。
    """
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True, timeout=10,
            )
        except Exception:
            proc.kill()
    else:
        import signal
        try:
            import os as _os
            _os.killpg(_os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()


def run_tracked_ff(cmd, **kw) -> subprocess.CompletedProcess:
    """审计 P0 修复：带取消跟踪的同步子进程执行（模块级，供 render/hyperframes 共用）。

    改用 Popen 并登记进程句柄；cancel_render() 会 terminate 活跃进程；
    取消标记存在时抛 RenderCancelledError，避免僵尸 ffmpeg 继续烧 CPU。
    """
    cid = _CURRENT_CANCEL_ID.get()
    if is_render_cancelled(cid):
        raise RenderCancelledError(cid)
    timeout = kw.pop("timeout", None)
    use_text = kw.pop("text", False)
    capture = kw.pop("capture_output", False)
    check = kw.pop("check", False)
    stdout_arg = kw.pop("stdout", subprocess.PIPE if capture else None)
    stderr_arg = kw.pop("stderr", subprocess.PIPE if capture else None)
    proc = subprocess.Popen(
        cmd, stdout=stdout_arg, stderr=stderr_arg, text=use_text, **kw
    )
    # Windows 下用 CREATE_NEW_PROCESS_GROUP + taskkill /T 杀进程树
    # （npx → node → chrome-headless-shell 链条只杀直接子进程会留孤儿 Chrome）
    if os.name == "nt":
        _child_procs.add(proc)
    _track_proc(cid, proc)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc)
        out, err = proc.communicate()
        raise
    finally:
        _untrack_proc(cid, proc)
        _child_procs.discard(proc)
    if is_render_cancelled(cid):
        raise RenderCancelledError(cid)
    if check and proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd, out, err)
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)

# ── 并发控制 ──
_MAX_CONCURRENT_RENDERS = 2


def _split_long_text(text: str, limit: int = 100) -> list[str]:
    """生产加固 1.6：长文本按标点/空格拆成 ≤limit 字的段（替代静默硬截断）。

    优先在 ，。！？；、/空格 等边界切分（过半宽即可切），无标点时按字数硬切。
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    buf = ""
    for ch in text:
        buf += ch
        if (ch in "，。！？；、,!?; " and len(buf) >= limit // 2) or len(buf) >= limit:
            if buf.strip():
                chunks.append(buf.strip())
            buf = ""
    if buf.strip():
        chunks.append(buf.strip())
    return chunks
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

# ── GPU 编码智能探测（Bug3）──────────────────
_encoder_resolved: str | None = None

def reset_render_caches() -> None:
    """配置热更新后失效编码器探测与信号量缓存（下次使用时按新配置重建）。

    注意：``_ffmpeg_pool`` 线程池大小在导入时固定，不随此重建 ——
    ``max_concurrent_renders`` 仍需重启才完全生效（UI 已标注）。
    """
    global _encoder_resolved, _RENDER_SEMAPHORE
    _encoder_resolved = None
    _RENDER_SEMAPHORE = None

def _nvenc_runtime_probe() -> bool:
    """真实 NVENC 编码探针：探测发现 h264_nvenc 后，再做一次微缩真实编码。

    仅凭 ``ffmpeg -encoders`` 含 h264_nvenc 并不代表可用——若 NVIDIA 驱动版本
    低于 ffmpeg 构建要求（如 nvenc API 13.1 需要驱动 ≥610.00），运行时
    ``Error while opening encoder`` 会拖垮整次渲染（所有 trim 失败）。此处用
    320x240 0.2s 合成图真实编码一次，编码成功才算可用。结果不缓存（探测本身
    开销极小，且需反映当前驱动状态）。

    ⚠ 历史：探针帧尺寸曾用 64x64——低于 NVENC 最小支持尺寸（NVENC 对
    H.264 的 H 尺寸要求 ≥ 2 个 16px 宏块行，实际最小高 120），导致驱动返回
    ``invalid param (8)`` 被误判为"驱动版本过低"而错误回退 libx264。
    改用 320x240（高于 NVENC 最小支持尺寸）后，驱动正常时探针通过。
    """
    probe_out = _CLIPWRIGHT_TEMP / "nvenc_probe.mp4"
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
             "-i", "color=c=black:s=320x240:d=0.2",
             "-c:v", "h264_nvenc", "-pix_fmt", _current_pix_fmt(), str(probe_out)],
            capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        ok = r.returncode == 0 and probe_out.exists() and probe_out.stat().st_size > 0
        if not ok:
            stderr_head = "\n".join((r.stderr or "").strip().splitlines()[:2]) or "rc!=0"
            logger.warning(
                "[Render] nvenc 运行时编码探测失败（探针帧尺寸过小或驱动版本过旧）：\n%s",
                stderr_head,
            )
        return ok
    except Exception as e:
        logger.warning("[Render] nvenc 运行时编码探测异常: %s", e)
        return False
    finally:
        try:
            probe_out.unlink(missing_ok=True)
        except Exception:
            pass


def _resolve_encoder() -> str:
    """运行时探测可用的视频编码器（结果缓存）。

    判定规则（不硬编码假设）：
    1. ``ffmpeg -encoders`` 输出含 ``h264_nvenc``（编码器存在）
    2. ``nvidia-smi`` 返回 0 且输出含 NVIDIA GPU（驱动就绪）
    3. 真实 NVENC 编码探针通过（驱动版本满足 ffmpeg 构建要求，能实际编码）
    三条全满足 → h264_nvenc + 硬件解码；任一不满足 → 回退 libx264 并记录原因。
    探测结果缓存到模块级变量，并打印 [Render] encoder=... reason=... 日志。
    """
    global _encoder_resolved
    if _encoder_resolved:
        return _encoder_resolved

    # 配置显式指定（render_encoder 非空）→ 尊重配置，跳过探测
    try:
        from clipwright.config import settings
        configured = getattr(settings, 'render_encoder', '')
    except Exception:
        configured = ''
    if configured and configured.strip():
        _encoder_resolved = configured.strip()
        logger.info("[Render] encoder=%s reason=config", _encoder_resolved)
        return _encoder_resolved

    reason = "config 未指定"
    nvenc = False
    try:
        r = subprocess.run(["ffmpeg", "-encoders"], capture_output=True, text=True,
                           timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        nvenc = "h264_nvenc" in (r.stdout or "")
    except Exception:
        pass

    gpu = False
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                           capture_output=True, text=True, timeout=15,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        gpu = r.returncode == 0 and bool((r.stdout or "").strip())
    except Exception:
        pass

    if nvenc and gpu and _nvenc_runtime_probe():
        _encoder_resolved = "h264_nvenc"
        reason = "nvenc encoder + NVIDIA GPU + 运行时探针通过"
    elif nvenc and gpu:
        _encoder_resolved = "libx264"
        reason = "nvenc 存在但运行时编码失败（驱动版本过低）→ 回退 CPU"
    else:
        _encoder_resolved = "libx264"
        reason = f"nvenc={nvenc} gpu={gpu} → 回退 CPU"
    logger.info("[Render] encoder=%s reason=%s", _encoder_resolved, reason)
    return _encoder_resolved

def _hwaccel_args(encoder: str) -> list[str]:
    """NVENC 路径输入解码前缀；libx264 返回空列表。"""
    if encoder == "h264_nvenc":
        return ["-hwaccel", "cuda"]
    return []

def _get_encoder() -> str:
    return _resolve_encoder()

def _get_preset() -> str:
    try:
        from clipwright.config import settings
        return getattr(settings, 'render_preset', 'medium')
    except Exception:
        return 'medium'

# ── Phase 3.3: 单次渲染编码器/预设/像素格式覆盖（contextvar，随 ctx.run 传播到线程池）──
_ENC_OVERRIDE: contextvars.ContextVar[str] = contextvars.ContextVar("clipwright_enc_override", default="")
_PRESET_OVERRIDE: contextvars.ContextVar[str] = contextvars.ContextVar("clipwright_preset_override", default="")
_PFMT_OVERRIDE: contextvars.ContextVar[str] = contextvars.ContextVar("clipwright_pfmt_override", default="")
_EXT_OVERRIDE: contextvars.ContextVar[str] = contextvars.ContextVar("clipwright_ext_override", default="mp4")


def _current_encoder() -> str:
    ov = _ENC_OVERRIDE.get()
    return ov or _get_encoder()


def _current_preset() -> str:
    ov = _PRESET_OVERRIDE.get()
    return ov or _get_preset()


def _current_pix_fmt() -> str:
    ov = _PFMT_OVERRIDE.get()
    return ov or "yuv420p"


def _current_ext() -> str:
    """批6：中间产物容器扩展名跟随交付预设（ProRes 必须 .mov——MP4 容器
    不支持 prores 编码，旧实现全部中间阶段硬编码 .mp4 → 全链 trim 失败）。"""
    ov = _EXT_OVERRIDE.get()
    return (ov or "mp4").lstrip(".")


def _encoder_stage_args(encoder: str, preset: str, pix_fmt: str | None = None) -> list[str]:
    """R3: 编码器条件化的阶段输出参数。

    - prores_ks 无 -preset 选项（传入会导致 encode error → 全部 trim 失败）；
    - pix_fmt 缺省取当前渲染 override（_current_pix_fmt），修复 PIP/MG/overlay
      阶段硬编码 yuv420p 破坏 10-bit/ProRes 交付的问题。
    """
    args = ["-c:v", encoder]
    if encoder != "prores_ks":
        args += ["-preset", preset]
    args += ["-pix_fmt", pix_fmt or _current_pix_fmt()]
    return args



def _delivery_extra_args(encoder: str) -> list[str]:
    """交付级编码器附加参数（ProRes HQ profile / x265 静默模式）。"""
    if encoder.startswith("prores"):
        return ["-profile:v", "3"]  # prores_ks profile 3 = HQ 422
    if encoder == "libx265":
        return ["-x265-params", "log-level=error"]
    return []


# ── Phase 3.1: 渲染指纹与阶段缓存 ────────────────────────────
from clipwright.tool.video import _CLIPWRIGHT_TEMP
_VIDEO_CACHE_DIR = _CLIPWRIGHT_TEMP / "video_cache"
_VIDEO_CACHE_MAX_BYTES = 3 * 1024 ** 3  # 3GB 视频阶段缓存配额
try:
    _VIDEO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass


def _file_ident(p: Path) -> str:
    """文件身份：小文件内容哈希（精确），大文件路径+大小+mtime（廉价）。"""
    try:
        st = p.stat()
        if st.st_size < (1 << 20):  # ≤1MB：内容哈希（小型素材/测试可靠性，不受 mtime 粒度影响）
            try:
                return f"{p}|{hashlib.sha256(p.read_bytes()).hexdigest()}"
            except Exception:
                pass
        return f"{p}|{st.st_size}|{int(st.st_mtime)}"
    except Exception:
        return str(p)


def _timeline_render_fingerprint(
    timeline, output, width, height, fps, bitrate,
    audio_bitrate, audio_file_path, bgm_file_path, encoder, preset, pix_fmt,
) -> str:
    """整条时间线渲染指纹（无操作快速返回用）。"""
    try:
        canonical = json.dumps(timeline.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    except Exception:
        canonical = str(timeline)
    payload = "|".join([
        canonical, str(output), str(width), str(height), str(fps), bitrate,
        audio_bitrate, audio_file_path or "", bgm_file_path or "", encoder, preset, pix_fmt,
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _video_stage_fingerprint(
    trimmed, text_overlays, overlay_segments, hf_ov_local,
    width, height, fps, bitrate, encoder, preset, pix_fmt,
) -> str:
    """视频合成阶段指纹（trim 产物 + 文本/MG/画中画 + 设置）— 音频变更不影响本阶段。"""
    parts = [
        "|".join(_file_ident(Path(t)) for t in (trimmed or [])),
        json.dumps(text_overlays or [], sort_keys=True, ensure_ascii=False),
        json.dumps(overlay_segments or [], sort_keys=True, ensure_ascii=False),
        json.dumps(hf_ov_local or [], sort_keys=True, ensure_ascii=False),
        f"{width}x{height}@{fps}:{bitrate}:{encoder}:{preset}:{pix_fmt}",
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _prune_video_cache_dir() -> None:
    """按 mtime 删除超额旧阶段缓存（启动时 + 每次渲染前）。"""
    try:
        entries = sorted(_VIDEO_CACHE_DIR.glob("stage_*.mp4"), key=lambda p: p.stat().st_mtime)
        total = sum(p.stat().st_size for p in entries)
        for p in entries:
            if total <= _VIDEO_CACHE_MAX_BYTES:
                break
            try:
                total -= p.stat().st_size
                p.unlink(missing_ok=True)
            except Exception:
                pass
    except Exception:
        pass

def _fmt_sec(v: float) -> str:
    """把秒数格式化为紧凑字符串（去掉尾零，避免浮点噪声污染 filter 表达式）。

    12.5 → "12.5", 15.0 → "15", 0.0 → "0"。
    """
    return f"{v:g}"

def _caption_renderer() -> str:
    """字幕渲染器：ass（默认，libass 全 14 字段）| drawtext（旧滤镜回退）。"""
    try:
        from clipwright.config import settings
        return getattr(settings, 'caption_renderer', 'ass')
    except Exception:
        return 'ass'

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

_xfade_support_cache: bool | None = None


def _ffmpeg_supports_xfade() -> bool:
    """R11: 进程内缓存探测结果（每次转场渲染都 shell out 一次 ffmpeg -filters）。"""
    global _xfade_support_cache
    if _xfade_support_cache is not None:
        return _xfade_support_cache
    try:
        r = subprocess.run(["ffmpeg", "-filters"], capture_output=True, text=True, timeout=10)
        if "xfade" in (r.stdout or ""):
            _xfade_support_cache = True
        else:
            _xfade_support_cache = False
    except Exception:
        _xfade_support_cache = None  # 探测失败不缓存，下次重试
    return _xfade_support_cache

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

def _is_valid_video(path: str | Path, min_bytes: int = 1024,
                    require_streams: bool = False) -> bool:
    """ffmpeg 输出有效性：存在且非空（ffmpeg -y 失败也会留下 0 字节占位文件，
    仅检查 exists() 会把失败误判为成功，导致最终导出空视频）。

    require_streams=True 时再经 ffprobe 校验时长 ≥1s 且含视频流——用于拼接/
    转场/混音等关键产物，防止单帧残片被回退链判为有效而静默丢内容。
    """
    try:
        p = Path(path)
        if not (p.exists() and p.stat().st_size >= min_bytes):
            return False
    except Exception:
        return False
    if not require_streams:
        return True
    try:
        if _get_actual_duration(str(p)) < 1.0:
            return False
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v", "-of", "json",
             "-show_entries", "stream=codec_type", str(p)],
            capture_output=True, text=True, timeout=20)
        streams = (json.loads(r.stdout or "{}").get("streams") or [])
        return bool(streams)
    except Exception:
        return False

_font_file_cache: str | None = None

def _resolve_system_font() -> str:
    """解析一个可用的字体文件路径（过滤器可用形式）。

    两个硬约束：
    1. 本项目 ffmpeg 是带 fontconfig 的静态构建；Windows 无 fontconfig 配置，
       所有 drawtext 会报 "Fontconfig error: Cannot load default config file"。
    2. 该构建的过滤器解析器**不识别 ``\\:`` 转义**，Windows 盘符 ``C:`` 会被
       当作参数分隔符截断 → fontfile 必须用**无冒号**的路径。
    因此：Windows 下把系统字体复制到项目 ``_fonts/``（相对 CWD 路径，无盘符），
    Unix 下直接返回系统字体绝对路径（无冒号，过滤器可用）。
    """
    global _font_file_cache
    if _font_file_cache is not None:
        return _font_file_cache

    # Windows：复制 CJK 字体到项目 _fonts/（相对路径，过滤器可解析）
    win_fonts = [
        r"C:\Windows\Fonts\msyh.ttc",    # 微软雅黑（中文字幕首选）
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\simhei.ttf",  # 黑体
        r"C:\Windows\Fonts\simsun.ttc",  # 宋体
        r"C:\Windows\Fonts\arial.ttf",
    ]
    for src in win_fonts:
        if Path(src).exists():
            try:
                dest_dir = Path.cwd() / "_fonts"
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest = dest_dir / Path(src).name
                if not dest.exists():
                    import shutil
                    shutil.copy2(src, dest)
                # 用正斜杠相对路径（与 CWD 一致），避免盘符冒号
                _font_file_cache = f"_fonts/{Path(src).name}"
                return _font_file_cache
            except Exception:
                continue

    unix_fonts = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for p in unix_fonts:
        if Path(p).exists():
            _font_file_cache = p
            return p
    _font_file_cache = ""
    return ""

# ── 粗体字体解析（任务 31）────────────────────────
# 项目无 fontconfig.py：已知字族映射到 Windows 粗体变体，找不到回退普通字族。
_BOLD_FONT_MAP: dict[str, str] = {
    "msyh": "msyhbd.ttc",
    "microsoftyahei": "msyhbd.ttc",
    "microsoft yahei": "msyhbd.ttc",
    "微软雅黑": "msyhbd.ttc",
    "simhei": "simhei.ttf",
    "simsun": "simsunb.ttf",
    "宋体": "simsunb.ttf",
    "黑体": "simhei.ttf",
    "msjh": "msjhbd.ttc",
    "microsoft jhenghei": "msjhbd.ttc",
    "malgun": "malgunbd.ttf",
    "malgun gothic": "malgunbd.ttf",
    "sans-serif": "arialbd.ttf",
    "arial": "arialbd.ttf",
    "helvetica": "arialbd.ttf",
    "times new roman": "timesbd.ttf",
    "times": "timesbd.ttf",
    "courier new": "courbd.ttf",
    "jetbrains mono": "JetBrainsMono-Bold.ttf",
    "jetbrainsmono": "JetBrainsMono-Bold.ttf",
}
_bold_font_cache: dict[str, str] = {}


def _resolve_bold_font(family: str = "") -> str:
    """解析字族的粗体变体（过滤器可用形式，无盘符冒号）。

    与 _resolve_system_font 同约束：Windows 下复制到项目 _fonts/ 用相对路径。
    空 family 默认 msyh；找不到映射/文件则回退普通字族。
    """
    key = (family or "msyh").strip().lower()
    if key in _bold_font_cache:
        return _bold_font_cache[key]
    bold_name = _BOLD_FONT_MAP.get(key)
    if not bold_name:
        fallback = _resolve_system_font()
        _bold_font_cache[key] = fallback
        return fallback
    src = Path(r"C:\Windows\Fonts") / bold_name
    if not src.exists():
        fallback = _resolve_system_font()
        _bold_font_cache[key] = fallback
        return fallback
    try:
        dest_dir = Path.cwd() / "_fonts"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        if not dest.exists():
            import shutil
            shutil.copy2(src, dest)
        resolved = f"_fonts/{src.name}"
        _bold_font_cache[key] = resolved
        return resolved
    except Exception:
        fallback = _resolve_system_font()
        _bold_font_cache[key] = fallback
        return fallback


def _build_glow_underlay(text: str, font_size, glow_color: str, glow_width: float,
                         font_arg: str, x: str, y: str, enable: str, alpha: str = "") -> str:
    """发光底层通道 drawtext（glow 双通道方案）。

    与主文本同 enable 窗口/同坐标/同 fontfile/fontsize；底层在前，主文本在后。
    用描边（bordercolor=glow_color, borderw=glow_width）形成光晕，
    fontcolor 半透明（0xCOLOR@0.6）避免遮盖主文本；glow_width 上限 20px 防 FFmpeg 裁剪。
    """
    c = color_to_drawtext(glow_color)
    w = max(1, min(int(round(float(glow_width))), 20))
    safe = escape_drawtext_text(text)
    parts = [
        f"drawtext=text='{safe}'{font_arg}",
        f"fontsize={font_size}",
        f"fontcolor={c}@0.6",
        f"borderw={w}",
        f"bordercolor={c}",
        f"x={x}",
        f"y={y}",
    ]
    if alpha:
        parts.append(f"alpha={alpha}")
    parts.append(f"enable='{enable}'")
    return ":".join(parts)

# 裁剪缓存（持久化目录，不随 render 清理删除）
# 生产加固 1.7: LRU 淘汰（条数 + 磁盘配额），淘汰时删除磁盘文件；启动时清理超额旧文件。
import threading as _threading
from collections import OrderedDict
from clipwright.tool.video import _CLIPWRIGHT_TEMP
_trim_cache: "OrderedDict[str, str]" = OrderedDict()
_trim_cache_lock = _threading.Lock()
_TRIM_CACHE_MAX = 50
_TRIM_CACHE_MAX_BYTES = 2 * 1024 ** 3  # 2GB 磁盘配额
_TRIM_CACHE_DIR = _CLIPWRIGHT_TEMP / "trim_cache"
_TRIM_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _trim_cache_put_locked(cache_key: str, path: str) -> None:
    """LRU 写入（调用方持锁）：重复键移到最新；超额淘汰最旧并删盘文件。"""
    _trim_cache[cache_key] = path
    _trim_cache.move_to_end(cache_key)
    while len(_trim_cache) > _TRIM_CACHE_MAX:
        _old_key, old_path = _trim_cache.popitem(last=False)
        try:
            Path(old_path).unlink(missing_ok=True)
        except Exception:
            pass


def _prune_trim_cache_dir() -> None:
    """按 mtime 删除超额旧文件（启动时 + 每次渲染前调用，成本一次 scandir）。"""
    try:
        import time as _time
        now = _time.time()
        # 10 分钟内的新文件可能被在用渲染引用——清理时跳过
        entries = sorted(
            (p for p in _TRIM_CACHE_DIR.glob("trim_*.*")
             if now - p.stat().st_mtime > 600),
            key=lambda p: p.stat().st_mtime)
        total = sum(p.stat().st_size for p in entries)
        for p in entries:
            if total <= _TRIM_CACHE_MAX_BYTES:
                break
            try:
                total -= p.stat().st_size
                p.unlink(missing_ok=True)
            except Exception:
                pass
    except Exception:
        pass


_prune_trim_cache_dir()

def _trim_cache_key(src: str, offset: float, dur: float, width: int, height: int,
                    speed: float = 1.0, image_fit: str = "", transform: str = "",
                    kfs: str = "", kb: str = "") -> str:
    # /D2/V4/V3: 速度、填充模式、静态变换与关键帧都影响输出内容，必须进缓存键
    # 轮 85: kenburns 改变画面（图片推近），同样必须进键
    raw = f"{src}|{offset:.2f}|{dur:.2f}|{width}x{height}|{speed:.3f}|{image_fit}|{transform}|{kfs}|{kb}"
    return hashlib.sha256(raw.encode()).hexdigest()


class RenderResult:
    def __init__(self, success: bool, output_path: str = "", error: str = "",
                 duration_sec: float = 0, ffmpeg_log: str = "", warnings: list[str] | None = None):
        self.success = success
        self.output_path = output_path
        self.error = error
        self.duration_sec = duration_sec
        self.ffmpeg_log = ffmpeg_log
        self.warnings = warnings or []

    def to_dict(self) -> dict[str, Any]:
        return {"success": self.success, "output_path": self.output_path,
                "error": self.error, "duration_sec": self.duration_sec,
                "ffmpeg_log": self.ffmpeg_log, "warnings": self.warnings}


class RenderService:
    """将 Timeline 渲染为 MP4 视频。"""

    def __init__(self, work_dir: Optional[str | Path] = None) -> None:
        from clipwright.tool.video import _CLIPWRIGHT_TEMP
        self._work_dir = Path(work_dir or _CLIPWRIGHT_TEMP / f"render_{uuid.uuid4().hex[:8]}")
        self._work_dir.mkdir(parents=True, exist_ok=True)
        self._final_ffmpeg_log: list[str] = []
        self._fallback_count = 0  # D6: trim 失败降级纯色段的计数（原静默仅 log）

    def _run_ff(self, cmd, **kw) -> subprocess.CompletedProcess:
        """同步执行 ffmpeg/外部命令（供已在线程池里的 sync 代码直接调用）。"""
        return run_tracked_ff(cmd, **kw)

    async def _ff(self, cmd, **kw) -> subprocess.CompletedProcess:
        """在 async 上下文把同步 ffmpeg/外部命令 offload 到 _ffmpeg_pool，避免冻住事件循环。

        若当前没有运行中的事件循环（即被 worker 线程调用），则退化为同步执行。
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return self._run_ff(cmd, **kw)
        from functools import partial
        # 复制当前 context（含 cancel_id），保证线程池内可感知取消
        ctx = contextvars.copy_context()
        return await loop.run_in_executor(
            _ffmpeg_pool, ctx.run, partial(self._run_ff, cmd, **kw)
        )

    async def _ff_concat(self, sync_fn, *args):
        """把同步拼接函数（内含阻塞 subprocess）offload 到 _ffmpeg_pool。"""
        loop = asyncio.get_running_loop()
        ctx = contextvars.copy_context()
        from functools import partial
        return await loop.run_in_executor(_ffmpeg_pool, ctx.run, partial(sync_fn, *args))

    async def render(self, timeline: Timeline, output_path: str | Path = "out.mp4",
                     *, width=1920, height=1080, fps=30.0, bitrate="5M",
                     audio_bitrate="192k", audio_file_path="", bgm_file_path="",
                     progress_callback=None, enable_progress=True,
                     cancel_id: str | None = None,
                     encoder_override: str = "", preset_override: str = "",
                     pix_fmt_override: str = "", force_render: bool = False,
                     soft_subtitle_srt: str = "",
                     loudnorm_enabled: bool = True) -> RenderResult:
        # 审计 P0 修复：取消标识注入 context，线程池内 ffmpeg 调用可感知并 terminate
        _CURRENT_CANCEL_ID.set(cancel_id)
        # Phase 3.3: 单次渲染编码器/像素格式覆盖注入 context（随 ctx.run 传播）
        _ENC_OVERRIDE.set(encoder_override)
        _PRESET_OVERRIDE.set(preset_override)
        _PFMT_OVERRIDE.set(pix_fmt_override)
        _EXT_OVERRIDE.set(str(output_path).rsplit(".", 1)[-1].lower() or "mp4")
        # 生产加固 1.7 + Phase 3.1: 每次渲染前清理超额缓存（一次 scandir，成本可忽略）
        _prune_trim_cache_dir()
        _prune_video_cache_dir()
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        self._final_ffmpeg_log = []
        self._fallback_count = 0

        ok, info = await asyncio.to_thread(ffmpeg_available)
        if not ok:
            return RenderResult(False, error=f"FFmpeg 未就绪: {info}")

        # Phase 3.1: 未变更无操作快速返回（指纹 sidecar 比对）
        self._render_fp = _timeline_render_fingerprint(
            timeline, output, width, height, fps, bitrate, audio_bitrate,
            audio_file_path, bgm_file_path, _current_encoder(), _current_preset(), _current_pix_fmt(),
        )
        if not force_render:
            sidecar = Path(str(output) + ".fp")
            if output.exists() and sidecar.exists():
                try:
                    if sidecar.read_text(encoding="utf-8").strip() == self._render_fp:
                        dur = await asyncio.to_thread(_get_actual_duration, str(output))
                        logger.info("渲染跳过（时间线未变更）: %s", output)
                        return RenderResult(True, output_path=str(output.resolve()), duration_sec=dur)
                except Exception:
                    pass

        try:
            async with _get_render_semaphore():
                try:
                    result = await self._render_inner(
                        timeline, output, width, height, fps, bitrate,
                        audio_bitrate, audio_file_path, bgm_file_path, progress_callback,
                        soft_subtitle_srt=soft_subtitle_srt,
                        loudnorm_enabled=loudnorm_enabled)
                    if result.success:
                        try:
                            Path(str(output) + ".fp").write_text(self._render_fp, encoding="utf-8")
                        except Exception:
                            pass
                    return result
                finally:
                    self._cleanup()
        finally:
            if cancel_id:
                clear_cancel_state(cancel_id)

    async def _render_inner(self, timeline, output, width, height, fps, bitrate,
                            audio_bitrate, audio_file_path, bgm_file_path, progress_callback,
                            soft_subtitle_srt: str = "", loudnorm_enabled: bool = True):
        video_segments, overlay_segments, text_overlays, audio_segments, hf_ov_local = \
            self._extract_segments(timeline)

        # 软字幕模式——caption 类烧入滤镜剔除（字幕走 mov_text 轨封装），
        # TEXT/动画类覆盖物仍烧入
        if soft_subtitle_srt and Path(soft_subtitle_srt).exists():
            text_overlays = [t for t in text_overlays if t.get("category") != "caption"]

        if progress_callback:
            # Bug4：prepare 从 0 起步，保证全阶段进度单调不减（trim 阶段从 0→50）。
            await progress_callback("prepare", 0, "解析时间线")

        # 并行裁剪
        encoder = _current_encoder()
        preset = _current_preset()
        trimmed = await self._trim_segments_parallel(video_segments, width, height, fps, bitrate,
                                                     encoder, preset, progress_callback)

        # Phase 3.1: 视频合成阶段缓存（pre-audio）— 命中时跳过 concat/text/MG/PIP 全量重编
        stage_fp = _video_stage_fingerprint(
            trimmed, text_overlays, overlay_segments, hf_ov_local,
            width, height, fps, bitrate, encoder, preset, _current_pix_fmt())
        cached_stage = _VIDEO_CACHE_DIR / f"stage_{stage_fp}.mp4"
        video_from_cache = False

        # 转场时间漂移校正——xfade 每处转场使成片缩短 td，下游字幕/音频/
        # MG/PIP 若仍按原始 start_sec 放置，将随转场逐段累积偏移。
        # 各片段累计漂移在此一次算出；文本烧写在 else 分支内应用，音频/MG/PIP
        # 在 else 之后统一应用（缓存命中路径同样需要）。
        _r1_seg_shifts: list[float] = []
        if len(trimmed) > 2 and any(
            video_segments[i].get("transition_in") for i in range(1, len(video_segments))
        ) and await asyncio.to_thread(_ffmpeg_supports_xfade):
            _cum = 0.0
            for _i, seg in enumerate(video_segments):
                _r1_seg_shifts.append(_cum)
                if _i > 0:
                    try:
                        _cum += max(0.0, float(seg.get("transition_duration_sec", 0) or 0))
                    except (TypeError, ValueError):
                        pass

        def _r1_shift(t: float) -> float:
            for _j in range(len(video_segments) - 1, -1, -1):
                try:
                    if t >= float(video_segments[_j].get("start_sec", 0) or 0):
                        return _r1_seg_shifts[_j]
                except (TypeError, ValueError):
                    continue
            return 0.0

        if cached_stage.exists() and _is_valid_video(cached_stage):
            final_video = str(cached_stage)
            video_from_cache = True
            if progress_callback:
                await progress_callback("video", 60, "视频阶段命中缓存（音频可快路径混入）")
        else:
            # 拼接
            final_video = await self._concat_segments(trimmed, video_segments, fps, bitrate,
                                                      encoder, preset, progress_callback,
                                                      width=width, height=height)

            # 漂移校正（R1）：文本/MG/PIP 的 start_sec 都要按 xfade 累计漂移前移，
            # 且必须在本轮合成之前完成。音频段的漂移在下面单独处理（缓存命中同样需要）。
            if _r1_seg_shifts:
                for _ov in text_overlays or []:
                    try:
                        _ov["start_sec"] = max(0.0, float(_ov["start_sec"]) - _r1_shift(float(_ov["start_sec"])))
                    except (TypeError, ValueError):
                        pass
                for _seg in overlay_segments or []:
                    try:
                        _seg["start_sec"] = max(0.0, float(_seg["start_sec"]) - _r1_shift(float(_seg["start_sec"])))
                    except (TypeError, ValueError):
                        pass
                for _ov in hf_ov_local or []:
                    try:
                        _ov["start_sec"] = max(0.0, float(_ov["start_sec"]) - _r1_shift(float(_ov["start_sec"])))
                    except (TypeError, ValueError):
                        pass

            # 合成顺序（video-use 硬规则 1：字幕/文字必须在所有 overlay 之后）：
            # 图解 + MG 动画 → 画中画 → 文本/字幕。旧的顺序把文字烧在中途，
            # MG/图解 overlay 随后覆盖上来 → 动画挡住字幕。
            final_video = await self._compose_video_stages(
                final_video, text_overlays, hf_ov_local, overlay_segments,
                encoder, preset, width, height, fps, progress_callback,
            )

        # 音频段漂移校正（缓存命中路径同样需要：音频在视频阶段之后混入）
        if _r1_seg_shifts:
            for _seg in audio_segments:
                try:
                    _seg["start_sec"] = max(0.0, float(_seg["start_sec"]) - _r1_shift(float(_seg["start_sec"])))
                except (TypeError, ValueError):
                    pass

        # 缓存 pre-audio 视频（供「仅音频变更」快路径复用）
        if final_video and _is_valid_video(final_video):
            try:
                shutil.copy2(final_video, cached_stage)
            except Exception as e:
                logger.warning("视频阶段缓存写入失败: %s", e)

        # 音频（C12：混合失败必须标记到结果，而非静默静音成片）
        audio_warnings: list[str] = []
        # Persona 目标响度（时间线元数据）→ loudnorm I 值（缺省 -16）
        loudness_target = None
        try:
            loudness_target = float(
                (getattr(timeline, "metadata", {}) or {}).get("target_loudness_lufs"))
        except (TypeError, ValueError):
            loudness_target = None
        # 轮 95：响度归一开关——timeline.metadata.loudnorm=false 可覆盖关闭
        #（请求级参数优先，时间线元数据兜底细粒度控制）
        if loudnorm_enabled:
            loudnorm_enabled = bool(
                (getattr(timeline, "metadata", {}) or {}).get("loudnorm", True))
        if final_video:
            if progress_callback:
                await progress_callback("audio", 98, "混流音频")
            final_video, mix_marker = await self._mix_audio_safe(
                final_video, audio_segments, audio_file_path, bitrate, audio_bitrate, bgm_file_path,
                video_cached=video_from_cache, loudness_target=loudness_target,
                loudnorm_enabled=loudnorm_enabled)
            if mix_marker:
                audio_warnings.append(mix_marker)
                logger.error("C12 音频混合失败已标记: %s (render=%s)", mix_marker, output)

        # 输出
        if final_video and _is_valid_video(final_video):
            if progress_callback:
                await progress_callback("done", 99, "输出成片")
            # 软字幕封装——SRT 以 mov_text 轨挂入（失败回退硬拷贝无字幕轨）
            _srt_ok = False
            if soft_subtitle_srt and Path(soft_subtitle_srt).exists():
                try:
                    await self._ff(
                        ["ffmpeg", "-y", "-loglevel", "error", "-i", final_video,
                         "-i", soft_subtitle_srt,
                         "-map", "0:v:0", "-map", "0:a?", "-map", "1:0",
                         "-c:v", "copy", "-c:a", "copy", "-c:s", "mov_text",
                         "-movflags", "+faststart", str(output)],
                        capture_output=True, text=False, timeout=600)
                    _srt_ok = _is_valid_video(output)
                except Exception as e:
                    logger.warning("D4 软字幕封装失败，回退无字幕轨: %s", e)
            if not _srt_ok:
                shutil.copy2(final_video, str(output))
            if not _is_valid_video(output):
                logger.error("渲染输出为空文件: %s（源 %s）", output, final_video)
                return RenderResult(False, error=f"渲染输出为空文件 (final={final_video})",
                                ffmpeg_log=chr(10).join(self._final_ffmpeg_log[-50:]))
            dur = await asyncio.to_thread(_get_actual_duration, str(output))
            if progress_callback:
                await progress_callback("done", 100, "渲染完成")
            logger.info("渲染完成: %s (%.1fs)", output, dur)
            # /D7 + V1 规则 5：成片时长校验——与"时间线时长 − 转场累计侵蚀"比对
            # （video-use 的 self-eval 会用 ffprobe 校验输出时长 == EDL 预期；
            #   早期实现拿原始 duration_sec 比对，转场侵蚀被误报为偏差）
            warnings = list(audio_warnings)
            if self._fallback_count:
                warnings.append(f"{self._fallback_count} 个片段源不可用，已降级纯色段")
            # V4/V7：渲染后切点复核（默认关闭，best-effort）
            await self._post_render_review(str(output), video_segments, warnings)
            try:
                tl_dur = float(getattr(timeline, "duration_sec", 0) or 0)
            except (TypeError, ValueError):
                tl_dur = 0.0
            expected_dur = self.expected_output_duration(
                tl_dur, video_segments, transitions_applied=bool(_r1_seg_shifts))
            dev = self.duration_deviation(dur, expected_dur)
            if dev is not None:
                warnings.append(
                    f"成片实际时长 {dur:.2f}s 与预期 {expected_dur:.2f}s 偏差 "
                    f"{dev:.2f}s（转场侵蚀已扣除；超出容差）")
                logger.warning("时长校验: 实际 %.2fs / 预期 %.2fs / 偏差 %.2fs",
                               dur, expected_dur, dev)
                if (os.environ.get("CLIPWRIGHT_STRICT_DURATION") or "").strip() in ("1", "true", "yes"):
                    return RenderResult(
                        False, error=f"成片时长偏差超出容差: {dur:.2f}s vs {expected_dur:.2f}s",
                        ffmpeg_log=chr(10).join(self._final_ffmpeg_log[-50:]), warnings=warnings)
            return RenderResult(True, output_path=str(output.resolve()), duration_sec=dur,
                                ffmpeg_log=chr(10).join(self._final_ffmpeg_log[-50:]),
                                warnings=warnings)

        return RenderResult(False, error=f"渲染失败 (video={len(video_segments)}, trimmed={len(trimmed)})",
                            ffmpeg_log=chr(10).join(self._final_ffmpeg_log[-50:]))

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
                             blend_mode=getattr(clip, 'blend_mode', None),
                             # 死字段接线——image_fit/mask 此前渲染零消费
                             image_fit=str(getattr(clip, 'image_fit', None) or ''),
                             mask_type=getattr(clip, 'mask_type', None),
                             mask_rect=clip.mask_rect,
                             # /M6: 音频淡入淡出
                             audio_fade_in_sec=getattr(clip, 'audio_fade_in_sec', None),
                             audio_fade_out_sec=getattr(clip, 'audio_fade_out_sec', None),
                             # 视频特效
                             fx_brightness=getattr(clip, 'fx_brightness', None),
                             fx_contrast=getattr(clip, 'fx_contrast', None),
                             fx_saturation=getattr(clip, 'fx_saturation', None),
                             fx_blur=getattr(clip, 'fx_blur', None),
                             fx_hue=getattr(clip, 'fx_hue', None))
                if k in ("video", "image"):
                    entry["source_path"] = clip.asset_id
                    # 轮 84（数码评测专项）：LUT 调色入口——clip.metadata.lut_path
                    # 指向 .cube 文件时在 trim 滤镜链应用 lut3d
                    _lp = (clip.metadata or {}).get("lut_path")
                    if _lp:
                        entry["lut_path"] = str(_lp)
                    # 轮 85：静态图 Ken Burns 缓慢推近（图片默认开启，
                    # metadata.kenburns=False 可关；视频片段不适用）
                    entry["kenburns"] = (
                        k == "image"
                        and bool((clip.metadata or {}).get("kenburns", True))
                    )
                    (overlay_segments if is_overlay else video_segments).append(entry)
                elif k == "audio":
                    entry["source_path"] = clip.asset_id
                    # 音源角色标记——供混音阶段做 BGM 自动避让（ducking）
                    _am = clip.metadata or {}
                    entry["is_bgm"] = bool(_am.get("bgm"))
                    entry["is_voice"] = bool(_am.get("narration") or _am.get("dubbing"))
                    # 轮 85：语音降噪标记（afftdn）
                    entry["denoise"] = bool(_am.get("denoise"))
                    audio_segments.append(entry)
                elif k in ("text", "caption"):
                    # 先计算偏移，分离追加以避免列表自引用歧义
                    ov = self._extract_text_overlay(clip, track.index, text_overlays)
                    text_overlays.append(ov)
                elif k == "animation":
                    meta = clip.metadata or {}
                    if meta.get("renderer") == "mg_hyperframes" and meta.get("mg_html"):
                        mg_html = self._ensure_mg_placeholders_filled(meta, clip.text or "")
                        hf_ov_local.append(dict(mg_html=mg_html, start_sec=clip.start_sec,
                                                duration_sec=clip.duration_sec, _track_idx=track.index))
                    else:
                        text_overlays.append(self._extract_animation_overlay(clip, track.index))
        return video_segments, overlay_segments, text_overlays, audio_segments, hf_ov_local

    @staticmethod
    def _ensure_mg_placeholders_filled(meta: dict, text_content: str = "") -> str:
        """确保 MG HTML 不含字面占位符（{key}）。

        老版本生成的 clip 可能把 {left_label}/{accent} 等占位符原样写进 mg_html，
        此处若检测到残留占位符，则用存储的 mg_def + params 重渲一遍：
        优先按 clip.text 的 | 分隔关键段填充（与 generator._build_llm_params 同源逻辑），
        缺失时回退 mg_def.params 默认值，避免占位符字面量渲染进成片。
        重渲失败则退回原始 HTML。
        """
        import json as _json
        import re as _re
        html = meta.get("mg_html", "")
        if not html:
            return html
        if not _re.search(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}", html):
            return html
        mg_def = meta.get("mg_def") or {}
        if not mg_def:
            return html
        try:
            from clipwright.animation.mg.fallback import FallbackEngine
            # 解析 clip.text：可能是 {"text":"A|B","description":...} JSON，也可能是裸文本
            real_text = text_content
            if text_content.lstrip().startswith("{"):
                try:
                    parsed = _json.loads(text_content)
                    real_text = parsed.get("text") or text_content
                except Exception:
                    pass
            parts = FallbackEngine.extract_keywords(real_text)

            param_defs = mg_def.get("params") or {}
            param_keys = list(param_defs.keys())
            # LLM 生成的 mg_def 可能只在元素内容里出现占位符，无 params 声明 → 从内容扫描
            if not param_keys:
                seen: list[str] = []
                for m in _re.findall(
                    r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", _json.dumps(mg_def, ensure_ascii=False)
                ):
                    if m not in seen:
                        seen.append(m)
                param_keys = seen

            params: dict = {}
            for i, key in enumerate(param_keys):
                if i < len(parts):
                    params[key] = parts[i]
                else:
                    default = param_defs.get(key)
                    params[key] = default.get("default", "") if isinstance(default, dict) else ""
            if parts:
                params["text"] = parts[0]
            stored = meta.get("mg_params") or {}
            for k, v in stored.items():
                if k not in params or not params[k]:
                    params[k] = v if isinstance(v, str) else ""
            from clipwright.animation.mg_renderer import MGRenderer
            rebuilt = MGRenderer.render(mg_def, params)
            if rebuilt and not _re.search(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}", rebuilt):
                logger.info("Render: 重渲 MG HTML 清除占位符 anim=%s", meta.get("anim_type"))
                return rebuilt
        except Exception as e:
            logger.warning("Render: MG 占位符重渲失败: %s", e)
        return html

    @staticmethod
    def _extract_text_overlay(clip, track_idx, existing):
        meta = clip.metadata or {}
        style = dict(meta.get("style", {}))
        # 合并判据：metadata.category == "caption" 或 Clip.kind == caption
        category = meta.get("category", "") or (
            "caption" if str(getattr(clip, "kind", "")) == "caption" else "")
        pos = meta.get("position", style.get("position", {1: "bottom", 2: "top", 3: "center"}.get(track_idx, "bottom")))
        # 字幕（caption，合并双方语义）：offset_y 恒 0（Bug1：同轨字幕不逐条上移）；
        # 管线产出的 caption（携带 metadata）且无显式 style 时注入默认描边
        # （borderw=2，白字可读性）；完全无 metadata 的 legacy ASS 字幕保持原样。
        if category == "caption":
            offset_y = 0
            if not style and meta:
                style = {"stroke_width": 2, "stroke_color": "#000000", "offset_y": 0, "position": pos}
        else:
            offset_y = min(len([t for t in existing if t.get("_track_idx") == track_idx]) * 35, 500)
        # 任务 31：新样式字段直接从 Clip 读取并合并进 style dict（clip 优先，meta.style 兜底）
        for f in _CLIP_STYLE_FIELDS:
            v = getattr(clip, f, None)
            if v is not None:
                style[f] = v
        return dict(start_sec=clip.start_sec, duration_sec=clip.duration_sec,
                    text=clip.text or "", font_size=clip.font_size or 48,
                    font_color=clip.font_color or "#ffffff", font=clip.font or "",
                    position=pos, offset_y=offset_y, style=style,
                    anim_type=meta.get("anim_type", ""), renderer=meta.get("renderer", "drawtext"),
                    category=category, _track_idx=track_idx, keyframes=clip.keyframes or [],
                    # 关键帧时间基标记（前端 exportTimeline 写 clip_local）
                    kf_time_base=meta.get("kf_time_base"))

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

        # setsar=1 归一化像素宽高比：Pexels 等来源 SAR 不一致（1215:1216 vs 1:1），
        # concat 过滤器要求所有输入 SAR 相同，否则报 "Invalid argument" 导致拼接失败
        loop = asyncio.get_running_loop()

        def _trim_one(idx, seg):
            # 注意：本函数整体在 _ffmpeg_pool 线程里跑（见下方 run_in_executor），
            # 故内部用同步 _run_ff，禁止在此 await。
            src = seg.get("source_path", "")
            speed = seg.get("speed", 1.0)
            try:
                speed = float(speed or 1.0)
            except (TypeError, ValueError):
                speed = 1.0
            speed = max(0.25, min(4.0, speed))
            dur = max(0.5, seg.get("duration_sec", 5))
            if not src:
                self._fallback_count += 1
                return self._generate_fallback(dur, width, height, fps, idx)

            # 源文件损坏预检：无时长/无视频流（如 EditAgent 裁剪失败的 258 字节残留）
            # 直接走 fallback，避免 ffmpeg 对 Duration:N/A 的输入挂死/空输出
            if not self._source_valid(src):
                logger.warning("RenderService: 源文件不可解码，用 fallback 兜底: %s", str(src)[-50:])
                self._final_ffmpeg_log.append(f"trim({str(src)[-30:]}): 源文件不可解码 → fallback")
                self._fallback_count += 1
                return self._generate_fallback(dur, width, height, fps, idx)

            # 缓存（D1/D2: speed 与 image_fit 进键；V4: 静态变换进键）
            image_fit = str(seg.get("image_fit", "") or "").lower()
            # 静态变换（预览 metadata.transform 对齐导出）
            _tf_raw = (seg.get("metadata") or {}).get("transform") or {}
            try:
                _tx = float(_tf_raw.get("x", 0) or 0)
                _ty = float(_tf_raw.get("y", 0) or 0)
                _ts = float(_tf_raw.get("scale", 1) or 1)
                _tr = float(_tf_raw.get("rotation", 0) or 0)
            except (TypeError, ValueError):
                _tx = _ty = 0.0
                _ts = 1.0
                _tr = 0.0
            _has_transform = (abs(_ts - 1.0) > 1e-3 or abs(_tr) > 1e-3
                              or abs(_tx) > 1e-4 or abs(_ty) > 1e-4)
            # 关键帧归一化到片段相对秒（前端 clip_local 标记 / 管线绝对秒启发式）
            _kfs_raw = seg.get("keyframes") or []
            _kf_marker = (seg.get("metadata") or {}).get("kf_time_base")
            _kfs = normalize_keyframe_times(_kfs_raw, dur, seg.get("start_sec", 0) or 0.0,
                                            time_base_marker=_kf_marker)
            _kfs_digest = ""
            if _kfs_raw:
                _kfs_digest = hashlib.sha256(
                    json.dumps(_kfs_raw, sort_keys=True, default=str).encode()).hexdigest()[:16]
            cache_key = _trim_cache_key(src, seg.get("source_offset", 0), dur, width, height,
                                        speed, image_fit,
                                        transform=f"{_tx:.4f},{_ty:.4f},{_ts:.4f},{_tr:.4f}" if _has_transform else "",
                                        kfs=_kfs_digest,
                                        kb="1" if seg.get("kenburns") else "")
            with _trim_cache_lock:
                cached = _trim_cache.get(cache_key)
                if cached:
                    _trim_cache.move_to_end(cache_key)  # LRU touch
            if cached and Path(cached).exists():
                return cached

            out = str(_TRIM_CACHE_DIR / f"trim_{cache_key}.{_current_ext()}")
            # 先写临时名再原子替换——并发渲染同 key 不再互写半截文件。
            # 临时名必须把**媒体扩展名留在末尾**（`x.part.mp4`）：ffmpeg 仅按输出
            # 文件后缀推断 muxer，`x.mp4.part` 会直接失败
            # （"Error initializing the muxer" / "Error opening output files"）→
            # 每个片段静默降级为 fallback 板。见 _atomic_temp_path。
            out_tmp = self._atomic_temp_path(out)
            try:
                # 关键帧变速——有 speed 关键帧时走分段恒速近似（trim+concat），
                # 全局 setpts 前缀跳过；无关键帧保持 D1 单段 setpts 路径
                _kf_speed_expr = property_expression(_kfs, "speed", speed) if _kfs else None
                if _kf_speed_expr:
                    speed_prefix = ""
                else:
                    speed_prefix = f"setpts=PTS/{speed:.4f}," if abs(speed - 1.0) > 0.01 else ""
                # image_fit 落地——COVER=裁满（increase+crop），CONTAIN=信箱
                # （decrease+pad，与原行为一致）；原实现两模式都走 pad 分支。
                # 轮 85：图片 + kenburns 时先放大 1.3x 供 zoompan 裁切（保证
                # 推近后仍清晰），输出仍为 WxH
                _kb = bool(seg.get("kenburns"))
                if _kb:
                    _kb_w, _kb_h = int(width * 1.3), int(height * 1.3)
                    if image_fit == "contain":
                        fit_scale = (f"scale={_kb_w}:{_kb_h}:force_original_aspect_ratio=decrease,"
                                     f"pad={_kb_w}:{_kb_h}:(ow-iw)/2:(oh-ih)/2,setsar=1")
                    else:
                        fit_scale = (f"scale={_kb_w}:{_kb_h}:force_original_aspect_ratio=increase,"
                                     f"crop={_kb_w}:{_kb_h},setsar=1")
                elif image_fit == "contain":
                    fit_scale = (f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                                 f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1")
                else:
                    fit_scale = (f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                                 f"crop={width}:{height},setsar=1")
                vf = speed_prefix + fit_scale
                if _kb:
                    # Ken Burns：中心缓慢推近至 1.10x（zoompan d=1 逐输出帧递增）
                    _kb_frames = max(1, int(dur * fps))
                    _kb_step = 0.10 / _kb_frames
                    vf += (f",zoompan=z='min(1+{_kb_step:.8f}*on,1.10)':"
                           f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:"
                           f"fps={fps}:s={width}x{height}")

                # /X1: 蒙版落地——rectangle(=rect) 裁剪还原画幅；ellipse geq alpha 椭圆遮罩
                mask_rect = seg.get("mask_rect")
                _mask_type = seg.get("mask_type")
                if _mask_type in ("rectangle", "rect") and isinstance(mask_rect, dict):
                    try:
                        mx = max(0.0, min(1.0, float(mask_rect.get("x", 0) or 0)))
                        my = max(0.0, min(1.0, float(mask_rect.get("y", 0) or 0)))
                        mw = max(0.0, min(1.0, float(mask_rect.get("w", 0) or 0)))
                        mh = max(0.0, min(1.0, float(mask_rect.get("h", 0) or 0)))
                    except (TypeError, ValueError):
                        mx = my = mw = mh = 0.0
                    if mw > 0.01 and mh > 0.01:
                        vf += (f",crop=iw*{mw:.4f}:ih*{mh:.4f}:iw*{mx:.4f}:ih*{my:.4f},"
                               f"scale={width}:{height},setsar=1")
                elif _mask_type == "ellipse":
                    # 椭圆蒙版：中心椭圆内保留，外 alpha=0（与预览 ellipse 蒙版对齐）
                    vf += (",format=rgba,geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
                           "a='if(lt(pow(X-(W/2),2)/pow(W/2,2)+pow(Y-(H/2),2)/pow(H/2,2),1),255,0)'")

                # opacity 关键帧 → 缓动感知分段插值表达式（替换旧 0.1s 窗口近似，
                # 旧近似多关键帧时 alpha 叠加越界闪烁）
                _kf_op = property_expression(_kfs, "opacity", 1.0) if _kfs else None
                if _kf_op:
                    vf += f",format=rgba,colorchannelmixer=aa={_kf_op}"

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
                # 轮 84（数码评测专项）：LUT 调色（lut3d）——修 LUT 插件双重
                # 死链的渲染端消费；路径经白名单校验且拒特殊字符防滤镜注入
                _lut = seg.get("lut_path")
                if _lut:
                    _lut_s = str(_lut)
                    if (
                        _lut_s.lower().endswith(".cube")
                        and not _re.search(r"['\":,;]", _lut_s)
                        and Path(_lut_s).is_file()
                    ):
                        try:
                            from clipwright.security import assert_allowed_path as _aap
                            _lut_safe = _aap(Path(_lut_s))
                            fx_parts.append(f"lut3d=file='{_lut_safe}'")
                        except Exception:
                            logger.debug("LUT 路径不在白名单，跳过: %s", _lut_s)
                if fx_parts:
                    vf += "," + ",".join(fx_parts)

                # 关键帧 transform（translate/scale，画幅比例单位，静态值为缺省
                # 基线）——经 overlay/scale eval=frame 表达式逐帧求值
                _kf_tx = property_expression(_kfs, "translate_x", _tx) if _kfs else None
                _kf_ty = property_expression(_kfs, "translate_y", _ty) if _kfs else None
                _kf_sc = property_expression(_kfs, "scale_x", _ts) if _kfs else None
                _kf_rot = property_expression(_kfs, "rotate", _tr) if _kfs else None
                _has_kf_transform = bool(_kf_tx or _kf_ty or _kf_sc or _kf_rot)

                # 分段恒速近似——speed 关键帧区间取平均速度，源区间按
                # S(t)=∫v 累计（含 source_offset 基准）
                pieces: list[tuple[float, float, float]] = []
                if _kf_speed_expr:
                    spd_pts = sorted(
                        (float(kf.get("time", 0) or 0), float(kf["properties"]["speed"]))
                        for kf in _kfs if "speed" in (kf.get("properties") or {})
                    )
                    if spd_pts:
                        if spd_pts[0][0] > 1e-4:
                            spd_pts.insert(0, (0.0, speed))
                        if spd_pts[-1][0] < dur - 1e-4:
                            spd_pts.append((dur, spd_pts[-1][1]))
                        src_pos = float(seg.get("source_offset", 0) or 0)
                        for (t0, v0), (t1, v1) in zip(spd_pts, spd_pts[1:]):
                            if t1 - t0 <= 1e-4:
                                continue
                            v_avg = max(0.1, (max(0.1, v0) + max(0.1, v1)) / 2)
                            pieces.append((src_pos, src_pos + v_avg * (t1 - t0), v_avg))
                            src_pos += v_avg * (t1 - t0)

                _needs_fc = _has_transform or _has_kf_transform or pieces
                if _needs_fc:
                    body = vf
                    overlay_spec: tuple[str, str, str] | None = None
                    if _has_kf_transform:
                        sc_e = _kf_sc or (f"{_ts:.4f}" if abs(_ts - 1.0) > 1e-3 else None)
                        tx_e = _kf_tx or (f"{_tx:.4f}" if abs(_tx) > 1e-4 else None)
                        ty_e = _kf_ty or (f"{_ty:.4f}" if abs(_ty) > 1e-4 else None)
                        if _kf_rot:
                            # kf rotate：rotate 输出尺寸在 init 定死，不支持逐帧变
                            # 尺寸的下游链——scale 关键帧取最大值恒定化（保留旋转
                            # 动画；rotate+scale 同动画为有损近似，见报告 V3 备注）
                            sc_max = _ts
                            try:
                                sc_vals = [float(kf["properties"]["scale_x"]) for kf in _kfs
                                           if "scale_x" in (kf.get("properties") or {})]
                                if sc_vals:
                                    sc_max = max(max(sc_vals), _ts)
                            except (TypeError, ValueError):
                                pass
                            sc_max = max(0.1, min(4.0, sc_max))
                            sw = max(2, int(width * sc_max) // 2 * 2)
                            sh = max(2, int(height * sc_max) // 2 * 2)
                            body += f",scale={sw}:{sh},rotate='{_kf_rot}*PI/180':c=black:ow={sw}:oh={sh}"
                        else:
                            if sc_e:
                                body += f",scale=w='iw*({sc_e})':h='ih*({sc_e})':eval=frame"
                            if abs(_tr) > 1e-3:
                                body += f",rotate={_tr:.4f}*PI/180:c=black"
                        ox_e = f"(main_w-overlay_w)/2+({tx_e})*main_w" if tx_e else "(main_w-overlay_w)/2"
                        oy_e = f"(main_h-overlay_h)/2+({ty_e})*main_h" if ty_e else "(main_h-overlay_h)/2"
                        overlay_spec = ("expr", ox_e, oy_e)
                    elif _has_transform:
                        # 静态变换——scale/rotate 绕中心，translate × 画幅
                        sw = max(2, int(width * abs(_ts)) // 2 * 2)
                        sh = max(2, int(height * abs(_ts)) // 2 * 2)
                        body += f",scale={sw}:{sh},rotate={_tr:.4f}*PI/180:c=black:ow={sw}:oh={sh}"
                        ox = int(round((width - sw) / 2 + _tx * width))
                        oy = int(round((height - sh) / 2 + _ty * height))
                        overlay_spec = ("static", str(ox), str(oy))
                    chains: list[str] = []
                    if pieces:
                        chains.append(f"[0:v]{body}[pre0]")
                        for i, (s0, s1, v_i) in enumerate(pieces):
                            chains.append(
                                f"[pre0]trim=start={s0:.4f}:end={s1:.4f},"
                                f"setpts=(PTS-START)/{v_i:.4f}[pc{i}]")
                        concat_in = "".join(f"[pc{i}]" for i in range(len(pieces)))
                        chains.append(f"{concat_in}concat=n={len(pieces)}:v=1:a=0[segv]")
                    else:
                        chains.append(f"[0:v]{body}[segv]")
                    if overlay_spec:
                        kind, a, b = overlay_spec
                        chains.append(f"color=c=black:s={width}:{height}:r={fps}:d={dur:.4f}[bg]")
                        if kind == "expr":
                            chains.append(f"[bg][segv]overlay=x='{a}':y='{b}':eof_action=pass[vout]")
                        else:
                            chains.append(f"[bg][segv]overlay=x={a}:y={b}:eof_action=pass[vout]")
                    fc = ";".join(chains)
                    cmd = ["ffmpeg", "-y", "-loglevel", "error", *(_hwaccel_args(encoder)),
                           "-ss", str(seg.get("source_offset", 0)),
                           "-stream_loop", "-1", "-i", src, "-t", str(dur),
                           "-filter_complex", fc, "-map", "[vout]", "-r", str(fps),
                           *_encoder_stage_args(encoder, preset),
                           "-b:v", bitrate, "-an", out_tmp]
                else:
                    cmd = ["ffmpeg", "-y", "-loglevel", "error", *(_hwaccel_args(encoder)),
                           "-ss", str(seg.get("source_offset", 0)),
                           "-stream_loop", "-1", "-i", src, "-t", str(dur), "-vf", vf, "-r", str(fps),
                           *_encoder_stage_args(encoder, preset),
                           "-b:v", bitrate, "-an", out_tmp]
                r = self._run_ff(cmd, capture_output=True, text=False, timeout=600)
                if r.returncode == 0 and _is_valid_video(out_tmp):
                    # 原子落位
                    import os as _os
                    _os.replace(out_tmp, out)
                    with _trim_cache_lock:
                        _trim_cache_put_locked(cache_key, out)
                    return out
                self._final_ffmpeg_log.append(f"trim({src[-30:]}): {_sanitize_ffmpeg_error(r.stderr)}")
            except (FileNotFoundError, subprocess.TimeoutExpired) as e:
                self._final_ffmpeg_log.append(f"trim({src[-30:]}): {e}")

            self._fallback_count += 1
            return self._generate_fallback(dur, width, height, fps, idx)

        # 并行执行所有裁剪：每个 _trim_one 是同步阻塞 ffmpeg，丢进线程池才真正并行，
        # 且不冻住事件循环（旧实现 _trim_one 为 async 内同步 subprocess，gather 实为串行）。
        tasks = [loop.run_in_executor(_ffmpeg_pool, contextvars.copy_context().run, _trim_one, i, s) for i, s in enumerate(segments)]
        results = await asyncio.gather(*tasks)
        # 批A(D1)：trim 失败时同步丢弃对应 segment——旧实现只过滤 trimmed，
        # 后续 zip(trimmed, segments) 位置错开 → 空洞检测/转场配对全部错位
        kept = [
            (seg, r) for seg, r in zip(segments, results)
            if r and Path(r).exists()
        ]
        segments = [seg for seg, _ in kept]
        trimmed = [t for _, t in kept]

        if progress_callback:
            await progress_callback("trim", 50, f"完成 {len(trimmed)}/{len(segments)} 裁剪")
        return trimmed

    async def _post_render_review(self, output: str, video_segments: list[dict],
                                  result_warnings: list[str]) -> None:
        """渲染后切点复核（V4/V7，默认关闭）：抽切点复核图，可选交视觉模型打分。

        开关：环境变量 ``CLIPWRIGHT_POST_RENDER_REVIEW=1``（或 ``render_review`` 设置）。
        产物写 ``CLIPWRIGHT_REVIEW_DIR``（缺省放渲染工作目录）。
        任何失败只记日志、绝不改变渲染结果——自评是"多看一层"，不是新的失败点。
        """
        try:
            enabled = (os.environ.get("CLIPWRIGHT_POST_RENDER_REVIEW") or "").strip() in ("1", "true", "yes")
            if not enabled:
                try:
                    from clipwright.config import settings as _settings

                    enabled = bool(getattr(_settings, "render_review", False))
                except Exception:
                    enabled = False
            if not enabled:
                return
            from clipwright.services.timeline_view import (
                build_review_sheets, cut_times_from_segments,
            )

            cut_times = cut_times_from_segments(video_segments)
            if not cut_times:
                logger.info("切点复核: 无切点（单片段时间线），跳过")
                return
            base = (os.environ.get("CLIPWRIGHT_REVIEW_DIR") or "").strip()
            review_dir = (
                Path(base) / f"review_{uuid.uuid4().hex[:8]}" if base
                else Path(self._work_dir) / f"review_{uuid.uuid4().hex[:8]}"
            )
            sheets = await asyncio.to_thread(build_review_sheets, output, cut_times, review_dir)
            if not sheets:
                logger.warning("切点复核: 未能生成复核图（抽帧失败）")
                return
            logger.info("切点复核: %d 个切点 → %d 张复核图（%s）",
                        len(cut_times), len(sheets), review_dir)
            result_warnings.append(f"切点复核图 {len(sheets)} 张已生成: {review_dir}")
            self._final_ffmpeg_log.append(f"切点复核: {len(sheets)} 张 → {review_dir}")
            # 可选：交视觉模型打分（与 MG 视觉自评共用评分器与 enable_visual_llm 门控）
            try:
                from clipwright.animation.mg.visual_eval import MGVisualEvaluator

                verdict = await MGVisualEvaluator().evaluate(
                    sheets,
                    description=f"{len(cut_times)} 个剪辑点的前后帧对照（查跳变/爆闪/字幕遮挡/overlay 错位）",
                )
            except Exception as exc:
                logger.debug("切点复核: 视觉打分跳过（%s）", exc)
                verdict = None
            if verdict:
                score = verdict.get("score")
                logger.info("切点复核: 视觉评分 %s（问题 %d 条）",
                            score, len(verdict.get("issues") or []))
                result_warnings.append(f"切点复核视觉评分 {score}")
                for issue in (verdict.get("issues") or [])[:3]:
                    result_warnings.append(f"切点复核问题: {issue.get('detail', '')}")
        except Exception as exc:
            logger.warning("切点复核失败（不影响成片）: %s", exc)

    @staticmethod
    def expected_output_duration(
        timeline_duration: float,
        video_segments: list[dict] | None = None,
        transitions_applied: bool = False,
    ) -> float:
        """成片**预期时长** = 时间线时长 − 转场累计侵蚀时长。

        xfade 每处转场会把成片缩短 `transition_duration`；只有真正走 xfade 路径时
        （`_r1_seg_shifts` 非空）才扣减，否则硬切拼接不缩短。用累计值校正后再比对，
        才能把"正常的转场缩短"与"真实偏差"区分开——旧实现拿原始 duration_sec 比对，
        有转场的片子必然误报。
        """
        try:
            base = float(timeline_duration or 0)
        except (TypeError, ValueError):
            base = 0.0
        if base <= 0:
            return 0.0
        erosion = 0.0
        if transitions_applied and video_segments:
            for seg in video_segments[1:]:
                try:
                    erosion += max(0.0, float(seg.get("transition_duration_sec", 0) or 0))
                except (TypeError, ValueError, AttributeError):
                    continue
        return max(0.0, base - erosion)

    @staticmethod
    def duration_deviation(actual: float, expected: float) -> float | None:
        """超出容差时返回偏差绝对值，否则 None。容差 = max(0.3s, 预期 × 1%)。

        预期为 0（时间线未声明时长，如仅音频/仅图片的边界场景）时不做判定。
        """
        try:
            a, e = float(actual or 0), float(expected or 0)
        except (TypeError, ValueError):
            return None
        if e <= 0:
            return None
        tol = max(0.3, e * 0.01)
        diff = abs(a - e)
        return diff if diff > tol else None

    @staticmethod
    def _atomic_temp_path(final_path: str) -> str:
        """原子替换用的临时路径：把媒体扩展名保留在**末尾**。

        ffmpeg 只按输出文件名的后缀推断 muxer（除非显式 `-f`）。因此
        `trim_x.mp4.part` 会失败（"Error initializing the muxer"），而
        `trim_x.part.mp4` 正常——2026-09-05 的原子替换改造踩过这个坑，
        导致所有 trim 失败并静默降级为 fallback 板。
        """
        p = Path(str(final_path))
        return str(p.with_name(f"{p.stem}.part{p.suffix}")) if p.suffix else f"{p}.part"

    def _source_valid(self, src: str) -> bool:
        """检查源视频文件是否可解码（有视频流且时长可读）。"""
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=codec_type:format=duration", "-of", "json", src],
                capture_output=True, text=True, timeout=15,
            )
            if r.returncode != 0:
                return False
            j = json.loads(r.stdout)
            streams = j.get("streams", []) or []
            dur = j.get("format", {}).get("duration")
            return bool(streams) and dur not in (None, "", "N/A")
        except Exception:
            return False

    def _generate_fallback(self, dur, width, height, fps, idx):
        out = str(self._work_dir / f"fallback_{idx}.{_current_ext()}")
        try:
            # 超时按时长缩放：长片段（如 113s）色块编码超过固定 30s 会被误杀
            timeout = 30 + int(dur) * 2
            self._run_ff(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                           f"color=c=0x1a1a2e:s={width}x{height}:d={dur}",
                           "-c:v", _current_encoder(), "-pix_fmt", _current_pix_fmt(), "-r", str(fps), out],
                          capture_output=True, text=False, timeout=timeout)
            return out if _is_valid_video(out) else None
        except Exception:
            return None

    # ── 拼接 ──────────────────────────────────────

    @staticmethod
    def _concat_copy_enabled() -> bool:
        """无转场时的无损拼接开关（`CLIPWRIGHT_CONCAT_COPY=0` 可关闭）。"""
        return (os.environ.get("CLIPWRIGHT_CONCAT_COPY") or "1").strip().lower() not in (
            "0", "false", "off", "no",
        )

    @staticmethod
    def _video_signature(path: str) -> tuple | None:
        """片段视频流指纹（codec / pix_fmt / WxH / fps / SAR）；探测失败返回 None。

        concat demuxer 要求所有输入的视频流参数完全一致，否则 `-c copy` 拼接会产出
        花屏或直接失败——因此拼接前必须逐个比对指纹。
        """
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                 "stream=codec_name,pix_fmt,width,height,avg_frame_rate,sample_aspect_ratio",
                 "-of", "json", path],
                capture_output=True, text=True, timeout=15,
            )
            if r.returncode != 0:
                return None
            streams = (json.loads(r.stdout) or {}).get("streams") or []
            if not streams:
                return None
            s = streams[0]
            return (
                s.get("codec_name"), s.get("pix_fmt"), s.get("width"), s.get("height"),
                s.get("avg_frame_rate"), s.get("sample_aspect_ratio") or "1:1",
            )
        except Exception:
            return None

    def _try_concat_copy(self, paths: list[str]) -> str | None:
        """V1 规则 2：concat demuxer + `-c copy` 无损拼接，避免二次编码。

        裁剪阶段已把每个片段编码成**最终**参数（分辨率/fps/编码器/pix_fmt/SAR 统一），
        因此无转场时可以直接流拷贝拼接：成片少一代编码损失，且省掉一次全片重编码。
        任一环节不满足（参数不一致 / copy 失败 / 开关关闭）返回 None，
        调用方回退既有的 filter 拼接，行为不退化。
        """
        if len(paths) < 2 or not self._concat_copy_enabled():
            return None
        sigs = [self._video_signature(p) for p in paths]
        if any(s is None for s in sigs) or len(set(sigs)) != 1:
            self._final_ffmpeg_log.append("concat: 片段参数不一致，回退 filter 拼接")
            return None

        def _esc(p: str) -> str:
            # 必须用**绝对路径**：ffconcat 的相对路径按列表文件所在目录解析，
            # 而 trim 片段路径是相对 CWD 的（_cache/tmp/trim_cache/...）→ 会找不到文件。
            # 同时统一成正斜杠并用单引号包裹（ffconcat 语法，内部单引号写成 '\''）。
            return "'" + Path(p).resolve().as_posix().replace("'", "'\\''") + "'"

        list_path = self._work_dir / "concat_list.txt"
        try:
            list_path.write_text(
                "".join(f"file {_esc(p)}\n" for p in paths), encoding="utf-8",
            )
            out = self._work_dir / f"concat_copy.{_current_ext()}"
            r = self._run_ff(
                ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                 "-i", str(list_path), "-c", "copy", str(out)],
                capture_output=True, text=False, timeout=600,
            )
            if r.returncode == 0 and _is_valid_video(str(out), require_streams=True):
                self._final_ffmpeg_log.append(
                    f"concat: {len(paths)} 段无损拼接（-c copy）")
                return str(out)
            self._final_ffmpeg_log.append(
                "concat: copy 拼接失败，回退 filter 拼接（"
                f"{_sanitize_ffmpeg_error(getattr(r, 'stderr', b''))[:120]}）")
            return None
        except Exception as e:
            self._final_ffmpeg_log.append(f"concat: copy 拼接异常，回退 filter 拼接（{e}）")
            return None

    async def _concat_segments(self, trimmed, segments, fps, bitrate, encoder, preset, progress_callback,
                               width: int = 1920, height: int = 1080):
        if not trimmed:
            return ""
        # start_sec 空洞黑帧填充——旧实现背靠背拼接，任何空洞之后的画面
        # 整体左移，而字幕/MG/音频按绝对时间定位 → 字幕盖错画面、A/V 错位
        filled: list[str] = []
        filled_segments: list[dict] = []
        cursor = 0.0
        gap_idx = 0
        for seg_path, seg in zip(trimmed, segments):
            start = float(seg.get("start_sec", 0) or 0)
            dur = float(seg.get("duration_sec", 0) or 0)
            if start > cursor + 0.05:
                black = self._generate_fallback(start - cursor, width, height, fps, f"gap{gap_idx}")
                if black:
                    filled.append(black)
                    # 批A(R4)：segments 同步插入占位段——否则 xfade 配对错位，
                    # 真实片段的 transition_in 落到黑帧接缝上
                    filled_segments.append({
                        "start_sec": cursor, "duration_sec": start - cursor,
                        "transition_in": "fade", "transition_duration_sec": 0.3,
                    })
                    self._final_ffmpeg_log.append(f"gap fill: {start - cursor:.2f}s 空洞黑帧填充")
            filled.append(seg_path)
            filled_segments.append(seg)
            cursor = max(cursor, start + dur)
            gap_idx += 1
        if len(filled) != len(trimmed):
            trimmed = filled
            segments = filled_segments
        if progress_callback:
            await progress_callback("concat", 55, f"拼接 {len(trimmed)} 个片段")

        if len(trimmed) == 1:
            f = str(self._work_dir / f"concat.{_current_ext()}"); shutil.copy2(trimmed[0], f); return f
        has_trans = any(segments[i].get("transition_in") for i in range(len(segments)) if i > 0)
        if not has_trans:
            # V1 规则 2：无转场 → 优先无损流拷贝拼接（省一代编码 + 一次全片重编码）；
            # 参数不一致或失败自动落到下面的 filter 拼接
            copied = await asyncio.to_thread(self._try_concat_copy, list(trimmed))
            if copied:
                if progress_callback:
                    await progress_callback("concat", 58, f"无损拼接 {len(trimmed)} 个片段（-c copy）")
                return copied
        if len(trimmed) == 2:
            # 修复：两片段时间线的转场此前被硬切短路
            if has_trans and await asyncio.to_thread(_ffmpeg_supports_xfade):
                return await self._concat_xfade_parallel(trimmed, segments, fps, bitrate, encoder, preset)
            return await self._ff_concat(self._run_concat, trimmed[0], trimmed[1], fps, bitrate, encoder, preset)

        if has_trans and await asyncio.to_thread(_ffmpeg_supports_xfade):
            # Phase 3.2: 分治并行拼接（替代 O(N) 串行全片重编）
            return await self._concat_xfade_parallel(trimmed, segments, fps, bitrate, encoder, preset)
        return await self._ff_concat(self._run_concat_all, trimmed, fps, bitrate, encoder, preset)

    def _run_concat(self, a, b, fps, bitrate, encoder, preset, out_name="concat.{ext}"):
        out_name = out_name.format(ext=_current_ext())
        out = Path(a).parent / out_name
        self._run_ff(["ffmpeg", "-y", "-loglevel", "error", *(_hwaccel_args(encoder)), "-i", a, "-i", b,
                       "-filter_complex", "[0:v]setsar=1[a];[1:v]setsar=1[b];[a][b]concat=n=2:v=1:a=0[v]",
                       "-map", "[v]", *_encoder_stage_args(encoder, preset),
                       "-b:v", bitrate, "-r", str(fps), str(out)],
                      capture_output=True, text=False, timeout=600)
        if _is_valid_video(out, require_streams=True):
            return str(out)
        self._final_ffmpeg_log.append(f"concat({Path(a).name}+{Path(b).name}): 输出无效，丢弃 {Path(b).name}")
        return a

    def _xfade_pair(self, left, right, tt, td, fps, bitrate, encoder, preset, out_name):
        """单次 xfade 合成（Phase 3.2：可并行）；输出无效时回退右片段。

        注意：调用方传进来的 out_name 形如 ``xr1_ab12cd.{ext}``，必须像
        ``_run_concat`` 一样先 ``.format(ext=…)``——否则输出文件名带字面量 ``{ext}``，
        ffmpeg 无法从 ``.{ext}`` 推断 muxer（"Error initializing the muxer"）→ 每次都
        静默降级硬切拼接，转场从未进入成片（连带使转场漂移校正把字幕/音频错误前移）。
        """
        out_name = str(out_name).format(ext=_current_ext())
        acc = _get_actual_duration(left)
        off = max(0, acc - td)
        out = str(self._work_dir / out_name)
        self._run_ff(["ffmpeg", "-y", "-loglevel", "error", *(_hwaccel_args(encoder)), "-i", left, "-i", right,
                       "-filter_complex",
                       f"[0:v]setsar=1[a];[1:v]setsar=1[b];[a][b]xfade=transition={tt}:duration={td}:offset={off}[v]",
                       "-map", "[v]", *_encoder_stage_args(encoder, preset),
                       "-b:v", bitrate, "-r", str(fps), out],
                      capture_output=True, text=False, timeout=600)
        if _is_valid_video(out, require_streams=True):
            return out
        self._final_ffmpeg_log.append(f"xfade({Path(left).name}×{Path(right).name}): 输出无效，降级硬切拼接")
        fallback = self._run_concat(left, right, fps, bitrate, encoder, preset,
                                    out_name=f"xc_{uuid.uuid4().hex[:6]}.{{ext}}")
        if _is_valid_video(fallback, require_streams=True) and fallback != left:
            return fallback
        self._final_ffmpeg_log.append(f"xfade({Path(left).name}×{Path(right).name}): 硬切降级仍失败，保留右片段")
        return right

    async def _concat_xfade_parallel(self, trimmed, segments, fps, bitrate, encoder, preset):
        """Phase 3.2: 分治并行拼接 — 每轮两两 xfade（asyncio.gather 并行投递线程池），
        把 O(N) 串行全片重编降为 O(log N) 轮（每轮并行），10 段转场拼接的等效
        全片重编码次数从 ~4.5 次降到 ~2.4 次。

        过渡名/时长白名单校验与旧串行路径一致（P0-3 注入防护不退化）。
        """
        from clipwright.animation.xfade_map import XFADE_VALUES

        items: list[dict] = []
        for i, p in enumerate(trimmed):
            tt_raw = segments[i].get("transition_in", "fade") if i < len(segments) else "fade"
            # 精确集合校验——原 regex 形状校验放行任意合法形状名（如 LLM
            # 语义名 crossfade/zoom_in），ffmpeg EINVAL 后 _xfade_pair 静默回退
            # 硬切，LLM 转场决策从未生效；非法名降级 fade 并记录
            if tt_raw and str(tt_raw) in XFADE_VALUES:
                tt = str(tt_raw)
            else:
                self._final_ffmpeg_log.append(f"transition({str(tt_raw)[:40]!r}) 不在 xfade 白名单，降级 fade")
                tt = "fade"
            try:
                td = float(segments[i].get("transition_duration_sec", 0.4) if i < len(segments) else 0.4)
            except (TypeError, ValueError):
                td = 0.4
            items.append({
                "path": p,
                "acc": _get_actual_duration(p),
                "tt": tt,
                "td": max(0.0, min(td, 60.0)),
            })

        round_no = 0
        while len(items) > 1:
            round_no += 1
            pairs = [(items[j], items[j + 1]) for j in range(0, len(items) - 1, 2)]
            odd = items[-1] if len(items) % 2 == 1 else None

            async def _merge(pair: tuple[dict, dict]) -> dict:
                left, right = pair
                # td<0.2 视为无转场；转场时长不得超过左块时长（否则 offset 归 0
                # → xfade EINVAL → 旧实现丢整个左块）
                if right["td"] < 0.2 or right["td"] >= left["acc"]:
                    out = await self._ff_concat(
                        self._run_concat, left["path"], right["path"], fps, bitrate,
                        encoder, preset, f"cc{round_no}_{uuid.uuid4().hex[:6]}.{{ext}}")
                    if _is_valid_video(out, require_streams=True):
                        return {"path": out, "acc": left["acc"] + right["acc"],
                                "tt": right["tt"], "td": right["td"]}
                    self._final_ffmpeg_log.append(
                        f"concat({Path(left['path']).name}+{Path(right['path']).name}): 硬切失败，保留右块")
                    return right
                out = await self._ff_concat(
                    self._xfade_pair, left["path"], right["path"], right["tt"], right["td"],
                    fps, bitrate, encoder, preset, f"xr{round_no}_{uuid.uuid4().hex[:6]}.{{ext}}",
                )
                return {
                    "path": out,
                    "acc": left["acc"] + right["acc"] - right["td"],
                    "tt": right["tt"],
                    "td": right["td"],
                }

            merged = await asyncio.gather(*(_merge(p) for p in pairs))
            items = list(merged) + ([odd] if odd else [])

        final = str(self._work_dir / f"concat.{_current_ext()}")
        if _is_valid_video(items[0]["path"], require_streams=True):
            shutil.copy2(items[0]["path"], final)
            return final
        self._final_ffmpeg_log.append("xfade 分治拼接最终产物无效，仅保留首片段（后续内容缺失）")
        return trimmed[0]

    def _run_concat_all(self, clips, fps, bitrate, encoder, preset):
        out = Path(clips[0]).parent / f"concat.{_current_ext()}"
        inputs = sum([["-i", f] for f in clips], [])
        n = len(clips)
        # 每个输入先 setsar=1 归一化，保证 concat 输入参数一致（含旧缓存里 SAR 未归一化的片段）
        flt = "".join(f"[{i}:v]setsar=1[v{i}];" for i in range(n)) + "".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[v]"
        timeout = 120 + n * 60  # 120s base + 60s per segment
        self._run_ff(["ffmpeg", "-y", "-loglevel", "error", *(_hwaccel_args(encoder)), *inputs,
                       "-filter_complex", flt, "-map", "[v]",
                       *_encoder_stage_args(encoder, preset),
                       "-b:v", bitrate, "-r", str(fps), str(out)],
                      capture_output=True, text=False, timeout=timeout)
        if _is_valid_video(out, require_streams=True):
            return str(out)
        # 修复：旧实现失败只返回最后一个片段（其余全部丢失）。降级两两串接，
        # 尽可能保留已成功拼接的部分，并逐段记录丢失
        self._final_ffmpeg_log.append(f"concat_all({n} 段) 输出无效，降级两两串接")
        acc = clips[0]
        for idx, nxt in enumerate(clips[1:], start=1):
            merged = self._run_concat(acc, nxt, fps, bitrate, encoder, preset,
                                      out_name=f"cc_all_{idx}_{uuid.uuid4().hex[:6]}.{{ext}}")
            if _is_valid_video(merged, require_streams=True):
                acc = merged
            else:
                self._final_ffmpeg_log.append(f"concat 降级：第 {idx + 1} 段未能并入（该段内容缺失）")
        return acc

    # ── M2: concat + text 合并 ────────────────────

    async def _compose_video_stages(self, video, text_overlays, hf_ov_local, overlay_segments,
                                    encoder, preset, width, height, fps, progress_callback=None):
        """视频合成阶段的**固定顺序**（唯一入口，防止顺序被改回去）。

        1. 图解 + MG 动画（HyperFrames）
        2. 画中画
        3. 文本/字幕（**最后**）

        依据 browser-use/video-use 的硬规则 1：「字幕/文字必须在所有 overlay 之后」——
        否则 overlay 会盖住字幕（静默失败：成片里字幕被动画遮住且无任何日志）。
        对应测试：`tests/clipwright/test_render_stage_order.py`。
        """
        if video and await self._hyperframes_available_async():
            video = await self._apply_all_hyperframes(video, text_overlays, hf_ov_local,
                                                      width, height, fps, progress_callback)
        if video and overlay_segments:
            if progress_callback:
                await progress_callback("pip", 94, f"合成 {len(overlay_segments)} 个画中画")
            video = await self._apply_overlays_safe(video, overlay_segments, width, height)
        if video and text_overlays:
            # progress_base=95：文本最后执行，95 → 97 封顶，后接音频(98)/完成(99,100)
            video = await self._apply_text_concat(video, text_overlays, encoder, preset,
                                                  progress_callback, width=width, height=height,
                                                  progress_base=95.0)
        return video

    async def _apply_text_concat(self, video, overlays, encoder, preset, progress_callback=None,
                                 width=1920, height=1080, progress_base=None):
        """将文字叠加层烧录到视频（单次 re-encode）。

        默认走 ASS 路径（``-vf ass=<path>``）：全部 14 个样式字段真实生效。
        ``settings.caption_renderer == "drawtext"`` 时回退到旧 drawtext 滤镜链。

        progress_base: 进度基准（合成顺序调整后文本最后执行 → 传 97.5 保持单调不减）；
        None 时沿用历史语义（60→70），供既有调用方与测试保持零变化。
        """
        if _caption_renderer() == "drawtext":
            return await self._apply_text_concat_drawtext(video, overlays, encoder, preset,
                                                          progress_callback, width=width, height=height,
                                                          progress_base=progress_base)
        return await self._apply_text_ass(video, overlays, encoder, preset, progress_callback,
                                          width=width, height=height, progress_base=progress_base)

    @staticmethod
    def _text_progress_pct(base: float | None, done: int, total: int) -> float:
        """文本阶段的进度百分比。

        - ``base=None``：历史语义 60→70（既有调用方/测试零变化）；
        - 指定 base：从 base 线性推进到 **97.0** 封顶——文本在合成顺序里排最后，
          后接音频(98)/完成(99,100)，封顶保证全链路单调不减（AGENTS.md Bug4）。
        """
        ratio = min(done / max(total, 1), 1.0)
        if base is None:
            return 60 + ratio * 10
        start = min(float(base), 97.0)
        return min(97.0, start + ratio * max(0.5, 97.0 - start))

    async def _apply_text_ass(self, video, overlays, encoder, preset, progress_callback=None,
                              width=1920, height=1080, progress_base=None):
        """ASS 路径：全部文字 overlay → 单个 .ass 文件 + ``-vf ass=<relpath>``。

        字幕起止裁剪到成片实际时长（Bug：``_concat_xfade`` 的转场会缩短成片，
        Dialogue end 若超出实际时长，libass 直接不渲染 → 最后几秒字幕消失）。
        写文件用相对路径（ffmpeg 从 CWD=仓库根运行，同 ``_fonts/`` 哲学，
        规避 Windows 盘符冒号被 filter 解析器截断）。
        """
        from clipwright.tool.design import TextStyle
        try:
            actual_dur = await asyncio.to_thread(_get_actual_duration, str(video))
        except Exception:
            actual_dur = 0

        dialogues: list[str] = []
        style_ts: TextStyle | None = None
        for ov in overlays:
            if ov.get("renderer") == "hyperframes" or ov.get("diagram_params"):
                continue
            # 生产加固 1.6：长字幕自动拆分多 Dialogue（时间按字数比例分配），不再硬截断
            chunks = _split_long_text(ov.get("text") or "")
            if not chunks:
                continue
            start = float(ov.get("start_sec", 0) or 0)
            dur = float(ov.get("duration_sec", 3) or 3)
            # 裁剪到成片实际时长；never extend past start+duration（min 天然保证）
            end = min(start + dur, actual_dur) if actual_dur > 0 else start + dur
            end = max(end, start)
            total_chars = sum(len(c) for c in chunks) or 1
            style_d = ov.get("style", {})
            if style_d:
                # 优先 style dict（含 font），再补 clip 级 font 字段
                if "font" not in style_d and ov.get("font"):
                    style_d = {**style_d, "font": ov.get("font")}
                ts = TextStyle.from_dict(style_d)
            else:
                ts = TextStyle(
                    font_size=ov.get("font_size", 48), font_color=ov.get("font_color", "#ffffff"),
                    font=ov.get("font", ""),
                    stroke_width=ov.get("stroke_width", 0), position=ov.get("position", "bottom"),
                    offset_y=ov.get("offset_y", 0))
            if style_ts is None:
                style_ts = ts  # 样式取第一条 overlay（14 字段映射 1:1）
            cursor = start
            for chunk in chunks:
                w = (end - start) * len(chunk) / total_chars
                dialogues.append(ts.build_ass_dialogue(chunk, cursor, min(cursor + w, end)))
                cursor += w
        if not dialogues:
            return video

        if progress_callback:
            await progress_callback("text", self._text_progress_pct(progress_base, 0, len(dialogues)),
                                    f"烧录 {len(dialogues)} 条字幕/文字")

        # 分批：每批一个 .ass + 单次 FFmpeg（长视频数百秒单趟 5-15 分钟 → 超时放大）
        batch_size = 100
        current = video
        for bi in range(0, len(dialogues), batch_size):
            batch = dialogues[bi:bi + batch_size]
            ass_path = self._work_dir / f"subs_{bi}.ass"
            header = style_ts.build_ass_style(int(width), int(height))
            body = ("[Events]\n"
                    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
                    + "\n".join(batch) + "\n")
            ass_path.write_text(header + "\n\n" + body, encoding="utf-8")
            try:
                ass_arg = ass_path.resolve().relative_to(Path.cwd().resolve()).as_posix()
                if " " in ass_arg:
                    ass_arg = f"'{ass_arg}'"
            except ValueError:
                # work_dir 在仓库外/跨盘符（如系统临时目录）：旧实现退化为裸文件名，
                # ffmpeg 以 CWD 解析 → 找不到文件 → 整批字幕静默不入片（本机 e2e 冒烟
                # 用的正是临时 work_dir，字幕长期静默缺失）。
                # 绝对路径必须「转义盘符冒号 + 单引号包裹」：裸 `ass=C:/x.ass` 会被
                # filtergraph 以 `:` 切分，报 `Unable to parse "original_size" option`
                # （本机 ffmpeg 实测：'C\:/…' 引号形式 rc=0，其余 3 种形式全部失败）。
                _abs = ass_path.resolve().as_posix().replace(":", r"\:")
                ass_arg = f"'{_abs}'"
            out = str(self._work_dir / f"txt_{bi}.{_current_ext()}")
            cmd = ["ffmpeg", "-y", "-loglevel", "error", *(_hwaccel_args(encoder)), "-i", current,
                   "-vf", f"ass={ass_arg}",
                   *_encoder_stage_args(encoder, preset),
                   "-c:a", "copy", out]
            r = await self._ff(cmd, capture_output=True, text=False, timeout=1800)
            if r.returncode == 0 and _is_valid_video(out, require_streams=True):
                current = out
            else:
                # 修复：烧录失败原先静默通过 → 成片零字幕交付且无任何日志
                err_tail = _sanitize_ffmpeg_error(getattr(r, "stderr", b""))
                self._final_ffmpeg_log.append(f"字幕烧录批次 {bi} 失败（该批字幕缺失）: {err_tail[:200]}")
                logger.warning("字幕烧录批次 %d 失败，该批字幕未入片", bi)
            if progress_callback:
                done = bi + len(batch)
                pct = self._text_progress_pct(progress_base, done, len(dialogues))
                await progress_callback("text", pct, f"烧录 {done}/{len(dialogues)} 条字幕")
        return current

    async def _apply_text_concat_drawtext(self, video, overlays, encoder, preset, progress_callback=None,
                                          width=1920, height=1080, progress_base=None):
        """将 drawtext filter 叠加到视频（单次 re-encode）。旧路径，仅 fallback 使用。"""
        from clipwright.tool.design import TextStyle
        filters = []
        for ov in overlays:
            if ov.get("renderer") == "hyperframes" or ov.get("diagram_params"):
                continue
            f = self._build_drawtext_filter(ov, width=width, height=height)
            if f:
                filters.append(f)
        if not filters:
            return video

        if progress_callback:
            await progress_callback("text", self._text_progress_pct(progress_base, 0, len(filters)),
                                    f"烧录 {len(filters)} 条字幕/文字")

        # 分批，每批内所有 filter 以逗号连接，单次 FFmpeg 调用。
        # 长视频（数百秒）单趟重编码就需 5-15 分钟：batch 放大到单批 + 超时放大。
        batch_size = 100
        current = video
        for bi in range(0, len(filters), batch_size):
            batch = filters[bi:bi + batch_size]
            out = str(self._work_dir / f"txt_{bi}.{_current_ext()}")
            # 用 -vf 而非重新 -filter_complex，减少复杂度
            cmd = ["ffmpeg", "-y", "-loglevel", "error", *(_hwaccel_args(encoder)), "-i", current,
                   "-vf", ",".join(batch),
                   *_encoder_stage_args(encoder, preset),
                   "-c:a", "copy", out]
            r = await self._ff(cmd, capture_output=True, text=False, timeout=1800)
            if r.returncode == 0 and _is_valid_video(out, require_streams=True):
                current = out
            else:
                # 修复：烧录失败原先静默通过 → 成片零字幕交付且无任何日志
                err_tail = _sanitize_ffmpeg_error(getattr(r, "stderr", b""))
                self._final_ffmpeg_log.append(f"字幕烧录批次 {bi} 失败（该批字幕缺失）: {err_tail[:200]}")
                logger.warning("字幕烧录批次 %d 失败，该批字幕未入片", bi)
            if progress_callback:
                done = bi + len(batch)
                pct = self._text_progress_pct(progress_base, done, len(filters))
                await progress_callback("text", pct, f"烧录 {done}/{len(filters)} 条字幕")
        return current

    # ── S2: 单次 Hyperframes 批量 ──────────────────

    async def _apply_all_hyperframes(self, video, text_overlays, hf_ov_local, width, height, fps,
                                     progress_callback=None):
        """将图解动画 + MG 动画合并到单次 Hyperframes 调用。

        Phase 1: MG HTML → MOV 有界并发渲染（信号量限制 Chrome 实例并发数）。
        Phase 2: 全部 MG MOV → 单次 filter_complex 链式 overlay（对比旧版 N 次
                 全片 re-encode）。进度回调保持单调不减：70 → 90 → 95 → 96。
        """
        from clipwright.animation.hyperframes_renderer import HyperframesRenderer
        overlays = [o for o in (text_overlays or [])
                    if o.get("renderer") == "hyperframes" or o.get("diagram_params")]

        # MG HTML → MOV 并行渲染 (Phase 1: 有界信号量并发)
        if hf_ov_local:
            total = max(len(hf_ov_local), 1)
            if progress_callback:
                await progress_callback("mg", 70, f"渲染 {len(hf_ov_local)} 个 MG 动画")
            # Chrome 渲染是重资源操作：限制同时运行的实例数（默认 6，可用
            # material_concurrency 配置覆盖），避免 44 个 MG 一次性打爆内存。
            # 合并：pipeline_concurrency（远程线键名）优先，回退 material_concurrency。
            try:
                from clipwright.config import settings
                limit = max(1, int(getattr(settings, "pipeline_concurrency", None)
                                   or getattr(settings, "material_concurrency", 6) or 6))
            except Exception:
                limit = 6
            sem = asyncio.Semaphore(limit)
            completed = 0

            async def _render_one(mg_ov):
                nonlocal completed
                async with sem:
                    res = await self._render_mg_mov(mg_ov, width, height, fps)
                completed += 1
                if progress_callback:
                    pct = 70 + (completed / total) * 20  # 70 → 90 单调递增
                    await progress_callback("mg", pct, f"渲染 MG {completed}/{len(hf_ov_local)}")
                return res

            movs = await asyncio.gather(*(_render_one(m) for m in hf_ov_local),
                                        return_exceptions=True)
            # /M2: Phase 2 改单次 filter_complex 链式合成（N 个 MOV 只做 1 次
            # 全片重编，替代串行 N 次）；MOV>8 或命令行超限时由 chained 内部
            # 递归拆批；链式失败回退 per-MOV 逐个叠加。
            pairs = [(mov, float(mg.get("start_sec", 0) or 0),
                      float(mg.get("duration_sec", 0) or 0))
                     for mg, mov in zip(hf_ov_local, movs)
                     if not isinstance(mov, BaseException) and mov and _is_valid_video(mov)]
            if pairs:
                if progress_callback:
                    await progress_callback("mg", 90, "链式叠加 MG 动画")
                chained = await self._apply_mg_overlay_chained(
                    video, pairs, width, height, fps, progress_callback=progress_callback)
                if chained != video:
                    video = chained
                else:
                    logger.warning("R5: 链式 MG 叠加失败，回退 per-MOV 逐个叠加")
                    for mg, mov in zip(hf_ov_local, movs):
                        if isinstance(mov, BaseException):
                            continue
                        if mov and _is_valid_video(mov):
                            video = await self._apply_mg_overlay(
                                video, mov, width, height, fps,
                                start_sec=float(mg.get("start_sec", 0) or 0),
                                duration_sec=(float(mg.get("duration_sec", 0) or 0) or None))
            if progress_callback:
                await progress_callback("mg", 92, "MG 叠加完成")

        if overlays:
            if progress_callback:
                await progress_callback("mg", 93, "合成图解动画叠加层")
            mov = str(self._work_dir / "overlay.mov")
            ok = await HyperframesRenderer.render_overlays(overlays, mov, width, height, fps)
            if ok and _is_valid_video(mov):
                out_v = str(self._work_dir / f"with_hf.{_current_ext()}")
                ok2 = HyperframesRenderer.render_overlay_on_video(mov, video, out_v)
                if ok2 and _is_valid_video(out_v):
                    video = out_v
        return video

    async def _render_mg_mov(self, mg_ov: dict, width: int, height: int, fps: float) -> str | None:
        """(a)+(b) 单个 MG HTML → 独立工作目录 → npx hyperframes render 产出 MOV。

        P1-01: 核心逻辑抽到 HyperframesRenderer.render_mg_html_to_mov 供预览端点复用，
        本方法注入 _ff 保持既有追踪/并发治理语义。
        """
        html = mg_ov.get("mg_html", "")
        if not html:
            return None

        from clipwright.animation.hyperframes_renderer import HyperframesRenderer
        mov = str(Path(self._work_dir) / f"mg_{uuid.uuid4().hex[:8]}" / "mg_out.mov")

        async def _run(cmd):
            return await self._ff(cmd, capture_output=True, text=False, timeout=1800,
                                  env=HyperframesRenderer._render_env())

        try:
            out = await HyperframesRenderer.render_mg_html_to_mov(
                html, self._work_dir, mov, fps=fps, run_cmd=_run,
                validate=lambda p: _is_valid_video(p),
                # P1-5：模板路径的 MG clip 存了 mg_params → 以上游变量覆盖渲染，
                # LLM 路径没有 params 时靠 HTML 里声明的 default（结果一致）。
                variables=mg_ov.get("mg_params") if isinstance(mg_ov.get("mg_params"), dict) else None,
            )
            if out and _is_valid_video(out):
                return out
        except Exception as e:
            logger.warning("MG render fail: %s", e)
        return None

    async def _apply_mg_overlay(self, video, mov: str, width: int, height: int, fps: float,
                                start_sec: float = 0.0, duration_sec: float | None = None) -> str:
        """(c) 将已渲染的 MOV overlay 合成到当前视频（严格串行链式）。

        注意：render_overlay_on_video 内部是阻塞的 subprocess.run（对整段视频 re-encode，
        单次可达数分钟），因此 offload 到 _ffmpeg_pool，避免冻结事件循环；
        overlay 顺序仍严格串行（每一轮依赖上一轮输出）。
        start_sec/duration_sec 传入 MOV 在主时间线上的时间窗口，让 overlay 只在
        MG clip 对应时段可见（Bug2 修复）。
        """
        from clipwright.animation.hyperframes_renderer import HyperframesRenderer
        try:
            out_v = str(self._work_dir / f"mg_{uuid.uuid4().hex[:4]}.{_current_ext()}")
            loop = asyncio.get_running_loop()
            ok = await loop.run_in_executor(
                _ffmpeg_pool, contextvars.copy_context().run,
                HyperframesRenderer.render_overlay_on_video, mov, video, out_v,
                start_sec, duration_sec,
            )
            if ok and _is_valid_video(out_v):
                return out_v
        except Exception as e:
            logger.warning("MG overlay fail: %s", e)
        return video

    async def _apply_mg_overlay_chained(
        self, video, movs: list[tuple[str, float, float]], width: int, height: int, fps: float,
        max_len: int = 30000, progress_callback=None, _counter: dict | None = None,
    ) -> str:
        """(c') 全部 MG MOV → 单次 filter_complex 链式 overlay（对比旧版 N 次全片 re-encode）。

        ``movs`` 为 ``(mov_path, start_sec, duration_sec)`` 元组列表，已按 start_sec 排序。
        每个 MOV 单独 ``-i`` 输入，先 scale/pad 到导出分辨率，再链式 overlay 到主视频，
        以 ``enable='between(t,...)'`` 限制到各自时间窗口；最终 ``-map`` 末级标签输出。

        - 缺失/损坏 MOV：调用方已过滤 None；此处再按文件存在性兜底跳过，
          链式图保证任何输入缺失都不断链、不使整次渲染失败。
        - cmdline 长度超过 ``max_len``（Windows 命令行限制）时拆成两半递归分批，
          每批仍为单次 ffmpeg 调用（M8：每批完成上报进度事件）。
        - 失败时回退返回原 ``video``，绝不让 MG 阶段拖垮整个导出。
        """
        # 合并决策：不再按 Path.exists() 预过滤——缺失/损坏 MOV 由上游
        # `_render_mg_mov` 返回 None / `_is_valid_video` 门控；此处保留 None
        # 过滤，真不存在的输入由 ffmpeg 失败回退兜底（返回原 video 不断链）。
        movs = [(m, s, d) for m, s, d in movs if m]
        if not movs:
            return video
        counter = _counter if _counter is not None else {"done": 0, "total": len(movs)}
        out = str(self._work_dir / f"mg_chain_{uuid.uuid4().hex[:8]}.{_current_ext()}")
        cmd = self._build_mg_chained_cmd(video, movs, width, height, out)
        length = len(" ".join(cmd))
        if length > max_len:
            logger.warning(
                "[Render] MG chained cmdline %d chars > %d，拆成两批链式叠加",
                length, max_len)
            mid = len(movs) // 2
            first = await self._apply_mg_overlay_chained(
                video, movs[:mid], width, height, fps, max_len=max_len,
                progress_callback=progress_callback, _counter=counter)
            result = await self._apply_mg_overlay_chained(
                first, movs[mid:], width, height, fps, max_len=max_len,
                progress_callback=progress_callback, _counter=counter)
            return result
        logger.info("[Render] MG chained overlay: %d inputs, %d chars",
                    len(movs), length)
        try:
            r = await self._ff(cmd, capture_output=True, text=False, timeout=3600)
            if r.returncode == 0 and _is_valid_video(out, require_streams=True):
                counter["done"] += len(movs)
                if progress_callback:
                    frac = 90 + min(counter["done"] / max(counter["total"], 1), 1.0) * 1
                    await progress_callback("mg", frac,
                                            f"链式叠加 MG {counter['done']}/{counter['total']}")
                return out
            logger.warning("[Render] MG chained overlay fail rc=%s", r.returncode)
        except Exception as e:
            logger.warning("MG chained overlay fail: %s", e)
        return video

    def _build_mg_chained_cmd(
        self, video, movs: list[tuple[str, float, float]], width: int, height: int, out: str,
    ) -> list[str]:
        """构建单次 ffmpeg 命令：主视频 + N 个 MOV 输入，链式 filter_complex overlay。

        filter_complex 形态（N 个有效 MOV）::

            [0:v]null[base];
            [1:v]scale=W:H:force_original_aspect_ratio=decrease,pad=W:H:(ow-iw)/2:(oh-ih)/2,setsar=1[mg1];
            [base][mg1]overlay=format=rgba:enable='between(t,s0,s0+d0)'[v1];
            [2:v]scale=...:pad=...[mg2];
            [v1][mg2]overlay=...:enable='between(t,s1,s1+d1)'[v2];
            ...
            -map "[vN]" -map 0:a?

        scale/pad 保证 MOV 尺寸对齐实际导出分辨率（大小安全落在链式图内，
        不改动 render_overlay_on_video 的逐 MOV 非链式回退路径）。
        """
        encoder = _current_encoder()
        preset = _current_preset()
        cmd = ["ffmpeg", "-y", "-loglevel", "error", *(_hwaccel_args(encoder)),
               "-i", str(video)]
        for mov, _start, _dur in movs:
            cmd += ["-i", str(mov)]
        parts = ["[0:v]null[base]"]
        prev = "base"
        for i, (mov, start_sec, duration_sec) in enumerate(movs, start=1):
            start = float(start_sec or 0)
            end = start + float(duration_sec or 0)
            parts.append(
                f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1[mg{i}]"
            )
            parts.append(
                # 实渲修复: overlay 的 format 选项是枚举（yuv420/yuv420p10/rgb/gbrp/auto），
                # `rgba` 非法会导致整条链式合成失败（rc=EINVAL）、MG 静默丢失。
                # 用 format=auto —— 与非链式路径 render_overlay_on_video 一致，alpha 由输入像素格式保留。
                f"[{prev}][mg{i}]overlay=format=auto:"
                f"enable='between(t,{_fmt_sec(start)},{_fmt_sec(end)})'[v{i}]"
            )
            prev = f"v{i}"
        cmd += ["-filter_complex", ";".join(parts),
                "-map", f"[{prev}]", "-map", "0:a?",
                *_encoder_stage_args(encoder, preset),
                "-c:a", "copy", str(out)]
        return cmd

    # ── drawtext 构建（同原版） ──────────────────

    def _build_drawtext_filter(self, ov, width: int = 0, height: int = 0):
        from clipwright.tool.design import TextStyle
        # 生产加固 1.6：不再硬截断 100 字；长文本在静态路径自动拆分顺序窗口
        text = (ov.get("text") or "").strip()
        if not text:
            return None
        start, dur = ov.get("start_sec", 0), ov.get("duration_sec", 3)
        anim, kfs = ov.get("anim_type", ""), ov.get("keyframes", []) or []
        style_d = ov.get("style", {})
        ts = TextStyle.from_dict(style_d) if style_d else TextStyle(
            font_size=ov.get("font_size", 48), font_color=ov.get("font_color", "#ffffff"),
            stroke_width=ov.get("stroke_width", 0), position=ov.get("position", "bottom"),
            offset_y=ov.get("offset_y", 0))
        # 显式 fontfile 绕过 Windows fontconfig 缺失：优先片段指定字体（无盘符），否则用系统字体
        explicit_font = ov.get("font") or ""
        if explicit_font and Path(explicit_font).exists() and ":" not in explicit_font:
            font_file = explicit_font
        elif ts.font_weight == "bold":
            font_file = _resolve_bold_font(explicit_font)
        else:
            font_file = _resolve_system_font()
        font_arg = f":fontfile={font_file}" if font_file else ""

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
                    safe = escape_drawtext_text(ch)
                    parts.append(f"drawtext=text='{safe}'{font_arg}:fontsize={ts.font_size}:fontcolor={color_to_drawtext(ts.font_color)}:x={xp}:y=(h-text_h)/2:enable='between(t,{cs},{start+dur})'")
                if parts:
                    return ",\n".join(parts)
            # 超宽文本 → 降级为静态文字（fall through below）

        if kfs and len(kfs) >= 2 and len(text) <= 1000:
            # kf_time_base 标记区分来源——前端导出（clip_local）translate 为
            # 画幅比例；管线遗留（无标记）为像素
            return self._build_kf_drawtext(text, ts, start, dur, kfs, font_arg,
                                           marker=ov.get("kf_time_base"),
                                           frame_w=width or 0,
                                           frame_h=height or 0)
        # 生产加固 1.6：长文本 → 按字数比例拆成顺序 enable 窗口的多 drawtext
        chunks = _split_long_text(text)
        if len(chunks) > 1:
            total_chars = sum(len(c) for c in chunks) or 1
            parts = []
            cursor = float(start)
            for chunk in chunks:
                w = float(dur) * len(chunk) / total_chars
                parts.append(ts.build_drawtext_filter(chunk, cursor, w, font_file))
                cursor += w
            return ",\n".join(p for p in parts if p)
        base = ts.build_drawtext_filter(text, start, dur, font_file)
        if ts.glow_width > 0 and ts.glow_color:
            # glow 双通道：底层在前（同坐标/同 enable 窗口），主文本在后，`,` 同处一个 filtergraph
            xp, yp = ts.drawtext_position()
            glow = _build_glow_underlay(text, ts.font_size, ts.glow_color, ts.glow_width,
                                        font_arg, xp, yp,
                                        f"between(t,{start},{start + dur})")
            return f"{glow},{base}"
        return base

    @staticmethod
    def _build_kf_drawtext(text, ts, start_sec, duration_sec, keyframes, font_arg="",
                           marker: str | None = None, frame_w: int = 0, frame_h: int = 0):
        from clipwright.utils.keyframes_expr import normalize_keyframe_times, property_expression
        if not keyframes: return ""
        # 时间基归一化（前端 clip_local / 管线 absolute → 片段相对秒），
        # 表达式以成片时间轴 t 求值，整体偏移 +start_sec；easing 按段携带生效
        norm = normalize_keyframe_times(keyframes, duration_sec, start_sec,
                                        time_base_marker=marker)
        if len(norm) < 2:
            return ""
        times = [kf["time"] for kf in norm]
        s, e = min(times) + start_sec, max(times) + start_sec
        safe = escape_drawtext_text(text)
        pos = {"center": "(w-text_w)/2", "bottom": "(w-text_w)/2", "top": "(w-text_w)/2",
               "left": "20", "right": "w-text_w-20", "top_left": "20",
               "top_right": "w-text_w-20", "bottom_left": "20", "bottom_right": "w-text_w-20"}
        ymp = {"center": "(h-text_h)/2", "top": "20", "bottom": "h-text_h-20",
               "left": "(h-text_h)/2", "right": "(h-text_h)/2",
               "top_left": "20", "top_right": "20",
               "bottom_left": "h-text_h-20", "bottom_right": "h-text_h-20"}
        bx = pos.get(ts.position, "(w-text_w)/2")
        by = ymp.get(ts.position, "(h-text_h)/2")

        # 缓动感知的分段插值表达式（前端 translate 为画幅比例 → ×w/×h；
        # 管线像素 translate 直接加）。window 缺失时回退 1，比例单位退化为像素
        unit_x = f"*{frame_w}" if (marker == "clip_local" and frame_w) else ""
        unit_y = f"*{frame_h}" if (marker == "clip_local" and frame_h) else ""
        a = property_expression(norm, "opacity", 1.0, t_offset=start_sec) or "1"
        xo = property_expression(norm, "translate_x", 0.0, t_offset=start_sec)
        yo = property_expression(norm, "translate_y", 0.0, t_offset=start_sec)
        sc = property_expression(norm, "scale_x", 1.0, t_offset=start_sec)
        xo_s = f"(({xo})){unit_x}" if xo else "0"
        yo_s = f"(({yo})){unit_y}" if yo else "0"
        fs = f"({ts.font_size})*({sc})" if sc else str(ts.font_size)
        enable = f"between(t,{s},{max(e,start_sec+duration_sec)})"
        parts = [f"drawtext=text='{safe}'{font_arg}", f"fontsize={fs}", f"fontcolor={color_to_drawtext(ts.font_color)}",
                 f"x={bx}+({xo_s})", f"y={by}+({yo_s})", f"alpha={a}",
                 # 与其余 key=value 一致，不加前导冒号——旧写法经 ":".join 产生
                 # "alpha=1.0::enable=…"，ffmpeg 报 "No option name near ':enable'"
                 # 导致整批 drawtext 失败、文字静默消失。
                 f"enable='{enable}'"]
        if ts.stroke_width > 0:
            parts.append(f"borderw={ts.stroke_width}:bordercolor={color_to_drawtext(ts.stroke_color)}")
        if ts.shadow_x != 0 or ts.shadow_y != 0:
            parts.append(f"shadowx={ts.shadow_x}:shadowy={ts.shadow_y}:shadowcolor={color_to_drawtext(ts.shadow_color)}")
        main = ":".join(parts)
        if ts.glow_width > 0 and ts.glow_color:
            # glow 双通道：底层在前（同坐标/同 enable 窗口/随缩放），主文本在后
            glow = _build_glow_underlay(text, fs, ts.glow_color, ts.glow_width, font_arg,
                                        f"{bx}+({xo})", f"{by}+({yo})", enable, alpha=a)
            return f"{glow},{main}"
        return main

    # ── overlay / audio（同原版精简）─────────────

    async def _apply_overlays_safe(self, video, segments, width, height):
        out = str(self._work_dir / f"ov.{_current_ext()}")
        try:
            await self._apply_overlays(video, segments, out, width, height)
            return out if _is_valid_video(out) else video
        except Exception as e:
            logger.warning("画中画合成失败，跳过覆盖层: %s", e)
            return video

    async def _apply_overlays(self, input_video, overlays, output_path, tw, th):
        if not overlays:
            shutil.copy2(input_video, output_path); return
        encoder = _current_encoder(); preset = _current_preset()
        filters = []
        base = "[0:v]"
        used = 0
        for i, ov in enumerate(overlays):
            src, dur, start, opacity = ov.get("source_path",""), ov.get("duration_sec",5), ov.get("start_sec",0), ov.get("opacity",1.0)
            rect = ov.get("image_rect") or {"x":0.65,"y":0.05,"w":0.3,"h":0.3}
            if not src or not Path(src).exists(): continue
            used += 1
            ow, oh = int(tw*rect["w"]), int(th*rect["h"])
            ox, oy = int(tw*rect["x"]), int(th*rect["y"])
            # blend_mode 落地——screen/multiply/overlay 三种混合模式经
            # blend 滤镜实现（裁剪底图区域 → 混合 → 贴回）；normal/未知值走
            # 原 overlay 直叠。原实现该字段从不消费。
            mode = str(ov.get("blend_mode") or "normal").lower()
            chain_head = f"[{i+1}:v]"
            if mode in ("screen", "multiply", "overlay"):
                filters.append(
                    f"{chain_head}scale={ow}:{oh},format=yuv420p[ov{i}];"
                    f"{base}crop={ow}:{oh}:{ox}:{oy},format=yuv420p[cb{i}];"
                    f"[cb{i}][ov{i}]blend=all_mode={mode}:all_opacity={opacity}[bl{i}];"
                    f"{base}[bl{i}]overlay={ox}:{oy}:enable='between(t,{start},{start+dur})'[v{i}]"
                )
            else:
                filters.append(f"{chain_head}scale={ow}:{oh},format=rgba,colorchannelmixer=aa={opacity}[ov{i}];{base}[ov{i}]overlay={ox}:{oy}:enable='between(t,{start},{start+dur})'[v{i}]")
            base = f"[v{i}]"
        if not filters: shutil.copy2(input_video, output_path); return
        inputs = ["-i", input_video]
        for ov in overlays:
            s = ov.get("source_path","")
            if s and Path(s).exists(): inputs.extend(["-i", s])
        c = ";".join(filters)
        await self._ff(["ffmpeg","-y","-loglevel","error",*(_hwaccel_args(encoder)),*inputs,"-filter_complex",c,
                       "-map",f"[v{used-1}]","-map","0:a?",
                       *_encoder_stage_args(encoder, preset),
                       "-c:a","copy",output_path],
                      capture_output=True, text=False, timeout=1800)

    async def _mix_audio_safe(self, video, segments, audio_path, bitrate, ab, bgm_path,
                              video_cached: bool = False,
                              loudness_target: float | None = None,
                              loudnorm_enabled: bool = True):
        """混合音频（C12：失败必须标记而非静默静音成片）。返回 (video, failure_marker|None)。"""
        if not video or not Path(video).exists():
            return video, None
        out = str(self._work_dir / f"aud.{_current_ext()}")
        try:
            await self._mix_audio(video, segments, out, audio_path, ab, bgm_path, bitrate,
                                  video_cached=video_cached,
                                  loudness_target=loudness_target,
                                  loudnorm_enabled=loudnorm_enabled)
            if Path(out).exists() and _is_valid_video(out, require_streams=True):
                return out, None
            # 混合失败/输出无效 → 保留无声视频但标记失败
            return video, "audio_mix_failed"
        except Exception as e:
            logger.warning("音频混合失败，跳过音频: %s", e)
            return video, f"audio_mix_error: {str(e)[:120]}"

    # video-use 硬规则 3：每个切点 30ms 淡化，消除波形被硬切产生的爆音。
    # 本项目的画面片段是 -an 裁剪（不带源声），音频全部来自时间线上的音频片段，
    # 因此"切点"= 音频片段自身与静音相接的首尾边界。
    _CUT_FADE_DEFAULT_SEC = 0.03

    @classmethod
    def _apply_default_cut_fades(cls, voices: list[dict]) -> None:
        """给未显式设置淡化的音频片段补 30ms 切点淡化（原地修改）。

        只在"片段边界与静音相接"时补：
        - 同组（人声/音乐/整文件音源）中首尾相接的两段（例如逐句配音被切成多段）属于同一
          连续声源，补淡化会在接缝处制造 30ms 下陷；
        - 因此仅当同组内没有另一段的端点落在本段首/尾 ±50ms 内时才补。
        - ``fade_in``/``fade_out`` 为 ``None`` 表示"未设置"（补默认）；显式数值（含 0.0）
          表示调用方已决定，不做覆盖。

        方法结束时把 None 归一为数值，供下游 ``> 0`` 比较使用。
        """
        if not voices:
            return

        def _group(v: dict) -> str:
            if v.get("is_voice"):
                return "voice"
            if v.get("is_bgm"):
                return "bgm"
            return "other"

        eps = 0.05
        for v in voices:
            start = float(v.get("start") or 0)
            dur = float(v.get("dur") or 0)
            end = start + dur if dur > 0 else start
            gj = _group(v)
            touching_start = False
            touching_end = False
            for other in voices:
                if other is v or _group(other) != gj:
                    continue
                o_start = float(other.get("start") or 0)
                o_dur = float(other.get("dur") or 0)
                o_end = o_start + o_dur if o_dur > 0 else o_start
                if abs(o_end - start) <= eps:
                    touching_start = True
                if dur > 0 and abs(o_start - end) <= eps:
                    touching_end = True
            if v.get("fade_in") is None and not touching_start:
                v["fade_in"] = cls._CUT_FADE_DEFAULT_SEC
            if v.get("fade_out") is None and dur > 0 and not touching_end:
                v["fade_out"] = cls._CUT_FADE_DEFAULT_SEC
            # 归一为数值：下游用 `v["fade_in"] > 0` 直接比较
            v["fade_in"] = float(v.get("fade_in") or 0)
            v["fade_out"] = float(v.get("fade_out") or 0)

    async def _mix_audio(self, input_video, segments, output_path, afp="", ab="192k", bfp="", bitrate="5M",                         video_cached: bool = False, loudness_target: float | None = None,
                         loudnorm_enabled: bool = True):
        """混音。任一路径失败都会抛出/返回 False，由 _mix_audio_safe 统一标记。

        C11: 真实混音 — 所有音频片段按时间窗裁剪 + 各自音量 + 淡入淡出，
        延迟对齐后 amix，最终 loudnorm LUFS 归一；失败逐级回退。
        Phase 3.1: video_cached=True 时视频轨 -c:v copy（视频未变，仅重编音频），
        音频变更的迭代从「全片重编码」降为「仅音频编码」。
        """
        encoder = _current_encoder(); preset = _current_preset()

        # 收集实际存在的音源：显式配音 afp > BGM bfp > 时间线音频片段
        voices = []
        if afp and Path(afp).exists():
            voices.append({"path": afp, "volume": 1.0, "start": 0, "dur": 0,
                           "fade_in": None, "fade_out": None, "is_voice": True, "is_bgm": False})
        for seg in segments or []:
            s = seg.get("source_path", "")
            if s and Path(s).exists():
                _fin = seg.get("audio_fade_in_sec")
                _fout = seg.get("audio_fade_out_sec")
                voices.append({
                    "path": s,
                    "volume": float(seg.get("volume", 1.0) or 1.0),
                    "start": float(seg.get("start_sec", 0) or 0),
                    "dur": float(seg.get("duration_sec", 0) or 0),
                    # None = 未设置 → 由 _apply_default_cut_fades 补 30ms 切点淡化；
                    # 显式数值（含 0.0）= 调用方已决定，不覆盖
                    "fade_in": None if _fin is None else float(_fin or 0),
                    "fade_out": None if _fout is None else float(_fout or 0),
                    "is_voice": bool(seg.get("is_voice")),
                    "is_bgm": bool(seg.get("is_bgm")),
                    # 轮 95：语音降噪标记（afftdn 消费）
                    "denoise": bool(seg.get("denoise")),
                    # 源内偏移与时间线位置分离
                    "source_offset": float(seg.get("source_offset_sec", 0) or 0),
                    # 播放速率——atrim 时长按源时间换算 + atempo 补偿
                    "speed": float(seg.get("speed", 1) or 1),
                })
        if bfp and Path(bfp).exists():
            voices.append({"path": bfp, "volume": 0.3, "start": 0, "dur": 0,
                           "fade_in": None, "fade_out": None, "is_voice": False, "is_bgm": True})

        self._apply_default_cut_fades(voices)

        # 路径：≥2 个音源 → 多输入 amix + loudnorm（真实混音/LUFS）
        if len(voices) >= 2:
            try:
                inputs = ["ffmpeg", "-y", "-loglevel", "error", *( _hwaccel_args(encoder)),
                          "-i", input_video]
                for v in voices:
                    inputs += ["-i", v["path"]]
                chains = []
                mix_inputs: list[str] = []
                # 修复（1.1c）：整文件型音源（显式配音/BGM，dur=0）按画面时长裁齐，
                # 否则 duration=longest 下长 BGM 文件会把混音拖到远超画面
                vdur = await asyncio.to_thread(_get_actual_duration, str(input_video))
                # 阶段 1：每路音源独立预处理链 [i:a]→[a{i}]
                for i, v in enumerate(voices, start=1):
                    parts = []
                    # atrim 用「源内偏移」（素材文件内的起点），adelay 用时间线
                    # start_sec——此前两者混用导致源偏移素材播放错误区域
                    so = v.get("source_offset", 0)
                    # 修复：atrim 恒用「源内偏移」（默认 0）。旧实现把时间线 start
                    # 当源偏移，逐句配音文件（时长数秒、start=20s）被裁成空流 → 整段静音
                    atrim_start = so
                    sp = v.get("speed", 1) or 1
                    # 源时间 atrim 时长 = 时间线时长 × 速率；atempo（单级限
                    # 0.5–2.0，越界链式分级）把源片段压/展到时间线时长，与预览
                    # playbackRate 语义一致
                    trim_dur = v["dur"]
                    if trim_dur <= 0 and vdur > 0:
                        trim_dur = max(0.5, vdur - v["start"])
                    if trim_dur > 0:
                        parts.append(f"atrim=start={atrim_start}:duration={trim_dur * sp:.6f}")
                    # 轮 85：语音去噪——v.get("denoise") 为真时用 afftdn（FFT 降噪，
                    # 对人声友好；上传配音/嘈杂环境录制的旁白显著受益）
                    if v.get("denoise"):
                        parts.append("afftdn=nr=12:nf=-25")
                    if abs(sp - 1.0) > 1e-3:
                        remaining = sp
                        while remaining > 2.0:
                            parts.append("atempo=2.0")
                            remaining /= 2.0
                        while remaining < 0.5:
                            parts.append("atempo=0.5")
                            remaining /= 0.5
                        parts.append(f"atempo={remaining:.6f}")
                    if v["start"] > 0:
                        parts.append(f"adelay={int(v['start'] * 1000)}|{int(v['start'] * 1000)}")
                    # 修复：adelay 后 pts 含静音头部，fade 须用时间线绝对位置
                    # （旧实现 st 相对源，对 start>0 的片段淡的是静音区）
                    if v["fade_in"] > 0:
                        parts.append(f"afade=t=in:st={v['start']:.6f}:d={v['fade_in']}")
                    if v["fade_out"] > 0 and v["dur"] > 0:
                        fade_out_st = max(0.0, v["start"] + v["dur"] - v["fade_out"])
                        parts.append(f"afade=t=out:st={fade_out_st:.6f}:d={v['fade_out']}")
                    parts.append(f"volume={v['volume']}")
                    chains.append(f"[{i}:a]{','.join(parts)}[a{i}]")

                # 阶段 2：按角色路由（顺序无关）。D3 ducking：首个配音 asplit 出
                # N 份侧链（每路 BGM 独占一份，asplit 输出必须全部被消费，否则
                # ffmpeg 报 unconnected output —— 旧实现要求 BGM 排在人声之后，
                # 常见顺序下 C11 必败静默回退单音源）。
                voice_base = next((f"a{i}" for i, v in enumerate(voices, start=1)
                                   if v.get("is_voice")), "")
                bgm_bases = [f"a{i}" for i, v in enumerate(voices, start=1) if v.get("is_bgm")]
                ducked_bgms: set[str] = set()
                if voice_base and bgm_bases:
                    taps = bgm_bases[:3]  # 侧链上限 3（正常场景 1 配音 + 1 BGM）
                    split_out = "".join(f"[sc{k}]" for k in range(1, len(taps) + 1))
                    chains.append(f"[{voice_base}]asplit={len(taps) + 1}[mixv]{split_out}")
                    mix_inputs.append("mixv")
                    for k, bb in enumerate(taps, start=1):
                        chains.append(
                            f"[{bb}][sc{k}]sidechaincompress="
                            f"threshold=0.03:ratio=4:attack=25:release=400[mix{bb}]"
                        )
                        mix_inputs.append(f"mix{bb}")
                        ducked_bgms.add(bb)
                for i, v in enumerate(voices, start=1):
                    base = f"a{i}"
                    # 修复：仅当存在 BGM（mixv 侧链已建）时跳过作为侧链源的第一路
                    # 人声；其余人声必须直接进 amix——旧实现 continue 掉所有
                    # is_voice，逐句配音只有第一句有声
                    if v.get("is_voice") and voice_base == base and bgm_bases:
                        continue  # 该路已作为 mixv 混入（ducking 侧链源）
                    if v.get("is_bgm") and base in ducked_bgms:
                        continue  # 已走 sidechaincompress
                    mix_inputs.append(base)

                chains_mix_in = "".join(f"[{m}]" for m in mix_inputs)
                # duration=longest：混音覆盖全部人声窗（旧 first 在首句结束处
                # 截断，逐句配音只有第一句有声且 BGM 被截断）
                # 响度目标可由 Persona audio.target_loudness_lufs 指定（缺省 -16）
                _lufs = f"{loudness_target:g}" if loudness_target is not None else "-16"
                # 轮 95：loudnorm_enabled=false 时仅 amix，不做响度归一
                _tail = (f",loudnorm=I={_lufs}:LRA=11:TP=-1.5" if loudnorm_enabled else "")
                chains.append(
                    f"{chains_mix_in}amix=inputs={len(mix_inputs)}:duration=longest:normalize=0"
                    f"{_tail}[aout]"
                )
                r = await self._ff(inputs + [
                    "-filter_complex", ";".join(chains),
                    "-map", "0:v:0", "-map", "[aout]",
                    *(["-c:v", "copy"] if video_cached else
                      [*_encoder_stage_args(encoder, preset),
                       "-b:v", bitrate, *_delivery_extra_args(encoder)]),
                    "-c:a", "aac", "-b:a", ab, "-movflags", "+faststart", output_path,
                ], capture_output=True, text=False, timeout=1800)
                if _is_valid_video(output_path):
                    logger.info("C11 真实混音成功: %d 音源 + LUFS 归一", len(voices))
                    return
                # 可观测性：C11 失败 stderr 尾部进渲染日志（此前只记 warning，
                # 失败原因如 unconnected output 无从排查）
                self._final_ffmpeg_log.append(
                    f"mix_audio(C11): 输出无效 {_sanitize_ffmpeg_error(r.stderr)}")
                logger.warning("_mix_audio: C11 混音输出无效，回退单音源: %s", output_path)
            except Exception as e:
                logger.warning("_mix_audio: C11 混音异常，回退单音源: %s", str(e)[:200])

        # 回退：单音源直接混入（优先人声——BGM 在前时 voices[0] 可能是音乐，
        # 回退只保留一路时丢内容音频比丢伴奏更糟）
        if voices:
            v0 = next((v for v in voices if v.get("is_voice")), voices[0])
            voice = v0["path"]
            # 轮 84：单音源回退补齐音量/淡入淡出/响度归一——旧实现裸混入，
            # 仅 BGM 时 0.3 衰减失效（原电平炸耳）、仅旁白时无防爆音淡入
            vol = float(v0.get("volume", 1.0) or 1.0)
            fin = float(v0.get("fade_in") or 0)
            fout = float(v0.get("fade_out") or 0)
            afilters: list[str] = []
            if 0 < vol != 1.0:
                afilters.append(f"volume={vol:.3f}")
            if fin > 0:
                afilters.append(f"afade=t=in:st=0:d={fin:.2f}")
            if fout > 0:
                _vd = await asyncio.to_thread(_get_actual_duration, str(input_video))
                if _vd > 0:
                    afilters.append(f"afade=t=out:st={max(0.0, _vd - fout):.2f}:d={fout:.2f}")
            if loudnorm_enabled:
                _lufs_fb = f"{loudness_target:g}" if loudness_target is not None else "-16"
                afilters.append(f"loudnorm=I={_lufs_fb}:LRA=11:TP=-1.5")
            try:
                await self._ff(["ffmpeg","-y","-loglevel","error",*(_hwaccel_args(encoder)),"-i",input_video,"-i",voice,
                               "-af", ",".join(afilters),
                               *(["-c:v","copy"] if video_cached else
                                 ["-c:v",encoder,"-preset",preset,"-pix_fmt",_current_pix_fmt(),
                                  "-b:v",bitrate,*_delivery_extra_args(encoder)]),
                               "-c:a","aac","-b:a",ab,"-map","0:v:0","-map","1:a:0",
                               "-movflags","+faststart",output_path],
                              capture_output=True, text=False, timeout=600)
                if _is_valid_video(output_path): return
                logger.warning("_mix_audio: 配音混合输出无效: %s", output_path)
            except Exception as e:
                logger.warning("_mix_audio: 配音混合异常: %s", str(e)[:200])
        if not voices:
            logger.warning(
                "_mix_audio: 未找到配音或BGM音源（segments=%d, afp=%s, bfp=%s），输出视频将无声音: %s",
                len(segments or []), afp or "(none)", bfp or "(none)", output_path,
            )
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

    @staticmethod
    async def _hyperframes_available_async(timeout: float = 60.0):
        """异步探测 Hyperframes 是否可用（含冷启动等待，最多 timeout 秒）。

        与同步版不同：冷启动时 ``is_available()`` 底层缓存探针首次返回 default=False，
        会误跳过 HF 图解/MG 渲染。此方法轮询等待真实可用性，超时仍不可用才返回 False。
        """
        try:
            from clipwright.animation.hyperframes_renderer import HyperframesRenderer
            return await HyperframesRenderer.await_available(timeout)
        except Exception:
            return False

    def _cleanup(self):
        if self._work_dir.exists():
            shutil.rmtree(self._work_dir, ignore_errors=True)
        self._work_dir.mkdir(parents=True, exist_ok=True)
