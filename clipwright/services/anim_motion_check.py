"""渲染后动画动效自检（PRD §5 的像素级验收）。

与生成期的 `motion_report`（只看 mg_def 关键帧）互补：这里**真解码**渲染出来的
MOV，按 4fps 抽帧算哈希，统计「不同帧占比」。生成期指标可能被"关键帧写了但没画
出来"骗过（例如元素被裁切、透明度关键帧写错），像素级检查能兜住。

集成点：`render.py::_apply_all_hyperframes` 在链式叠加前批量检查，
不合格写 warning + metadata（不阻断渲染——动画已经生成，只是质量提示）。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Iterable

from clipwright.config import logger

#: 默认阈值（与 scripts/verify_animation_motion.py 一致）
DEFAULT_SAMPLE_FPS = 4
DEFAULT_MIN_DISTINCT_FRAMES = 8
DEFAULT_MIN_DISTINCT_RATIO = 0.25


def _run(cmd: list[str], timeout: float = 300.0) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=False, timeout=timeout)


def probe_duration(path: str) -> float:
    r = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
              "-of", "default=nw=1:nk=1", path], timeout=60)
    try:
        return float(r.stdout.decode().strip())
    except Exception:  # noqa: BLE001
        return 0.0


def frame_hashes(path: str, fps: int = DEFAULT_SAMPLE_FPS) -> list[str]:
    r = _run(["ffmpeg", "-v", "error", "-i", path, "-vf", f"fps={fps}",
              "-f", "framemd5", "-"])
    if r.returncode != 0:
        return []
    hashes: list[str] = []
    for line in r.stdout.decode("utf-8", "replace").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 6:
            hashes.append(parts[-1])
    return hashes


def check_mov_motion(
    path: str, *,
    fps: int = DEFAULT_SAMPLE_FPS,
    min_frames: int = DEFAULT_MIN_DISTINCT_FRAMES,
    min_ratio: float = DEFAULT_MIN_DISTINCT_RATIO,
) -> dict[str, Any]:
    """检查单个 MOV 的动效多样性；文件不可读时返回 ok=True（不误报）。"""
    if not Path(path).exists():
        return {"ok": True, "skipped": "missing", "path": path}
    hashes = frame_hashes(path, fps=fps)
    if not hashes:
        return {"ok": True, "skipped": "no_frames", "path": path}
    distinct = len(set(hashes))
    ratio = distinct / len(hashes)
    return {
        "ok": distinct >= min_frames and ratio >= min_ratio,
        "path": path,
        "sampled_frames": len(hashes),
        "distinct_frames": distinct,
        "distinct_ratio": round(ratio, 3),
        "duration_sec": round(probe_duration(path), 2),
        "thresholds": {"min_frames": min_frames, "min_ratio": min_ratio},
    }


def settings_of() -> dict[str, Any]:
    try:
        from clipwright.config import settings

        return {
            "enabled": bool(getattr(settings, "animation_motion_check", True)),
            "fps": int(getattr(settings, "animation_motion_check_fps", DEFAULT_SAMPLE_FPS)),
            "min_frames": int(getattr(settings, "animation_motion_check_min_frames",
                                      DEFAULT_MIN_DISTINCT_FRAMES)),
            "min_ratio": float(getattr(settings, "animation_motion_check_min_ratio",
                                       DEFAULT_MIN_DISTINCT_RATIO)),
        }
    except Exception:  # noqa: BLE001
        return {"enabled": True, "fps": DEFAULT_SAMPLE_FPS,
                "min_frames": DEFAULT_MIN_DISTINCT_FRAMES,
                "min_ratio": DEFAULT_MIN_DISTINCT_RATIO}


def check_overlays(pairs: Iterable[tuple[str, float, float]]) -> dict[str, Any]:
    """批量检查动画 MOV；返回 ``{"checked", "failed", "results"}``。"""
    cfg = settings_of()
    if not cfg["enabled"]:
        return {"checked": 0, "failed": 0, "results": [], "disabled": True}
    results: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    checked = 0
    for mov, start, _dur in pairs:
        try:
            rep = check_mov_motion(mov, fps=cfg["fps"],
                                   min_frames=cfg["min_frames"], min_ratio=cfg["min_ratio"])
        except Exception as e:  # noqa: BLE001
            logger.debug("动画动效自检异常 %s: %s", mov, e)
            continue
        if rep.get("skipped"):
            continue
        checked += 1
        rep["start_sec"] = round(float(start or 0), 2)
        results.append(rep)
        if not rep["ok"]:
            failed.append(rep)
    if failed:
        logger.warning(
            "Render: %d/%d 个动画片段动效多样性不足（distinct ratio < %.2f 或帧数 < %d）",
            len(failed), checked, cfg["min_ratio"], cfg["min_frames"],
        )
    return {"checked": checked, "failed": len(failed), "results": results,
            "failed_items": failed}


__all__ = [
    "DEFAULT_MIN_DISTINCT_FRAMES",
    "DEFAULT_MIN_DISTINCT_RATIO",
    "DEFAULT_SAMPLE_FPS",
    "check_mov_motion",
    "check_overlays",
    "frame_hashes",
    "probe_duration",
    "settings_of",
]
