"""切点复核图（timeline_view 等价物，V4/V7）。

video-use 的自评闭环把「渲染输出」在**每个切点 ±窗口**抽帧合成一张图，让审查者
（人或视觉模型）一眼看出跳变/爆闪/字幕被遮/overlay 错位。本模块用 ffmpeg + Pillow
实现同样的能力，零新增依赖：

- `cut_times_from_segments()`：从视频片段推导切点（相邻片段边界，跳过 0）；
- `build_cut_sheet()`：对单个切点抽 3 帧（前/中/后）横向拼条 + 时间标注；
- `build_review_sheets()`：批量生成，限制张数（默认 12）避免长片产出上百张图。

产物是 PNG；审查本身（打分/问题清单）由调用方决定——`RenderService` 的
post-render hook 会把图交给视觉模型（若配置）或只记日志路径。

设计约束：纯本地、best-effort——任何失败都返回空列表/None，绝不阻断渲染。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from clipwright.config import logger
from clipwright.utils.timebase import (
    TimebaseError,
    as_frame_rate,
    seconds_to_ticks,
    snap_ticks,
    ticks_to_seconds,
)

#: 单个切点抽帧的窗口（秒）：以切点为中心，取 -WINDOW / 0 / +WINDOW 三帧
CUT_WINDOW_SEC = 0.35
#: 单张复核图内缩略图的宽度（像素）
TILE_WIDTH = 426
#: 一次复核最多生成多少张切点图
DEFAULT_MAX_CUTS = 12


def cut_times_from_segments(
    segments: Iterable[dict[str, Any]],
    *,
    skip_head: bool = True,
    frame_rate: Any = None,
) -> list[float]:
    """推导切点时间（秒，升序去重）。

    切点 = 每个片段（除第一个）的 start_sec —— 即剪辑点/转场点。首帧（0）不算切点
    （除非 skip_head=False），否则每条时间线都会恒定产出第一张无意义图。

    O1：先用整数 tick 量化再判重（切点身份 = tick，不用浮点近似），并按需吸附帧边界：
    给了 `frame_rate` 时切点落在整帧起点，抽帧 `-ss` 值即「某帧的开头」，
    复核图上看到的跳变不会被半帧错位的采样干扰。
    """
    fr = None
    if frame_rate is not None:
        try:
            fr = as_frame_rate(frame_rate)
        except TimebaseError:
            fr = None
    seen: dict[int, float] = {}
    for seg in segments or []:
        try:
            ticks = seconds_to_ticks(float(seg.get("start_sec", 0) or 0))
        except (TypeError, ValueError, TimebaseError):
            continue
        if ticks < 0:
            continue
        if skip_head and ticks == 0:
            continue
        if fr is not None:
            ticks = snap_ticks(ticks, fr, mode="nearest")
        # 保留完整精度（不做毫秒取整）：取整会把刚吸附到的帧边界又挪走，
        # 复核图的抽帧 `-ss` 就不再落在整帧起点上。标注文本自行 `:.2f` 格式化。
        seen.setdefault(ticks, ticks_to_seconds(ticks))
    return [seen[k] for k in sorted(seen)]


def _grab_frame(video: str, at: float, out_png: Path, timeout: int = 60) -> bool:
    """用 ffmpeg 抽单帧（-ss 前置快速 seek）。"""
    import subprocess

    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{max(0.0, at):.3f}",
             "-i", video, "-frames:v", "1", "-vf", f"scale={TILE_WIDTH}:-2", str(out_png)],
            capture_output=True, text=False, timeout=timeout,
        )
        return r.returncode == 0 and out_png.exists() and out_png.stat().st_size > 0
    except Exception as exc:
        logger.debug("timeline_view: 抽帧失败 t=%.2f: %s", at, exc)
        return False


def build_cut_sheet(
    video: str,
    cut_time: float,
    out_png: Path,
    *,
    index: int = 0,
    window: float = CUT_WINDOW_SEC,
    tmp_dir: Path | None = None,
) -> str | None:
    """为单个切点生成一张横向复核图（前 0.35s / 切点 / 后 0.35s 三帧 + 标注）。"""
    from PIL import Image, ImageDraw

    work = Path(tmp_dir) if tmp_dir else out_png.parent
    work.mkdir(parents=True, exist_ok=True)
    frames: list[tuple[str, Image.Image]] = []
    for label, offset in (("before", -window), ("cut", 0.0), ("after", window)):
        t = max(0.0, cut_time + offset)
        png = work / f"f_{index}_{label}.png"
        if _grab_frame(video, t, png):
            try:
                frames.append((f"{label}  t={t:.2f}s", Image.open(png).convert("RGB")))
            except Exception:
                continue
    if not frames:
        return None

    tile_w = max(f[1].width for f in frames)
    tile_h = max(f[1].height for f in frames)
    header_h = 34
    gap = 6
    sheet = Image.new(
        "RGB",
        (tile_w * len(frames) + gap * (len(frames) + 1), tile_h + header_h + gap * 2),
        (18, 18, 22),
    )
    draw = ImageDraw.Draw(sheet)
    draw.text((gap, 9), f"#{index}  cut @ {cut_time:.2f}s   (±{window:.2f}s)",
              fill=(235, 235, 235))
    for i, (label, img) in enumerate(frames):
        x = gap + i * (tile_w + gap)
        y = header_h + gap
        sheet.paste(img, (x, y + (tile_h - img.height) // 2))
        draw.text((x + 4, y + 4), label, fill=(255, 140, 60))
    out_png.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_png)
    for _label, img in frames:
        try:
            img.close()
        except Exception:
            pass
    return str(out_png)


def build_review_sheets(
    video: str,
    cut_times: list[float],
    out_dir: str | Path,
    *,
    max_cuts: int = DEFAULT_MAX_CUTS,
    window: float = CUT_WINDOW_SEC,
) -> list[str]:
    """批量生成切点复核图，返回 PNG 路径列表（best-effort，失败即少图）。"""
    if not video or not cut_times:
        return []
    out = Path(out_dir)
    sheets: list[str] = []
    for idx, t in enumerate(cut_times[: max(1, int(max_cuts))]):
        path = build_cut_sheet(video, t, out / f"cut_{idx:02d}_{t:.2f}s.png",
                               index=idx, window=window, tmp_dir=out / "_frames")
        if path:
            sheets.append(path)
    return sheets
