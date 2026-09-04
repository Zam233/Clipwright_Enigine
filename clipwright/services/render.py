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
import json
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
    _track_proc(cid, proc)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        raise
    finally:
        _untrack_proc(cid, proc)
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


def _current_encoder() -> str:
    ov = _ENC_OVERRIDE.get()
    return ov or _get_encoder()


def _current_preset() -> str:
    ov = _PRESET_OVERRIDE.get()
    return ov or _get_preset()


def _current_pix_fmt() -> str:
    ov = _PFMT_OVERRIDE.get()
    return ov or "yuv420p"


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

def _is_valid_video(path: str | Path, min_bytes: int = 1024) -> bool:
    """ffmpeg 输出有效性：存在且非空（ffmpeg -y 失败也会留下 0 字节占位文件，
    仅检查 exists() 会把失败误判为成功，导致最终导出空视频）。"""
    try:
        p = Path(path)
        return p.exists() and p.stat().st_size >= min_bytes
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

# M1: 裁剪缓存（持久化目录，不随 render 清理删除）
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
        entries = sorted(_TRIM_CACHE_DIR.glob("trim_*.mp4"), key=lambda p: p.stat().st_mtime)
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
                    speed: float = 1.0, image_fit: str = "") -> str:
    # D1/D2: 速度与填充模式影响输出内容，必须进缓存键
    raw = f"{src}|{offset:.2f}|{dur:.2f}|{width}x{height}|{speed:.3f}|{image_fit}"
    return hashlib.md5(raw.encode()).hexdigest()


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
                     soft_subtitle_srt: str = "") -> RenderResult:
        # 审计 P0 修复：取消标识注入 context，线程池内 ffmpeg 调用可感知并 terminate
        _CURRENT_CANCEL_ID.set(cancel_id)
        # Phase 3.3: 单次渲染编码器/像素格式覆盖注入 context（随 ctx.run 传播）
        _ENC_OVERRIDE.set(encoder_override)
        _PRESET_OVERRIDE.set(preset_override)
        _PFMT_OVERRIDE.set(pix_fmt_override)
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
                        soft_subtitle_srt=soft_subtitle_srt)
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
                            soft_subtitle_srt: str = ""):
        video_segments, overlay_segments, text_overlays, audio_segments, hf_ov_local = \
            self._extract_segments(timeline)

        # D4: 软字幕模式——caption 类烧入滤镜剔除（字幕走 mov_text 轨封装），
        # TEXT/动画类覆盖物仍烧入
        if soft_subtitle_srt and Path(soft_subtitle_srt).exists():
            text_overlays = [t for t in text_overlays if t.get("category") != "caption"]

        if progress_callback:
            # Bug4：prepare 从 0 起步，保证全阶段进度单调不减（trim 阶段从 0→50）。
            await progress_callback("prepare", 0, "解析时间线")

        # S1: 并行裁剪
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
        if cached_stage.exists() and _is_valid_video(cached_stage):
            final_video = str(cached_stage)
            video_from_cache = True
            if progress_callback:
                await progress_callback("video", 60, "视频阶段命中缓存（音频可快路径混入）")
        else:
            # 拼接
            final_video = await self._concat_segments(trimmed, video_segments, fps, bitrate,
                                                      encoder, preset, progress_callback)

            # M2: concat+text+overlay 合并为单次 filter_complex
            if final_video and text_overlays:
                final_video = await self._apply_text_concat(final_video, text_overlays, encoder, preset,
                                                            progress_callback, width=width, height=height)

            # S2: HF 图解 + MG 动画 → 单次 Hyperframes 调用
            if final_video and self._hyperframes_available():
                final_video = await self._apply_all_hyperframes(final_video, text_overlays, hf_ov_local,
                                                                width, height, fps, progress_callback)

            # 画中画
            if final_video and overlay_segments:
                final_video = await self._apply_overlays_safe(final_video, overlay_segments, width, height)

            # 缓存 pre-audio 视频（供「仅音频变更」快路径复用）
            if final_video and _is_valid_video(final_video):
                try:
                    shutil.copy2(final_video, cached_stage)
                except Exception as e:
                    logger.warning("视频阶段缓存写入失败: %s", e)

        # 音频（C12：混合失败必须标记到结果，而非静默静音成片）
        audio_warnings: list[str] = []
        if final_video:
            final_video, mix_marker = await self._mix_audio_safe(
                final_video, audio_segments, audio_file_path, bitrate, audio_bitrate, bgm_file_path,
                video_cached=video_from_cache)
            if mix_marker:
                audio_warnings.append(mix_marker)
                logger.error("C12 音频混合失败已标记: %s (render=%s)", mix_marker, output)

        # 输出
        if final_video and _is_valid_video(final_video):
            if progress_callback:
                await progress_callback("done", 99, "输出成片")
            # D4: 软字幕封装——SRT 以 mov_text 轨挂入（失败回退硬拷贝无字幕轨）
            _srt_ok = False
            if soft_subtitle_srt and Path(soft_subtitle_srt).exists():
                try:
                    await self._ff(
                        ["ffmpeg", "-y", "-loglevel", "error", "-i", final_video,
                         "-i", soft_subtitle_srt,
                         "-map", "0:v:0", "-map", "0:a?", "-map", "1:0",
                         "-c:v", "copy", "-c:a", "copy", "-c:s", "mov_text", str(output)],
                        capture_output=True, text=False, timeout=600)
                    _srt_ok = _is_valid_video(output)
                except Exception as e:
                    logger.warning("D4 软字幕封装失败，回退无字幕轨: %s", e)
            if not _srt_ok:
                shutil.copy2(final_video, str(output))
            if not _is_valid_video(output):
                logger.error("渲染输出为空文件: %s（源 %s）", output, final_video)
                return RenderResult(False, error=f"渲染输出为空文件 (final={final_video})")
            dur = await asyncio.to_thread(_get_actual_duration, str(output))
            if progress_callback:
                await progress_callback("done", 100, "渲染完成")
            logger.info("渲染完成: %s (%.1fs)", output, dur)
            # D6/D7: fallback 计数与实际时长偏差显式暴露（原先静默）
            warnings = list(audio_warnings)
            if self._fallback_count:
                warnings.append(f"{self._fallback_count} 个片段源不可用，已降级纯色段")
            try:
                tl_dur = float(getattr(timeline, "duration_sec", 0) or 0)
            except (TypeError, ValueError):
                tl_dur = 0.0
            if tl_dur > 0 and abs(dur - tl_dur) > max(1.0, tl_dur * 0.05):
                warnings.append(
                    f"成片实际时长 {dur:.1f}s 与时间线 {tl_dur:.1f}s 偏差较大"
                    f"（转场时长侵蚀/时长字段未校正）")
            return RenderResult(True, output_path=str(output.resolve()), duration_sec=dur,
                                warnings=warnings)

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
                             blend_mode=getattr(clip, 'blend_mode', None),
                             # D2: 死字段接线——image_fit/mask 此前渲染零消费
                             image_fit=str(getattr(clip, 'image_fit', None) or ''),
                             mask_type=getattr(clip, 'mask_type', None),
                             mask_rect=clip.mask_rect,
                             # C11/M6: 音频淡入淡出
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
                    (overlay_segments if is_overlay else video_segments).append(entry)
                elif k == "audio":
                    entry["source_path"] = clip.asset_id
                    # D3: 音源角色标记——供混音阶段做 BGM 自动避让（ducking）
                    _am = clip.metadata or {}
                    entry["is_bgm"] = bool(_am.get("bgm"))
                    entry["is_voice"] = bool(_am.get("narration") or _am.get("dubbing"))
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
        pos = meta.get("position", style.get("position", {1: "bottom", 2: "top", 3: "center"}.get(track_idx, "bottom")))
        # Bug1 修复：不再按同轨字幕数量累积偏移（旧逻辑会导致后出现的字幕逐条上移直至消失）。
        # 同一 track 的字幕 y 固定为 0（同一时刻多条字幕同高度，避免累加漂移）。
        y_off = 0
        # 基础样式并入 style dict：clip 显式字段优先，缺失时回退 meta.style / 默认值
        style.setdefault("font_size", clip.font_size or 48)
        style.setdefault("font_color", clip.font_color or "#ffffff")
        style.setdefault("position", pos)
        style.setdefault("offset_y", y_off)
        # 任务 31：新样式字段直接从 Clip 读取并合并进 style dict（clip 优先，meta.style 兜底）
        for f in _CLIP_STYLE_FIELDS:
            v = getattr(clip, f, None)
            if v is not None:
                style[f] = v
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

            # M1: 缓存（D1/D2: speed 与 image_fit 进键）
            image_fit = str(seg.get("image_fit", "") or "").lower()
            cache_key = _trim_cache_key(src, seg.get("source_offset", 0), dur, width, height,
                                        speed, image_fit)
            with _trim_cache_lock:
                cached = _trim_cache.get(cache_key)
                if cached:
                    _trim_cache.move_to_end(cache_key)  # LRU touch
            if cached and Path(cached).exists():
                return cached

            out = str(_TRIM_CACHE_DIR / f"trim_{cache_key}.mp4")
            try:
                # D1: speed≠1 真实变速——原实现只按速度改时长并用 stream_loop
                # 循环填充，播放速度不变（setpts 只存在于 tool/speed.py，未接入
                # 时间线渲染）。trim 输出为 -an 无音轨，仅视频 setpts 即可；
                # stream_loop 保留以在变速后仍能填满目标时长。
                speed_prefix = f"setpts=PTS/{speed:.4f}," if abs(speed - 1.0) > 0.01 else ""
                # D2: image_fit 落地——COVER=裁满（increase+crop），CONTAIN=信箱
                # （decrease+pad，与原行为一致）；原实现两模式都走 pad 分支。
                if image_fit == "contain":
                    fit_scale = (f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                                 f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1")
                else:
                    fit_scale = (f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                                 f"crop={width}:{height},setsar=1")
                vf = speed_prefix + fit_scale

                # D2: mask_type=rectangle 落地——按归一化 mask_rect 裁剪后还原画幅
                mask_rect = seg.get("mask_rect")
                if seg.get("mask_type") == "rectangle" and isinstance(mask_rect, dict):
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

                cmd = ["ffmpeg", "-y", "-loglevel", "error", *(_hwaccel_args(encoder)),
                       "-ss", str(seg.get("source_offset", 0)),
                       "-stream_loop", "-1", "-i", src, "-t", str(dur), "-vf", vf, "-r", str(fps),
                       "-c:v", encoder, "-pix_fmt", _current_pix_fmt(),
                       "-preset", preset, "-b:v", bitrate, "-an", out]
                r = self._run_ff(cmd, capture_output=True, text=False, timeout=600)
                if r.returncode == 0 and _is_valid_video(out):
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
        trimmed = [r for r in results if r and Path(r).exists()]

        if progress_callback:
            await progress_callback("trim", 50, f"完成 {len(trimmed)}/{len(segments)} 裁剪")
        return trimmed

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
        out = str(self._work_dir / f"fallback_{idx}.mp4")
        try:
            # 超时按时长缩放：长片段（如 113s）色块编码超过固定 30s 会被误杀
            timeout = 30 + int(dur) * 2
            self._run_ff(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                           f"color=c=0x1a1a2e:s={width}x{height}:d={dur}",
                           "-c:v", _resolve_encoder(), "-pix_fmt", _current_pix_fmt(), "-r", str(fps), out],
                          capture_output=True, text=False, timeout=timeout)
            return out if _is_valid_video(out) else None
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
            # Phase 3.2: 分治并行拼接（替代 O(N) 串行全片重编）
            return await self._concat_xfade_parallel(trimmed, segments, fps, bitrate, encoder, preset)
        return await self._ff_concat(self._run_concat_all, trimmed, fps, bitrate, encoder, preset)

    def _run_concat(self, a, b, fps, bitrate, encoder, preset):
        out = Path(a).parent / "concat.mp4"
        self._run_ff(["ffmpeg", "-y", "-loglevel", "error", *(_hwaccel_args(encoder)), "-i", a, "-i", b,
                       "-filter_complex", "[0:v]setsar=1[a];[1:v]setsar=1[b];[a][b]concat=n=2:v=1:a=0[v]",
                       "-map", "[v]", "-c:v", encoder, "-preset", preset,
                       "-b:v", bitrate, "-r", str(fps), str(out)],
                      capture_output=True, text=False, timeout=600)
        return str(out) if _is_valid_video(out) else a

    def _xfade_pair(self, left, right, tt, td, fps, bitrate, encoder, preset, out_name):
        """单次 xfade 合成（Phase 3.2：可并行）；输出无效时回退右片段。"""
        acc = _get_actual_duration(left)
        off = max(0, acc - td)
        out = str(self._work_dir / out_name)
        self._run_ff(["ffmpeg", "-y", "-loglevel", "error", *(_hwaccel_args(encoder)), "-i", left, "-i", right,
                       "-filter_complex",
                       f"[0:v]setsar=1[a];[1:v]setsar=1[b];[a][b]xfade=transition={tt}:duration={td}:offset={off}[v]",
                       "-map", "[v]", "-c:v", encoder, "-preset", preset,
                       "-b:v", bitrate, "-r", str(fps), out],
                      capture_output=True, text=False, timeout=600)
        if _is_valid_video(out):
            return out
        self._final_ffmpeg_log.append(f"xfade({Path(left).name}×{Path(right).name}): 输出无效，回退右片段")
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
            # C4: 精确集合校验——原 regex 形状校验放行任意合法形状名（如 LLM
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
                out = await self._ff_concat(
                    self._xfade_pair, left["path"], right["path"], right["tt"], right["td"],
                    fps, bitrate, encoder, preset, f"xr{round_no}_{uuid.uuid4().hex[:6]}.mp4",
                )
                return {
                    "path": out,
                    "acc": left["acc"] + right["acc"] - right["td"],
                    "tt": right["tt"],
                    "td": right["td"],
                }

            merged = await asyncio.gather(*(_merge(p) for p in pairs))
            items = list(merged) + ([odd] if odd else [])

        final = str(self._work_dir / "concat.mp4")
        if _is_valid_video(items[0]["path"]):
            shutil.copy2(items[0]["path"], final)
            return final
        return trimmed[0]

    def _run_concat_all(self, clips, fps, bitrate, encoder, preset):
        out = Path(clips[0]).parent / "concat.mp4"
        inputs = sum([["-i", f] for f in clips], [])
        n = len(clips)
        # 每个输入先 setsar=1 归一化，保证 concat 输入参数一致（含旧缓存里 SAR 未归一化的片段）
        flt = "".join(f"[{i}:v]setsar=1[v{i}];" for i in range(n)) + "".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[v]"
        timeout = 120 + n * 60  # 120s base + 60s per segment
        self._run_ff(["ffmpeg", "-y", "-loglevel", "error", *(_hwaccel_args(encoder)), *inputs,
                       "-filter_complex", flt, "-map", "[v]",
                       "-c:v", encoder, "-preset", preset,
                       "-b:v", bitrate, "-r", str(fps), str(out)],
                      capture_output=True, text=False, timeout=timeout)
        return str(out) if _is_valid_video(out) else clips[-1]

    # ── M2: concat + text 合并 ────────────────────

    async def _apply_text_concat(self, video, overlays, encoder, preset, progress_callback=None,
                                 width=1920, height=1080):
        """将文字叠加层烧录到视频（单次 re-encode）。

        默认走 ASS 路径（``-vf ass=<path>``）：全部 14 个样式字段真实生效。
        ``settings.caption_renderer == "drawtext"`` 时回退到旧 drawtext 滤镜链。
        """
        if _caption_renderer() == "drawtext":
            return await self._apply_text_concat_drawtext(video, overlays, encoder, preset,
                                                          progress_callback)
        return await self._apply_text_ass(video, overlays, encoder, preset, progress_callback,
                                          width=width, height=height)

    async def _apply_text_ass(self, video, overlays, encoder, preset, progress_callback=None,
                              width=1920, height=1080):
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
            await progress_callback("text", 60, f"烧录 {len(dialogues)} 条字幕/文字")

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
            except ValueError:
                ass_arg = ass_path.name  # 跨盘符回退：仅文件名（依赖 CWD 与 work_dir 一致）
            out = str(self._work_dir / f"txt_{bi}.mp4")
            cmd = ["ffmpeg", "-y", "-loglevel", "error", *(_hwaccel_args(encoder)), "-i", current,
                   "-vf", f"ass={ass_arg}",
                   "-c:v", encoder, "-preset", preset, "-pix_fmt", _current_pix_fmt(),
                   "-c:a", "copy", out]
            r = await self._ff(cmd, capture_output=True, text=False, timeout=1800)
            if r.returncode == 0 and _is_valid_video(out):
                current = out
            if progress_callback:
                done = bi + len(batch)
                pct = 60 + min(done / max(len(dialogues), 1), 1.0) * 10
                await progress_callback("text", pct, f"烧录 {done}/{len(dialogues)} 条字幕")
        return current

    async def _apply_text_concat_drawtext(self, video, overlays, encoder, preset, progress_callback=None):
        """将 drawtext filter 叠加到视频（单次 re-encode）。旧路径，仅 fallback 使用。"""
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

        if progress_callback:
            await progress_callback("text", 60, f"烧录 {len(filters)} 条字幕/文字")

        # 分批，每批内所有 filter 以逗号连接，单次 FFmpeg 调用。
        # 长视频（数百秒）单趟重编码就需 5-15 分钟：batch 放大到单批 + 超时放大。
        batch_size = 100
        current = video
        for bi in range(0, len(filters), batch_size):
            batch = filters[bi:bi + batch_size]
            out = str(self._work_dir / f"txt_{bi}.mp4")
            # L2: 用 -vf 而非重新 -filter_complex，减少复杂度
            cmd = ["ffmpeg", "-y", "-loglevel", "error", *(_hwaccel_args(encoder)), "-i", current,
                   "-vf", ",".join(batch),
                   "-c:v", encoder, "-preset", preset, "-pix_fmt", _current_pix_fmt(),
                   "-c:a", "copy", out]
            r = await self._ff(cmd, capture_output=True, text=False, timeout=1800)
            if r.returncode == 0 and _is_valid_video(out):
                current = out
            if progress_callback:
                done = bi + len(batch)
                pct = 60 + min(done / max(len(filters), 1), 1.0) * 10
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
            try:
                from clipwright.config import settings
                limit = max(1, int(getattr(settings, "material_concurrency", 6)))
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

            movs = await asyncio.gather(*(_render_one(m) for m in hf_ov_local))
            # 按 start_sec 排序，保持原有链式叠加顺序语义 (Phase 2: 单次 filter_complex)
            ordered = sorted(zip(movs, [m.get("start_sec", 0) for m in hf_ov_local],
                                 [m.get("duration_sec", 0) for m in hf_ov_local]),
                             key=lambda t: t[1])
            chained = [(mov, start_sec, duration_sec)
                       for mov, start_sec, duration_sec in ordered if mov]
            if chained:
                if progress_callback:
                    await progress_callback("mg", 90, "链式叠加 MG 动画")
                video = await self._apply_mg_overlay_chained(
                    video, chained, width, height, fps)
                if progress_callback:
                    await progress_callback("mg", 95, "MG 叠加完成")

        if overlays:
            if progress_callback:
                await progress_callback("mg", 96, "合成图解动画叠加层")
            mov = str(self._work_dir / "overlay.mov")
            ok = await HyperframesRenderer.render_overlays(overlays, mov, width, height, fps)
            if ok and _is_valid_video(mov):
                out_v = str(self._work_dir / "with_hf.mp4")
                ok2 = HyperframesRenderer.render_overlay_on_video(mov, video, out_v)
                if ok2 and _is_valid_video(out_v):
                    video = out_v
        return video

    async def _render_mg_mov(self, mg_ov: dict, width: int, height: int, fps: float) -> str | None:
        """(a)+(b) 单个 MG HTML → 独立工作目录 → npx hyperframes render 产出 MOV。"""
        html = mg_ov.get("mg_html", "")
        if not html:
            return None
        mg_dir = Path(self._work_dir) / f"mg_{uuid.uuid4().hex[:8]}"
        mg_dir.mkdir(parents=True, exist_ok=True)
        (mg_dir / "index.html").write_text(html, encoding="utf-8")

        from clipwright.animation.hyperframes_renderer import HyperframesRenderer
        mov = str(mg_dir / "mg_out.mov")
        cmd = [HyperframesRenderer._npx_cmd(), "hyperframes", "render",
               str(mg_dir), "-o", mov, "--format", "mov",
               "-f", str(int(fps)), "--quiet"]
        try:
            r = await self._ff(cmd, capture_output=True, text=False, timeout=1800,
                               env=HyperframesRenderer._render_env())
            if r.returncode == 0 and _is_valid_video(mov):
                return mov
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
            out_v = str(self._work_dir / f"mg_{uuid.uuid4().hex[:4]}.mp4")
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
        max_len: int = 30000,
    ) -> str:
        """(c') 全部 MG MOV → 单次 filter_complex 链式 overlay（对比旧版 N 次全片 re-encode）。

        ``movs`` 为 ``(mov_path, start_sec, duration_sec)`` 元组列表，已按 start_sec 排序。
        每个 MOV 单独 ``-i`` 输入，先 scale/pad 到导出分辨率，再链式 overlay 到主视频，
        以 ``enable='between(t,...)'`` 限制到各自时间窗口；最终 ``-map`` 末级标签输出。

        - 缺失/损坏 MOV：调用方已过滤 None；此处再按文件存在性兜底跳过，
          链式图保证任何输入缺失都不断链、不使整次渲染失败。
        - cmdline 长度超过 ``max_len``（Windows 命令行限制）时拆成两半递归分批，
          每批仍为单次 ffmpeg 调用。
        - 失败时回退返回原 ``video``，绝不让 MG 阶段拖垮整个导出。
        """
        movs = [(m, s, d) for m, s, d in movs if m and Path(m).exists()]
        if not movs:
            return video
        out = str(self._work_dir / f"mg_chain_{uuid.uuid4().hex[:8]}.mp4")
        cmd = self._build_mg_chained_cmd(video, movs, width, height, out)
        length = len(" ".join(cmd))
        if length > max_len:
            logger.warning(
                "[Render] MG chained cmdline %d chars > %d，拆成两批链式叠加",
                length, max_len)
            mid = len(movs) // 2
            first = await self._apply_mg_overlay_chained(
                video, movs[:mid], width, height, fps, max_len=max_len)
            return await self._apply_mg_overlay_chained(
                first, movs[mid:], width, height, fps, max_len=max_len)
        logger.info("[Render] MG chained overlay: %d inputs, %d chars",
                    len(movs), length)
        try:
            r = await self._ff(cmd, capture_output=True, text=False, timeout=3600)
            if r.returncode == 0 and _is_valid_video(out):
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
                # F3 实渲修复: overlay 的 format 选项是枚举（yuv420/yuv420p10/rgb/gbrp/auto），
                # `rgba` 非法会导致整条链式合成失败（rc=EINVAL）、MG 静默丢失。
                # 用 format=auto —— 与非链式路径 render_overlay_on_video 一致，alpha 由输入像素格式保留。
                f"[{prev}][mg{i}]overlay=format=auto:"
                f"enable='between(t,{_fmt_sec(start)},{_fmt_sec(end)})'[v{i}]"
            )
            prev = f"v{i}"
        cmd += ["-filter_complex", ";".join(parts),
                "-map", f"[{prev}]", "-map", "0:a?",
                "-c:v", encoder, "-preset", preset, "-pix_fmt", _current_pix_fmt(),
                "-c:a", "copy", str(out)]
        return cmd

    # ── drawtext 构建（同原版） ──────────────────

    def _build_drawtext_filter(self, ov):
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
            return self._build_kf_drawtext(text, ts, start, dur, kfs, font_arg)
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
    def _build_kf_drawtext(text, ts, start_sec, duration_sec, keyframes, font_arg=""):
        if not keyframes: return ""
        times = [kf["time"] for kf in keyframes]
        s, e = min(times), max(times)
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
        enable = f"between(t,{s},{max(e,start_sec+duration_sec)})"
        parts = [f"drawtext=text='{safe}'{font_arg}", f"fontsize={fs}", f"fontcolor={color_to_drawtext(ts.font_color)}",
                 f"x={bx}+({xo})", f"y={by}+({yo})", f"alpha={a}",
                 f":enable='{enable}'"]
        if ts.stroke_width > 0:
            parts.append(f":borderw={ts.stroke_width}:bordercolor={color_to_drawtext(ts.stroke_color)}")
        if ts.shadow_x != 0 or ts.shadow_y != 0:
            parts.append(f":shadowx={ts.shadow_x}:shadowy={ts.shadow_y}:shadowcolor={color_to_drawtext(ts.shadow_color)}")
        main = ":".join(parts)
        if ts.glow_width > 0 and ts.glow_color:
            # glow 双通道：底层在前（同坐标/同 enable 窗口/随缩放），主文本在后
            glow = _build_glow_underlay(text, fs, ts.glow_color, ts.glow_width, font_arg,
                                        f"{bx}+({xo})", f"{by}+({yo})", enable, alpha=a)
            return f"{glow},{main}"
        return main

    # ── overlay / audio（同原版精简）─────────────

    async def _apply_overlays_safe(self, video, segments, width, height):
        out = str(self._work_dir / "ov.mp4")
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
            # D2: blend_mode 落地——screen/multiply/overlay 三种混合模式经
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
                       "-c:v",encoder,"-preset",preset,"-pix_fmt","yuv420p","-c:a","copy",output_path],
                      capture_output=True, text=False, timeout=1800)

    async def _mix_audio_safe(self, video, segments, audio_path, bitrate, ab, bgm_path,
                              video_cached: bool = False):
        """混合音频（C12：失败必须标记而非静默静音成片）。返回 (video, failure_marker|None)。"""
        if not video or not Path(video).exists():
            return video, None
        out = str(self._work_dir / "aud.mp4")
        try:
            await self._mix_audio(video, segments, out, audio_path, ab, bgm_path, bitrate,
                                  video_cached=video_cached)
            if Path(out).exists() and _is_valid_video(out):
                return out, None
            # 混合失败/输出无效 → 保留无声视频但标记失败
            return video, "audio_mix_failed"
        except Exception as e:
            logger.warning("音频混合失败，跳过音频: %s", e)
            return video, f"audio_mix_error: {str(e)[:120]}"

    async def _mix_audio(self, input_video, segments, output_path, afp="", ab="192k", bfp="", bitrate="5M",
                         video_cached: bool = False):
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
                           "fade_in": 0, "fade_out": 0, "is_voice": True, "is_bgm": False})
        for seg in segments or []:
            s = seg.get("source_path", "")
            if s and Path(s).exists():
                voices.append({
                    "path": s,
                    "volume": float(seg.get("volume", 1.0) or 1.0),
                    "start": float(seg.get("start_sec", 0) or 0),
                    "dur": float(seg.get("duration_sec", 0) or 0),
                    "fade_in": float(seg.get("audio_fade_in_sec") or 0),
                    "fade_out": float(seg.get("audio_fade_out_sec") or 0),
                    "is_voice": bool(seg.get("is_voice")),
                    "is_bgm": bool(seg.get("is_bgm")),
                })
        if bfp and Path(bfp).exists():
            voices.append({"path": bfp, "volume": 0.3, "start": 0, "dur": 0,
                           "fade_in": 0, "fade_out": 0, "is_voice": False, "is_bgm": True})

        # C11 路径：≥2 个音源 → 多输入 amix + loudnorm（真实混音/LUFS）
        if len(voices) >= 2:
            try:
                inputs = ["ffmpeg", "-y", "-loglevel", "error", *( _hwaccel_args(encoder)),
                          "-i", input_video]
                for v in voices:
                    inputs += ["-i", v["path"]]
                chains = []
                mix_inputs: list[str] = []
                sc_source: str = ""
                for i, v in enumerate(voices, start=1):
                    parts = []
                    if v["dur"] > 0 and v["start"] >= 0:
                        parts.append(f"atrim=start={v['start']}:duration={v['dur']}")
                    if v["start"] > 0:
                        parts.append(f"adelay={int(v['start'] * 1000)}|{int(v['start'] * 1000)}")
                    if v["fade_in"] > 0:
                        parts.append(f"afade=t=in:st=0:d={v['fade_in']}")
                    if v["fade_out"] > 0 and v["dur"] > 0:
                        parts.append(f"afade=t=out:st={max(0, v['dur'] - v['fade_out'])}:d={v['fade_out']}")
                    parts.append(f"volume={v['volume']}")
                    base = f"a{i}"
                    chains.append(f"[{i}:a]{','.join(parts)}[{base}]")
                    # D3: BGM 自动避让（ducking）——首个配音链路 asplit 出 sidechain
                    # 源；BGM 链路经 sidechaincompress 在人声段自动压低（原实现
                    # BGM 固定低音量压全程，人声间歇处音乐也被压平）。
                    if v.get("is_voice") and not sc_source:
                        chains.append(f"[{base}]asplit=2[scsrc][mix{i}]")
                        sc_source = "scsrc"
                        mix_inputs.append(f"mix{i}")
                    elif v.get("is_bgm") and sc_source:
                        chains.append(
                            f"[{base}][{sc_source}]sidechaincompress="
                            f"threshold=0.03:ratio=4:attack=25:release=400[mix{i}]"
                        )
                        mix_inputs.append(f"mix{i}")
                    else:
                        mix_inputs.append(base)
                chains_mix_in = "".join(f"[{m}]" for m in mix_inputs)
                chains.append(
                    f"{chains_mix_in}amix=inputs={len(mix_inputs)}:duration=first:normalize=0,"
                    f"loudnorm=I=-16:LRA=11:TP=-1.5[aout]"
                )
                await self._ff(inputs + [
                    "-filter_complex", ";".join(chains),
                    "-map", "0:v:0", "-map", "[aout]",
                    *(["-c:v", "copy"] if video_cached else
                      ["-c:v", encoder, "-preset", preset, "-pix_fmt", _current_pix_fmt(),
                       "-b:v", bitrate, *_delivery_extra_args(encoder)]),
                    "-c:a", "aac", "-b:a", ab, "-shortest", output_path,
                ], capture_output=True, text=False, timeout=1800)
                if _is_valid_video(output_path):
                    logger.info("C11 真实混音成功: %d 音源 + LUFS 归一", len(voices))
                    return
                logger.warning("_mix_audio: C11 混音输出无效，回退单音源: %s", output_path)
            except Exception as e:
                logger.warning("_mix_audio: C11 混音异常，回退单音源: %s", str(e)[:200])

        # 回退：单音源直接混入
        if voices:
            voice = voices[0]["path"]
            try:
                await self._ff(["ffmpeg","-y","-loglevel","error",*(_hwaccel_args(encoder)),"-i",input_video,"-i",voice,
                               *(["-c:v","copy"] if video_cached else
                                 ["-c:v",encoder,"-preset",preset,"-pix_fmt",_current_pix_fmt(),
                                  "-b:v",bitrate,*_delivery_extra_args(encoder)]),
                               "-c:a","aac","-b:a",ab,"-map","0:v:0","-map","1:a:0","-shortest",output_path],
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

    def _cleanup(self):
        if self._work_dir.exists():
            shutil.rmtree(self._work_dir, ignore_errors=True)
        self._work_dir.mkdir(parents=True, exist_ok=True)
