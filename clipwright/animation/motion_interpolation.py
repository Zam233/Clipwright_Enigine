"""MotionSpec 插值与路径采样（纯函数）。"""

from __future__ import annotations

import math
from typing import Any, Iterable


def _bezier(t: float, p1: float, p2: float) -> float:
    # CSS cubic-bezier: x(t)=3(1-t)^2 t x1 + 3(1-t)t^2 x2 + t^3
    return 3 * (1 - t) ** 2 * t * p1 + 3 * (1 - t) * t ** 2 * p2 + t ** 3


def cubic_bezier_y(x: float, x1: float, y1: float, x2: float, y2: float) -> float:
    """用二分求 x 对应的 t，再算 y；足够稳定且不引入依赖。"""
    lo, hi = 0.0, 1.0
    for _ in range(24):
        mid = (lo + hi) / 2
        if _bezier(mid, x1, x2) < x:
            lo = mid
        else:
            hi = mid
    t = (lo + hi) / 2
    return _bezier(t, y1, y2)


_NAMED: dict[str, tuple[float, float, float, float]] = {
    "ease": (0.25, 0.1, 0.25, 1.0),
    "ease-in": (0.42, 0.0, 1.0, 1.0),
    "ease-out": (0.0, 0.0, 0.58, 1.0),
    "ease-in-out": (0.42, 0.0, 0.58, 1.0),
    "ease-in-cubic": (0.32, 0.0, 0.67, 0.0),
    "ease-out-cubic": (0.33, 1.0, 0.68, 1.0),
    "ease-in-out-cubic": (0.65, 0.0, 0.35, 1.0),
    "ease-out-back": (0.34, 1.56, 0.64, 1.0),
    "ease-in-back": (0.36, 0.0, 0.66, -0.56),
}


def easing_progress(easing: Any, t: float) -> float:
    """返回 [0,1] 区间内的 eased 进度。"""
    t = max(0.0, min(1.0, float(t)))
    if not easing:
        return t
    if isinstance(easing, str):
        if easing in _NAMED:
            x1, y1, x2, y2 = _NAMED[easing]
            return cubic_bezier_y(t, x1, y1, x2, y2)
        return t
    et = getattr(easing, "type", None) or (easing.get("type") if isinstance(easing, dict) else None)
    if et in (None, "linear"):
        return t
    if et == "hold":
        return 0.0 if t < 1 else 1.0
    if et == "named":
        name = getattr(easing, "name", None) or (easing.get("name") if isinstance(easing, dict) else "")
        return easing_progress(name, t)
    if et == "cubic-bezier":
        return cubic_bezier_y(
            t, float(getattr(easing, "x1", 0.25)), float(getattr(easing, "y1", 0.1)),
            float(getattr(easing, "x2", 0.25)), float(getattr(easing, "y2", 1.0)),
        )
    if et == "spring":
        vals = sample_spring(64, float(getattr(easing, "stiffness", 170)),
                             float(getattr(easing, "damping", 18)),
                             float(getattr(easing, "mass", 1)),
                             float(getattr(easing, "velocity", 0)))
        return vals[max(0, min(len(vals) - 1, int(t * (len(vals) - 1))))]
    if et == "steps":
        n = max(1, int(getattr(easing, "count", 1)))
        pos = getattr(easing, "position", "end")
        if pos == "start":
            return min(1.0, math.floor(t * n + 1) / n)
        if pos == "both":
            return min(1.0, round(t * n) / n)
        return min(1.0, math.floor(t * n) / n)
    return t


def sample_spring(count: int, stiffness: float, damping: float, mass: float, velocity: float = 0.0) -> list[float]:
    """固定时长 1s 的弹簧采样；返回 count 个归一化值。"""
    out: list[float] = []
    dt = 1.0 / max(1, count - 1)
    x, v = 0.0, float(velocity)
    for _ in range(max(1, count)):
        a = (-stiffness * x - damping * v) / max(0.2, mass)
        v += a * dt
        x += v * dt
        out.append(x)
    if out:
        end = out[-1] or 1.0
        out = [u / end for u in out]
    return out


def sample_path(path_type: str, points: list[dict[str, float]], progress: float) -> tuple[float, float]:
    """归一化坐标路径采样。"""
    p = max(0.0, min(1.0, progress))
    if not points:
        return 0.0, 0.0
    if path_type == "circle":
        # points[0]=center, points[1]=radius point
        cx, cy = points[0].get("x", 0), points[0].get("y", 0)
        rx = points[1].get("x", cx + 0.1) - cx
        ry = points[1].get("y", cy + 0.1) - cy
        ang = -math.pi / 2 + 2 * math.pi * p
        return cx + rx * math.cos(ang), cy + ry * math.sin(ang)
    if len(points) == 1:
        return points[0].get("x", 0), points[0].get("y", 0)
    if path_type == "cubic-bezier" and len(points) >= 4:
        # points[0]=start, [1]/[2]=controls, [3]=end
        t = p
        mt = 1 - t
        x = (mt ** 3 * points[0].get("x", 0) + 3 * mt ** 2 * t * points[1].get("x", 0)
             + 3 * mt * t ** 2 * points[2].get("x", 0) + t ** 3 * points[3].get("x", 0))
        y = (mt ** 3 * points[0].get("y", 0) + 3 * mt ** 2 * t * points[1].get("y", 0)
             + 3 * mt * t ** 2 * points[2].get("y", 0) + t ** 3 * points[3].get("y", 0))
        return x, y
    if path_type == "arc" and len(points) >= 2:
        cx, cy = points[0].get("x", 0), points[0].get("y", 0)
        rx = abs(points[1].get("x", cx + 0.1) - cx)
        ry = abs(points[1].get("y", cy + 0.1) - cy)
        ang = math.pi * p
        return cx + rx * math.cos(ang), cy - ry * math.sin(ang)
    # 多段折线等距近似
    seg = min(len(points) - 1, int(p * (len(points) - 1)))
    local = p * (len(points) - 1) - seg
    a, b = points[seg], points[seg + 1]
    return (a.get("x", 0) * (1 - local) + b.get("x", 0) * local,
            a.get("y", 0) * (1 - local) + b.get("y", 0) * local)


def sample_easing_keyframes(
    keyframes: Iterable[dict[str, Any]], duration: float, sample_hz: float = 60.0,
) -> list[dict[str, Any]]:
    """把压缩/复杂 keyframes 采样成离散关键帧（保留 value 字段）。"""
    frames = sorted([dict(k) for k in keyframes], key=lambda k: float(k.get("time", 0)))
    if not frames:
        return []
    n = max(2, int(max(duration, 0.01) * sample_hz))
    out: list[dict[str, Any]] = []
    for i in range(n + 1):
        t = duration * i / n
        before = frames[0]
        after = frames[-1]
        for j in range(len(frames) - 1):
            if float(frames[j].get("time", 0)) <= t <= float(frames[j + 1].get("time", 0)):
                before, after = frames[j], frames[j + 1]
                break
        span = max(1e-6, float(after.get("time", 0)) - float(before.get("time", 0)))
        local = (t - float(before.get("time", 0))) / span
        eased = easing_progress(before.get("easing"), local)
        frame = {"time": round(t, 4)}
        for k, v in before.items():
            if k in ("time", "easing"):
                continue
            if isinstance(v, (int, float)) and isinstance(after.get(k), (int, float)):
                frame[k] = float(v) + (float(after[k]) - float(v)) * eased
            else:
                frame[k] = v
        out.append(frame)
    return out
