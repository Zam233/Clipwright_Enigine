"""位图矢量化（PRD C3 / R-17 的前置）：把墨版掩膜描成多边形路径。

参考样本的要求：``f_eye`` / ``f_reach`` 这类要放大到 3.5 倍的特写，必须在加载时
描成平滑矢量路径，4K 下画路径而不是放大位图。

实现不引入 potrace 依赖：
1. 取掩膜边界像素（mask 与其 4 邻域腐蚀结果之差）；
2. 用最近邻游走把它们串成闭合轮廓（允许 8 邻域，优先直行以保持直线）；
3. Douglas–Peucker 简化，保留形状特征。

对测试用的方块/圆这类形状足够；对复杂剪影是"可用但不精致"，
gate 2 的自查项会明确要求人工复核 4K 边缘（V34）。
"""

from __future__ import annotations

from typing import Sequence

import numpy as np

Point = tuple[int, int]
Polygon = list[Point]


def boundary_pixels(mask: np.ndarray) -> list[Point]:
    """返回掩膜的 8 邻域边界像素（按行优先，顺序稳定）。"""
    m = np.asarray(mask, dtype=bool)
    if m.ndim != 2:
        raise ValueError("mask 必须是二维布尔数组")
    padded = np.pad(m, 1, mode="constant", constant_values=False)
    neighbors = (
        padded[:-2, 1:-1], padded[2:, 1:-1], padded[1:-1, :-2], padded[1:-1, 2:],
        padded[:-2, :-2], padded[:-2, 2:], padded[2:, :-2], padded[2:, 2:],
    )
    interior = np.logical_and.reduce([m, *neighbors])
    boundary = m & ~interior
    ys, xs = np.nonzero(boundary)
    return [(int(y), int(x)) for y, x in zip(ys, xs)]


def _neighbors(point: Point) -> list[Point]:
    y, x = point
    # 直行优先：先上下左右，再对角
    return [(y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1),
            (y - 1, x - 1), (y - 1, x + 1), (y + 1, x - 1), (y + 1, x + 1)]


def trace_contours(mask: np.ndarray, *, max_contours: int = 8) -> list[Polygon]:
    """把掩膜描成若干闭合轮廓（像素坐标，已简化）。"""
    remaining = set(boundary_pixels(mask))
    contours: list[Polygon] = []
    while remaining and len(contours) < max_contours:
        start = min(remaining)  # 行优先最小点：确定性
        contour: Polygon = [start]
        remaining.discard(start)
        current = start
        while True:
            nxt = None
            for candidate in _neighbors(current):
                if candidate in remaining:
                    nxt = candidate
                    break
            if nxt is None:
                break
            contour.append(nxt)
            remaining.discard(nxt)
            current = nxt
        if len(contour) >= 4:
            contours.append(simplify(contour))
    return contours


def _perpendicular_distance(point: Point, start: Point, end: Point) -> float:
    px, py = point
    sx, sy = start
    ex, ey = end
    dx, dy = ex - sx, ey - sy
    if dx == 0 and dy == 0:
        return float(np.hypot(px - sx, py - sy))
    return abs(dy * px - dx * py + ex * sy - ey * sx) / float(np.hypot(dx, dy))


def simplify(points: Sequence[Point], tolerance: float = 1.5) -> Polygon:
    """Douglas–Peucker 简化（保留首尾）。"""
    pts = list(points)
    if len(pts) < 3:
        return pts
    start, end = pts[0], pts[-1]
    max_dist, index = 0.0, 0
    for i in range(1, len(pts) - 1):
        dist = _perpendicular_distance(pts[i], start, end)
        if dist > max_dist:
            max_dist, index = dist, i
    if max_dist > tolerance:
        left = simplify(pts[: index + 1], tolerance)
        right = simplify(pts[index:], tolerance)
        return left[:-1] + right
    return [start, end]


def mask_to_svg_path(mask: np.ndarray, *, scale: float = 1.0, max_contours: int = 8) -> str:
    """生成 SVG path 数据（单条 ``d`` 字符串），供 4K 渲染使用。"""
    contours = trace_contours(mask, max_contours=max_contours)
    parts: list[str] = []
    for contour in contours:
        if len(contour) < 3:
            continue
        scaled = [(x * scale, y * scale) for y, x in contour]
        head = scaled[0]
        parts.append(f"M {head[0]:.2f} {head[1]:.2f}")
        for x, y in scaled[1:]:
            parts.append(f"L {x:.2f} {y:.2f}")
        parts.append("Z")
    return " ".join(parts)


def polygon_area(contour: Polygon) -> float:
    """鞋带公式（用于"矢量化后面积偏差"检查）。"""
    if len(contour) < 3:
        return 0.0
    total = 0.0
    for i in range(len(contour)):
        y1, x1 = contour[i]
        y2, x2 = contour[(i + 1) % len(contour)]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


__all__ = [
    "Polygon",
    "boundary_pixels",
    "mask_to_svg_path",
    "polygon_area",
    "simplify",
    "trace_contours",
]
