"""MG 快速路径（V3）——不经 Chrome 直接出 MG 帧序列。

为什么要有它
-----------
`mg_hyperframes` 渲染每个 MG clip 都要起一次 headless Chrome（
`npx hyperframes render`），单次秒级，一部片子里几十个 MG clip 就是几分钟；
预览/草稿导出时这个成本尤其刺眼。但**绝大多数 MG 定义其实不需要浏览器**：
纯色/渐变底 + 文本 + 矩形/圆形/圆环/弧线，配上 opacity/scale/rotate/
translate/width/height 的关键帧——这些用 Pillow 逐帧画得出来，而且**语义确定**。

本模块就是这条快路径：`mg_def` → PIL 逐帧 → PNG 序列 → ffmpeg 编码成带 alpha
的 MOV（与 Chrome 路径同格式，下游 `overlay` 合成链完全不变）。

安全阀：**白名单**。只要出现任何本模块不支持的元素类型/属性/CSS 值，
`fastpath_eligible()` 就返回不可用并给出原因，调用方回退 Chrome 路径。
"能画就画、画不了就交给 Chrome"——绝不允许快路径**猜**。

与 Chrome 路径的语义对齐点（逐条对照 `MGRenderer._render_element`）：
- 定位：`left:50%` + `translateX(-50%)`（center/center 才有 -50% 位移）；
  `left/right/top/bottom:20/60 + offset`；edge 锚点正 offset = 向画布内；
- 变换链顺序：`translateX(-50%) translateY(-50%) scale rotate translateY translateX`
  （CSS 从左到右是矩阵乘法顺序，作用于点是**从右往左**）；
- 变换原点默认元素中心（`transform-origin` 支持 center/left/right/top/bottom 与百分比）；
- 文本元素只认 `font_color`（`color` 在 HTML 路径里被 `STATIC_EXCLUDE_KEYS` 排除，
  因此写 `color` 的文本在 Chrome 下也是白色——快路径必须同样忽略，否则两条路径不一致）；
- 关键帧缺失的首/末端点按 Chromium 语义**从元素底层值补齐**
  （0% → 底层值，100% → 底层值），不是简单"保持最后一帧"；
- `fill-mode: forwards` + 每属性独立时间线（CSS 关键帧只对**声明了该属性**的关键帧插值）。

已知近似（写在文档里，不藏）：
- 文本盒宽度按 PIL 字体的 advance + letter-spacing × 字数测量（Chrome 含末字间距）；
- `line-height` 默认取 1.2×font_size，非 1.2 时以声明值为准；
- 字体只支持 sans-serif 族（映射到系统 CJK 字体），其它字族回退 Chrome。
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from clipwright.config import logger

# ── 白名单 ────────────────────────────────────────────────

#: 支持的元素类型（`arc` 用 top-half 圆角实现，`bg` 铺满全幅）
FAST_ELEMENT_TYPES: frozenset[str] = frozenset(
    {"text", "shape", "line", "circle", "ring", "arc", "bg"}
)

#: 支持的关键帧属性
FAST_KEYFRAME_PROPS: frozenset[str] = frozenset(
    {"opacity", "scale", "rotate", "translate_x", "translate_y", "width", "height"}
)

#: 各元素类型允许出现的字段（白名单；未列出即拒绝）
_COMMON_KEYS = {"type", "keyframes", "x", "y", "x_offset", "y_offset"}
_TEXT_KEYS = _COMMON_KEYS | {
    "content", "font_size", "font_color", "font_weight",
    "letter_spacing", "line_height", "text_shadow",
    # `color` 在 HTML 路径里被 STATIC_EXCLUDE_KEYS 排除 → 文本实际用 font_color 默认白；
    # 这里接受但**同样忽略**，保证两条路径输出一致（详见模块 docstring）。
    "color",
}
_SHAPE_KEYS = _COMMON_KEYS | {
    "shape", "color", "background", "width", "height",
    "border_radius", "stroke_width", "stroke_color",
    "border_width", "border_color", "box_shadow", "transform_origin",
}
_BG_KEYS = {"type", "keyframes", "background", "color"}

_ELEMENT_KEYS: dict[str, set[str]] = {
    "text": set(_TEXT_KEYS),
    "bg": set(_BG_KEYS),
    "shape": set(_SHAPE_KEYS),
    "line": set(_SHAPE_KEYS),
    "circle": set(_SHAPE_KEYS),
    "ring": set(_SHAPE_KEYS),
    "arc": set(_SHAPE_KEYS),
}

#: 支持的字族（空/未声明 → 系统默认 CJK 字体）
_SAFE_FONT_FAMILIES = frozenset(
    {"", "sans-serif", "sans", "system-ui", "arial", "helvetica", "微软雅黑", "msyh", "黑体", "simhei"}
)

#: CSS 命名缓动的贝塞尔控制点（与 CSS 规范一致）
CSS_NAMED_BEZIER: dict[str, tuple[float, float, float, float]] = {
    "linear": (0.0, 0.0, 1.0, 1.0),
    "ease": (0.25, 0.1, 0.25, 1.0),
    "ease-in": (0.42, 0.0, 1.0, 1.0),
    "ease-out": (0.0, 0.0, 0.58, 1.0),
    "ease-in-out": (0.42, 0.0, 0.58, 1.0),
    # 与 MGRenderer.EASING_MAP 的近似值保持一致（无标准关键字）
    "back-out": (0.175, 0.885, 0.32, 1.275),
    "elastic-out": (0.68, -0.55, 0.265, 1.55),
    "bounce": (0.25, 0.46, 0.45, 0.94),
}

#: 底层值（Chromium 在缺少 0%/100% 关键帧时用它合成端点）
_UNDERLYING: dict[str, float] = {
    "opacity": 1.0, "scale": 1.0, "rotate": 0.0,
    "translate_x": 0.0, "translate_y": 0.0,
}

#: 未解析的 `{placeholder}`
_PLACEHOLDER_RE = re.compile(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}")

_NAMED_COLORS: dict[str, tuple[int, int, int, int]] = {    "transparent": (0, 0, 0, 0), "none": (0, 0, 0, 0),
    "black": (0, 0, 0, 255), "white": (255, 255, 255, 255),
    "red": (255, 0, 0, 255), "green": (0, 128, 0, 255),
    "blue": (0, 0, 255, 255), "gray": (128, 128, 128, 255),
    "grey": (128, 128, 128, 255), "yellow": (255, 255, 0, 255),
    "cyan": (0, 255, 255, 255), "magenta": (255, 0, 255, 255),
}


class FastPathIneligible(Exception):
    """快路径不适用（原因逐条可读，便于线上统计"为什么没走快路径"）。"""


@dataclass
class Shadow:
    dx: float
    dy: float
    blur: float
    color: tuple[int, int, int, int]


@dataclass
class Gradient:
    angle_deg: float
    stops: list[tuple[float, tuple[int, int, int, int]]]


@dataclass
class RadialGradient:
    """`radial-gradient(...)`：默认椭圆（PIL 里用方形斜坡图缩放到盒尺寸实现）。

    `shape`：`"ellipse"`（默认）或 `"circle"`——两者都用 **farthest-corner**
    （CSS 默认结束形状）归一化，即最后一个色标恰落在最远角上。
    """
    stops: list[tuple[float, tuple[int, int, int, int]]]
    center: tuple[float, float] = (0.5, 0.5)
    shape: str = "ellipse"


@dataclass
class ElementSpec:
    """一个元素的**已解析**规格（帧循环里只做插值与绘制，不再解析）。"""
    index: int
    kind: str
    # 几何
    width: float
    height: float
    left: float           # 元素盒左上角（未变换前）在画布中的位置
    top: float
    origin: tuple[float, float] = (0.5, 0.5)   # transform-origin（盒内比例）
    center_offset: tuple[float, float] = (0.0, 0.0)  # translateX/Y(-50%) 折算的像素位移
    # 外观
    content: str = ""
    font_size: float = 48.0
    font_weight: str = "normal"
    font_color: tuple[int, int, int, int] = (255, 255, 255, 255)
    letter_spacing: float = 0.0
    line_height: float = 0.0
    fill: tuple[int, int, int, int] | None = None
    gradient: Gradient | None = None
    radial: RadialGradient | None = None
    border_radius: float | str = 0.0
    border_width: float = 0.0
    border_color: tuple[int, int, int, int] | None = None
    shape: str = "rect"
    shadows: list[Shadow] = field(default_factory=list)
    # 动画
    tracks: dict[str, list[tuple[float, float]]] = field(default_factory=dict)
    easings: dict[float, str] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)


# ── CSS 值解析（纯函数，单独可测） ─────────────────────────


def parse_color(value: Any) -> tuple[int, int, int, int] | None:
    """解析 CSS 颜色 → RGBA；不支持/非法返回 None。

    支持 `#rgb` `#rgba` `#rrggbb` `#rrggbbaa`、`rgb()/rgba()`、少量命名色、
    `transparent`/`none`。**不支持** `var(--x)`、`currentColor`、渐变（渐变有单独入口）。
    """
    if value is None:
        return None
    if isinstance(value, (tuple, list)) and len(value) in (3, 4):
        vals = [int(max(0, min(255, float(v)))) for v in value]
        return (vals[0], vals[1], vals[2], vals[3] if len(vals) == 4 else 255)
    s = str(value).strip()
    if not s:
        return None
    low = s.lower()
    if low in _NAMED_COLORS:
        return _NAMED_COLORS[low]
    if low.startswith("#"):
        h = low[1:]
        if not re.fullmatch(r"[0-9a-f]+", h):
            return None
        if len(h) == 3:
            return (int(h[0] * 2, 16), int(h[1] * 2, 16), int(h[2] * 2, 16), 255)
        if len(h) == 4:
            return (int(h[0] * 2, 16), int(h[1] * 2, 16), int(h[2] * 2, 16), int(h[3] * 2, 16))
        if len(h) == 6:
            return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 255)
        if len(h) == 8:
            return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(h[6:8], 16))
        return None
    m = re.fullmatch(r"rgba?\(([^)]*)\)", low)
    if m:
        parts = [p.strip() for p in re.split(r"[,\s/]+", m.group(1)) if p.strip()]
        if len(parts) not in (3, 4):
            return None
        try:
            rgb = []
            for p in parts[:3]:
                rgb.append(int(round(float(p[:-1]) * 255 / 100)) if p.endswith("%") else int(round(float(p))))
            alpha = 255
            if len(parts) == 4:
                a = parts[3]
                alpha = int(round(float(a[:-1]) * 255 / 100)) if a.endswith("%") else int(round(float(a) * 255))
            return (*(max(0, min(255, c)) for c in rgb), max(0, min(255, alpha)))
        except ValueError:
            return None
    return None


def parse_length(value: Any, default: float = 0.0) -> float | None:
    """解析 px 长度（也接受裸数字）；带非 px 单位或表达式返回 None。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return default
    m = re.fullmatch(r"(-?\d+(?:\.\d+)?)(px)?", s)
    if not m:
        return None
    return float(m.group(1))


def parse_gradient(value: Any) -> Gradient | None:
    """解析 `linear-gradient(角度, 色标...)`；其它（radial/conic/repeating/var）返回 None。"""
    if not isinstance(value, str):
        return None
    s = value.strip()
    m = re.match(r"^linear-gradient\((.*)\)$", s, re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    parts = [p.strip() for p in _split_top_level(m.group(1))]
    if len(parts) < 2:
        return None
    angle = 180.0  # CSS 默认 to bottom
    idx = 0
    head = parts[0].lower()
    if head.endswith("deg"):
        try:
            angle = float(head[:-3])
            idx = 1
        except ValueError:
            return None
    elif head.startswith("to "):
        angle = {"to top": 0.0, "to right": 90.0, "to bottom": 180.0, "to left": 270.0}.get(head, 180.0)
        idx = 1
    elif re.match(r"^-?\d+(\.\d+)?$", head):
        angle = float(head)
        idx = 1
    stops: list[tuple[float, tuple[int, int, int, int]]] = []
    for part in parts[idx:]:
        toks = part.rsplit(None, 1)
        color = None
        pos: float | None = None
        if len(toks) == 2 and toks[1].strip().endswith("%"):
            try:
                pos = float(toks[1].strip()[:-1]) / 100.0
                color = parse_color(toks[0])
            except ValueError:
                color = None
        if color is None:
            color = parse_color(part)
        if color is None:
            return None
        stops.append((pos if pos is not None else math.nan, color))
    if len(stops) < 2:
        return None
    # 未标位置的色标按 CSS 规则均分
    n = len(stops)
    for i, (pos, color) in enumerate(stops):
        if math.isnan(pos):
            stops[i] = (i / (n - 1), color)
    stops.sort(key=lambda sp: sp[0])
    return Gradient(angle_deg=angle, stops=stops)


def parse_radial_gradient(value: Any) -> RadialGradient | None:
    """解析 `radial-gradient([ellipse|circle]? [at <位置>]?, 色标...)`。

    只支持"无显式尺寸/范围"的形式（`at center` 或 `at X% Y%`）——带 `300px`/`closest-side`
    之类范围参数的渐变需要真正的几何求解，宁可交给 Chrome。
    """
    if not isinstance(value, str):
        return None
    s = value.strip()
    m = re.match(r"^radial-gradient\((.*)\)$", s, re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    parts = [p.strip() for p in _split_top_level(m.group(1))]
    if len(parts) < 2:
        return None
    center = (0.5, 0.5)
    shape = "ellipse"
    head = parts[0].lower()
    rest = parts
    if head.startswith("at ") or head in ("ellipse", "circle") or " at " in head:
        # 声明段：只允许 shape 关键字 + `at <位置>`
        toks = head.split()
        idx = 0
        if toks and toks[0] in ("ellipse", "circle"):
            shape = toks[0]
            idx = 1
        if idx < len(toks) and toks[idx] == "at":
            pos = toks[idx + 1:]
            # `at center`（单关键字 → 两轴同值）与 `at X% Y%` 都合法
            if len(pos) == 1 and pos[0] in ("center", "left", "right", "top", "bottom"):
                pos = pos * 2
            if len(pos) != 2:
                return None
            vals: list[float] = []
            kw = {"center": 0.5, "left": 0.0, "right": 1.0, "top": 0.0, "bottom": 1.0}
            for t in pos:
                if t in kw:
                    vals.append(kw[t])
                elif t.endswith("%"):
                    try:
                        vals.append(float(t[:-1]) / 100.0)
                    except ValueError:
                        return None
                else:
                    return None
            center = (vals[0], vals[1])
            rest = parts[1:]
        elif idx >= len(toks):
            rest = parts[1:]
        else:
            return None  # 显式尺寸（如 `circle 300px`）不支持
    stops: list[tuple[float, tuple[int, int, int, int]]] = []
    for part in rest:
        toks = part.rsplit(None, 1)
        color, pos = None, None
        if len(toks) == 2 and toks[1].strip().endswith("%"):
            try:
                pos = float(toks[1].strip()[:-1]) / 100.0
                color = parse_color(toks[0])
            except ValueError:
                color = None
        if color is None:
            color = parse_color(part)
        if color is None:
            return None
        stops.append((pos if pos is not None else math.nan, color))
    if len(stops) < 2:
        return None
    n = len(stops)
    for i, (pos, color) in enumerate(stops):
        if math.isnan(pos):
            stops[i] = (i / (n - 1), color)
    stops.sort(key=lambda sp: sp[0])
    return RadialGradient(stops=stops, center=center, shape=shape)


def _split_top_level(text: str) -> list[str]:
    """按顶层逗号切分（忽略括号内的逗号）。"""
    out: list[str] = []
    depth = 0
    cur: list[str] = []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            out.append("".join(cur))
            cur = []
            continue
        cur.append(ch)
    if "".join(cur).strip():
        out.append("".join(cur))
    return out


def parse_shadow(value: Any) -> list[Shadow] | None:
    """解析 `box-shadow` / `text-shadow`（可多条，逗号分隔）；失败返回 None。

    不支持 `inset`、`spread`（第四个长度）——它们无法用简单模糊近似。
    """
    if value is None or value == "":
        return []
    if not isinstance(value, str):
        return None
    shadows: list[Shadow] = []
    for chunk in _split_top_level(value):
        s = chunk.strip()
        if not s:
            continue
        if "inset" in s.lower():
            return None
        # 颜色可以出现在任意位置：先摘出颜色，剩下的是长度列表
        color: tuple[int, int, int, int] | None = None
        rest: list[str] = []
        for tok in s.split():
            c = parse_color(tok)
            if c is not None and color is None:
                color = c
            else:
                rest.append(tok)
        if color is None or not rest:
            return None
        if len(rest) > 3:  # dx dy blur [spread] —— spread 不支持
            return None
        try:
            nums = [float(t[:-2] if t.endswith("px") else t) for t in rest]
        except ValueError:
            return None
        nums += [0.0] * (3 - len(nums))
        shadows.append(Shadow(dx=nums[0], dy=nums[1], blur=max(0.0, nums[2]), color=color))
    return shadows


def parse_transform_origin(value: Any) -> tuple[float, float] | None:
    """解析 `transform-origin` → 盒内比例 (ox, oy)；关键字/百分比/px 都支持。"""
    if value is None or value == "":
        return (0.5, 0.5)
    s = str(value).strip().lower()
    kw = {
        "center": 0.5, "left": 0.0, "right": 1.0,
        "top": 0.0, "bottom": 1.0,
    }
    toks = s.split()
    # 单关键字：`center bottom` / `bottom center` 等
    if len(toks) == 2 and toks[0] in ("center", "top", "bottom") and toks[1] in ("center", "left", "right"):
        toks = [toks[1], toks[0]]  # 关键字对是「水平 垂直」以外的顺序 → 归一
    vals: list[float | None] = []
    for t in toks:
        if t in kw:
            vals.append(kw[t])
        elif t.endswith("%"):
            try:
                vals.append(float(t[:-1]) / 100.0)
            except ValueError:
                return None
        else:
            return None  # px 形式（相对盒尺寸）在调用方折算，这里只支持关键字/百分比
    if len(vals) == 1:
        # 单值：水平；垂直取 center
        return (vals[0] if vals[0] is not None else 0.5, 0.5)
    if len(vals) == 2:
        return (vals[0] or 0.0, vals[1] or 0.0)
    return None


def _split_origin_tokens(value: Any) -> tuple[float | None, float | None]:
    """`transform-origin` 的 px 兜底：在已知盒尺寸时把 px 折算成比例。"""
    if not value:
        return (None, None)
    toks = str(value).strip().lower().split()
    out: list[float | None] = []
    kw = {"center": 0.5, "left": 0.0, "right": 1.0, "top": 0.0, "bottom": 1.0}
    for t in toks[:2]:
        if t in kw:
            out.append(kw[t])
        elif t.endswith("%"):
            try:
                out.append(float(t[:-1]) / 100.0)
            except ValueError:
                out.append(None)
        else:
            out.append(None)
    while len(out) < 2:
        out.append(0.5)
    return (out[0], out[1])


def _cubic_bezier(x1: float, y1: float, x2: float, y2: float, t: float) -> float:
    """CSS 缓动求值：先在 x 上反解参数，再取 y（与浏览器一致）。"""
    if t <= 0:
        return 0.0
    if t >= 1:
        return 1.0
    # 牛顿迭代 + 二分兜底
    lo, hi = 0.0, 1.0
    u = t
    for _ in range(24):
        x = 3 * (1 - u) ** 2 * u * x1 + 3 * (1 - u) * u * u * x2 + u ** 3
        if abs(x - t) < 1e-6:
            break
        if x < t:
            lo = u
        else:
            hi = u
        u = (lo + hi) / 2
    return 3 * (1 - u) ** 2 * u * y1 + 3 * (1 - u) * u * u * y2 + u ** 3


def ease(t: float, easing: Any) -> float:
    """缓动求值；未知缓动 → 线性（调用方已在 eligibility 里拒绝未知值）。"""
    t = max(0.0, min(1.0, t))
    if not easing:
        return t
    name = easing if isinstance(easing, str) else ""
    if isinstance(easing, (list, tuple)) and len(easing) == 4:
        try:
            nums = tuple(float(v) for v in easing)
        except (TypeError, ValueError):
            return t
        if nums == (0.0, 0.0, 1.0, 1.0):
            return t
        return _cubic_bezier(*nums, t)
    key = name.strip().lower()
    if key in CSS_NAMED_BEZIER:
        pts = CSS_NAMED_BEZIER[key]
        # linear 直接返回 t：贝塞尔反解是迭代法，没必要为直线引入误差
        return t if pts == (0.0, 0.0, 1.0, 1.0) else _cubic_bezier(*pts, t)
    m = re.fullmatch(r"cubic-bezier\(([^)]*)\)", key)
    if m:
        try:
            nums = [float(v) for v in re.split(r"[,\s]+", m.group(1).strip()) if v]
            if len(nums) == 4:
                if nums == [0.0, 0.0, 1.0, 1.0]:
                    return t
                return _cubic_bezier(*nums, t)
        except ValueError:
            pass
    return t


def track_value(
    points: list[tuple[float, float]], t: float, easing_for: dict[float, str],
    dur: float, underlying: float,
) -> float:
    """在单个属性自己的时间线上取值（CSS 关键帧语义）。

    - 只对**声明了该属性**的关键帧插值；
    - 缺 0% 端点 → 用底层值补齐；缺 100% 端点 → 也用底层值补齐
      （Chromium 对隐式关键帧的处理，不是"保持最后一帧"）；
    - 区间的缓动取该区间**起点关键帧**上声明的 easing。
    """
    pts = list(points)
    if not pts:
        return underlying
    pts.sort(key=lambda p: p[0])
    if pts[0][0] > 0.0:
        pts.insert(0, (0.0, underlying))
    if pts[-1][0] < dur:
        pts.append((dur, underlying))
    if t <= pts[0][0]:
        return pts[0][1]
    if t >= pts[-1][0]:
        return pts[-1][1]
    for i in range(len(pts) - 1):
        ta, va = pts[i]
        tb, vb = pts[i + 1]
        if ta <= t <= tb:
            span = tb - ta
            if span <= 0:
                return vb
            u = (t - ta) / span
            return va + (vb - va) * ease(u, easing_for.get(ta, ""))
    return pts[-1][1]


# ── 资格判定 ──────────────────────────────────────────────


def _check_style(style: Any, bg_fill_allowed: bool) -> None:
    if style in (None, {}):
        return
    if not isinstance(style, dict):
        raise FastPathIneligible("style 结构异常")
    for key, val in style.items():
        if key == "background":
            if val in (None, "", "transparent"):
                continue
            if (parse_color(val) is None and parse_gradient(val) is None
                    and parse_radial_gradient(val) is None):
                raise FastPathIneligible(f"style.background 无法解析: {str(val)[:40]}")
        elif key == "font_family":
            if str(val or "").strip().lower() not in _SAFE_FONT_FAMILIES:
                raise FastPathIneligible(f"不支持的字族: {str(val)[:30]}")
        else:
            raise FastPathIneligible(f"style 含未支持字段: {key}")


def fastpath_eligible(mg_def: Any, params: dict | None = None) -> tuple[bool, str]:
    """判断一个 `mg_def` 能否走 PIL 快路径。返回 `(可否, 原因)`。

    宁可多拒绝：任何无法**忠实**复现的东西都回退 Chrome。原因文案会写进日志，
    便于统计"为什么这部片子没走快路径"。
    """
    try:
        _build_specs(mg_def, params or {})
        return True, ""
    except FastPathIneligible as exc:
        return False, str(exc)
    except Exception as exc:  # 解析期任何意外都按不可用处理（绝不让它影响主渲染）
        return False, f"快路径判定异常: {exc}"


def _build_specs(mg_def: dict[str, Any], params: dict[str, Any]) -> list[ElementSpec]:
    """把 `mg_def` 解析成 `ElementSpec` 列表；任何不支持的东西抛 `FastPathIneligible`。"""
    if not isinstance(mg_def, dict):
        raise FastPathIneligible("mg_def 不是对象")
    elements = mg_def.get("elements")
    if not isinstance(elements, list) or not elements:
        raise FastPathIneligible("没有元素")
    dur = float(mg_def.get("duration_sec") or 0)
    if dur <= 0:
        raise FastPathIneligible("duration_sec 非法")
    _check_style(mg_def.get("style"), bg_fill_allowed=True)

    def fill(val: Any) -> Any:
        if isinstance(val, str):
            for k, v in params.items():
                val = val.replace("{" + k + "}", "" if v is None else str(v))
        return val

    specs: list[ElementSpec] = []
    for i, elem in enumerate(elements):
        if not isinstance(elem, dict):
            raise FastPathIneligible(f"元素 #{i} 不是对象")
        kind = str(elem.get("type", "text"))
        if kind not in FAST_ELEMENT_TYPES:
            raise FastPathIneligible(f"元素类型 {kind!r} 需 Chrome（元素 #{i}）")
        allowed = _ELEMENT_KEYS[kind]
        extra = set(elem.keys()) - allowed
        if extra:
            raise FastPathIneligible(f"元素 #{i}({kind}) 含未支持字段: {', '.join(sorted(extra))}")
        kfs = elem.get("keyframes") or []
        if not isinstance(kfs, list) or not kfs:
            raise FastPathIneligible(f"元素 #{i} 无关键帧")
        for kf in kfs:
            if not isinstance(kf, dict):
                raise FastPathIneligible(f"元素 #{i} 关键帧结构异常")
            for key in kf:
                if key == "time":
                    continue
                if key == "easing":
                    if not _easing_supported(kf[key]):
                        raise FastPathIneligible(f"元素 #{i} 用了未支持缓动: {str(kf[key])[:30]}")
                    continue
                if key not in FAST_KEYFRAME_PROPS:
                    raise FastPathIneligible(f"元素 #{i} 关键帧属性 {key!r} 需 Chrome")
                if kind == "text" and key in ("width", "height"):
                    raise FastPathIneligible(f"元素 #{i} 文本尺寸关键帧需 Chrome")

        spec = _build_one(kind, elem, fill, i, dur)
        # bg 是 `inset:0` 的全幅底层：只有 opacity 是"安全的"关键帧属性。
        # 尺寸/变换关键帧在 HTML 路径会作用于全幅层（以元素中心为原点），
        # 快路径不建模，因此直接判为不适用而不是硬猜。
        if kind == "bg":
            extra_props = set(spec.tracks) - {"opacity"}
            if extra_props:
                raise FastPathIneligible(
                    f"元素 #{i}(bg) 含尺寸/变换关键帧 {', '.join(sorted(extra_props))}，需 Chrome")
        # 没被 params 解析掉的 `{placeholder}` 绝不能画进画面——HTML 路径靠
        # `RenderService._ensure_mg_placeholders_filled` 兜底，快路径没有那一步，
        # 因此直接把这种情况判为"不适用"（回退 Chrome，由既有兜底逻辑处理）。
        if _PLACEHOLDER_RE.search(spec.content or ""):
            raise FastPathIneligible(
                f"元素 #{i} 的文本仍含未解析占位符（缺 params），回退 Chrome")
        specs.append(spec)
    return specs


def _easing_supported(value: Any) -> bool:
    if value in (None, ""):
        return True
    if isinstance(value, (list, tuple)):
        return len(value) == 4
    s = str(value).strip().lower()
    if s in CSS_NAMED_BEZIER:
        return True
    m = re.fullmatch(r"cubic-bezier\(([^)]*)\)", s)
    if not m:
        return False
    try:
        return len([v for v in re.split(r"[,\s]+", m.group(1).strip()) if v]) == 4
    except Exception:
        return False


def _parse_background(value: Any, idx: int):
    """背景值 → (线性渐变, 径向渐变, 纯色) 三元组；无法解析抛 `FastPathIneligible`。"""
    grad = parse_gradient(value)
    if grad is not None:
        return grad, None, None
    radial = parse_radial_gradient(value)
    if radial is not None:
        return None, radial, None
    color = parse_color(value)
    if color is None:
        raise FastPathIneligible(f"元素 #{idx} 背景无法解析: {str(value)[:36]}")
    return None, None, color


def _build_one(kind: str, elem: dict, fill, idx: int, dur: float) -> ElementSpec:
    """解析单个元素（尺寸/位置/外观/动画轨迹）。"""
    x = elem.get("x", "center")
    y = elem.get("y", "center")
    x_off = parse_length(elem.get("x_offset"), 0.0)
    y_off = parse_length(elem.get("y_offset"), 0.0)
    if x_off is None or y_off is None:
        raise FastPathIneligible(f"元素 #{idx} offset 非 px 数值")
    x_off, y_off = float(x_off), float(y_off)

    width = parse_length(elem.get("width"), 100.0)
    height = parse_length(elem.get("height"), 100.0)
    if width is None or height is None:
        raise FastPathIneligible(f"元素 #{idx} 尺寸非 px 数值")

    spec = ElementSpec(index=idx, kind=kind, width=0.0, height=0.0, left=0.0, top=0.0)

    # 关键帧属性 → 轨迹（时间, 值）；尺寸类属性先记原始值，稍后按需解析
    raw_tracks: dict[str, list[tuple[float, Any]]] = {}
    easings: dict[float, str] = {}
    for kf in elem.get("keyframes") or []:
        t = float(kf.get("time", 0) or 0)
        t = max(0.0, min(t, dur))
        if kf.get("easing"):
            easings[t] = kf["easing"]
        for key, val in kf.items():
            if key in ("time", "easing"):
                continue
            raw_tracks.setdefault(key, []).append((t, fill(val)))

    # 位置：与 MGRenderer 的 CSS 输出逐条对齐
    if x == "center":
        left = 0.5  # 相对画布，宽度未知时留待 layout 阶段
        x_rel = 0.5
        x_anchor = "center"
    elif x == "left":
        left, x_rel, x_anchor = 20 + x_off, None, "abs"
    elif x == "right":
        left, x_rel, x_anchor = 20 + x_off, None, "right"
    else:
        v = parse_length(x, None)
        if v is None:
            raise FastPathIneligible(f"元素 #{idx} 的 x 无法解析: {str(x)[:20]}")
        left, x_rel, x_anchor = float(v), None, "abs"

    if y == "center":
        top, y_anchor = 0.5, "middle"
    elif y == "top":
        top, y_anchor = 20 + y_off, "abs"
    elif y == "bottom":
        top, y_anchor = 60 + y_off, "bottom"
    else:
        v = parse_length(y, None)
        if v is None:
            raise FastPathIneligible(f"元素 #{idx} 的 y 无法解析: {str(y)[:20]}")
        top, y_anchor = float(v), "abs"

    spec.payload = {
        "x_anchor": x_anchor, "x_rel": x_rel, "x_off": x_off,
        "y_anchor": y_anchor, "top": top, "y_off": y_off,
        "left": left, "raw_width": float(width), "raw_height": float(height),
    }

    # 外观
    if kind == "bg":
        bg = elem.get("background") or elem.get("color")
        spec.gradient, spec.radial, spec.fill = _parse_background(bg, idx)
        spec.payload["full_bleed"] = True
    elif kind == "text":
        content = fill(elem.get("content", ""))
        if content is None:
            content = ""
        content = str(content)
        if "\n" in content or "\r" in content:
            raise FastPathIneligible(f"元素 #{idx} 文本含换行（HTML 会折叠、PIL 不会）")
        spec.content = content
        fs = parse_length(elem.get("font_size"), 48.0)
        if fs is None or fs <= 0:
            raise FastPathIneligible(f"元素 #{idx} font_size 非法")
        spec.font_size = float(fs)
        spec.font_weight = str(fill(elem.get("font_weight", "normal")) or "normal")
        if spec.font_weight.lower() not in ("normal", "bold", "400", "500", "600", "700", "800", "900", "bolder", "lighter"):
            raise FastPathIneligible(f"元素 #{idx} font_weight 非法: {spec.font_weight[:20]}")
        color = parse_color(fill(elem.get("font_color", "#ffffff")))
        if color is None:
            raise FastPathIneligible(f"元素 #{idx} font_color 非法")
        spec.font_color = color
        ls = parse_length(elem.get("letter_spacing"), 0.0)
        if ls is None:
            raise FastPathIneligible(f"元素 #{idx} letter_spacing 非法")
        spec.letter_spacing = float(ls)
        lh = elem.get("line_height")
        if lh is None or lh == "":
            spec.line_height = 1.2 * spec.font_size
        else:
            v = parse_length(lh, None)
            if v is None:
                if isinstance(lh, (int, float)) or re.fullmatch(r"\d+(\.\d+)?", str(lh).strip()):
                    spec.line_height = float(lh) * spec.font_size  # 倍数
                else:
                    raise FastPathIneligible(f"元素 #{idx} line_height 非法: {str(lh)[:20]}")
            elif float(v) > 0 and float(v) <= 4.0 and not str(lh).strip().endswith("px"):
                spec.line_height = float(v) * spec.font_size
            else:
                spec.line_height = float(v)
        shadows = parse_shadow(elem.get("text_shadow"))
        if shadows is None:
            raise FastPathIneligible(f"元素 #{idx} text_shadow 无法解析")
        spec.shadows = shadows
        # 文本盒尺寸在 layout 阶段按字体测量
        spec.width = math.nan
        spec.height = spec.line_height
    else:  # shape / line / circle / ring / arc
        bg = elem.get("background") or elem.get("color") or "#4f8cff"
        spec.gradient, spec.radial, spec.fill = _parse_background(bg, idx)
        spec.shape = str(elem.get("shape", "rect") or "rect")
        spec.width = float(width)
        spec.height = float(height)
        # 圆角：circle/ring/arc 有类型默认值
        br = elem.get("border_radius", None)
        if kind == "circle":
            br = "50%"
        elif kind == "ring":
            br = "50%"
        elif kind == "arc":
            br = "arc-top"
        elif spec.shape == "ellipse":
            br = "50%"
        elif br is None:
            br = 4
        if br == "arc-top":
            spec.border_radius = "arc-top"  # 上半椭圆（border-radius: 50% 50% 0 0）
        elif isinstance(br, str) and br.strip().endswith("%"):
            try:
                pct = float(br.strip()[:-1])
            except ValueError:
                raise FastPathIneligible(f"元素 #{idx} border_radius 非法: {br[:20]}")
            spec.border_radius = "50%" if pct >= 50 else float(pct) / 100.0
        else:
            v = parse_length(br, 0.0)
            if v is None:
                raise FastPathIneligible(f"元素 #{idx} border_radius 非法: {str(br)[:20]}")
            spec.border_radius = float(v)
        sw = elem.get("stroke_width", elem.get("border_width", 0))
        swv = parse_length(sw, 0.0)
        if swv is None:
            raise FastPathIneligible(f"元素 #{idx} border_width 非法")
        spec.border_width = float(swv)
        sw_color = elem.get("stroke_color", elem.get("border_color", None))
        if spec.border_width > 0:
            c = parse_color(fill(sw_color)) if sw_color is not None else None
            if c is None:
                c = spec.fill
            if c is None:
                raise FastPathIneligible(f"元素 #{idx} 描边色无法解析")
            spec.border_color = c
        if kind == "ring" and spec.border_width <= 0:
            raise FastPathIneligible(f"元素 #{idx} ring 无描边宽度（Chrome 下也是空白）")
        shadows = parse_shadow(elem.get("box_shadow"))
        if shadows is None:
            raise FastPathIneligible(f"元素 #{idx} box_shadow 无法解析")
        spec.shadows = shadows
        origin = parse_transform_origin(elem.get("transform_origin"))
        if origin is None:
            raise FastPathIneligible(f"元素 #{idx} transform_origin 非法: {str(elem.get('transform_origin'))[:24]}")
        spec.origin = origin

    # 动画轨迹（尺寸类属性按元素类型折算）
    underlying: dict[str, float] = dict(_UNDERLYING)
    underlying["width"] = float(width)
    underlying["height"] = float(height)
    for prop, pts in raw_tracks.items():
        if prop in ("width", "height"):
            vals: list[tuple[float, float]] = []
            for t, val in pts:
                v = parse_length(val, None)
                if v is None:
                    raise FastPathIneligible(f"元素 #{idx} 的关键帧 {prop} 非 px 数值")
                vals.append((t, float(v)))
            if kind == "text":
                raise FastPathIneligible(f"元素 #{idx} 文本尺寸关键帧需 Chrome")
            spec.tracks[prop] = vals
        elif prop == "opacity":
            vals = []
            for t, val in pts:
                try:
                    vals.append((t, max(0.0, min(1.0, float(val)))))
                except (TypeError, ValueError):
                    raise FastPathIneligible(f"元素 #{idx} 的关键帧 opacity 非数值")
            spec.tracks[prop] = vals
        else:  # scale / rotate / translate_x / translate_y
            vals = []
            for t, val in pts:
                v = parse_length(val, None)
                if v is None:
                    raise FastPathIneligible(f"元素 #{idx} 的关键帧 {prop} 非数值")
                vals.append((t, float(v)))
            spec.tracks[prop] = vals
    spec.easings = easings
    spec.payload["underlying"] = underlying
    return spec


# ── 布局（需要字体度量，故独立于解析） ─────────────────────


# ── 字体（按字符选字体：对齐 Chrome 的 sans-serif + CJK 回退） ──

#: Chrome 在 Windows 上 `sans-serif` 的默认字体是 Arial；CJK 字符走字体回退，
#: 落到微软雅黑/黑体。快路径必须同样"逐字符挑字体"，否则同一段文本里
#: 拉丁字母的字形与字宽都会和 Chrome 不一致（实测这是整帧差异的最大单一来源）。
_LATIN_FONT_CANDIDATES: list[tuple[str, int]] = [
    (r"C:\Windows\Fonts\arial.ttf", 0),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),
    ("/System/Library/Fonts/Helvetica.ttc", 0),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0),
]
_LATIN_BOLD_CANDIDATES: list[tuple[str, int]] = [
    (r"C:\Windows\Fonts\arialbd.ttf", 0),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 0),
    ("/System/Library/Fonts/Helvetica.ttc", 0),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 0),
]
_CJK_FONT_CANDIDATES: list[tuple[str, int]] = [
    (r"C:\Windows\Fonts\msyh.ttc", 0),
    (r"C:\Windows\Fonts\simhei.ttf", 0),
    (r"C:\Windows\Fonts\simsun.ttc", 0),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0),
    ("/System/Library/Fonts/PingFang.ttc", 0),
]
_CJK_BOLD_CANDIDATES: list[tuple[str, int]] = [
    (r"C:\Windows\Fonts\msyhbd.ttc", 0),
    (r"C:\Windows\Fonts\simhei.ttf", 0),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 0),
    ("/System/Library/Fonts/PingFang.ttc", 0),
]

font_cache: dict[tuple[bool, int, bool], Any] = {}


def _is_cjk(ch: str) -> bool:
    """是否需要用 CJK 字体渲染（CJK 统一表意/标点/全角/假名/谚文）。"""
    o = ord(ch)
    return (
        0x2E80 <= o <= 0x9FFF      # CJK 部首扩展 ~ 统一表意
        or 0x3000 <= o <= 0x303F   # CJK 标点
        or 0x3040 <= o <= 0x30FF   # 假名
        or 0xAC00 <= o <= 0xD7AF   # 谚文
        or 0xF900 <= o <= 0xFAFF   # 兼容表意
        or 0xFF00 <= o <= 0xFFEF   # 全角/半角形式
        or 0x20000 <= o <= 0x3FFFF
    )


def resolve_font(bold: bool, size: int, cjk: bool = True):
    """解析可用字体（带缓存）。找不到任何字体 → 抛 `FastPathIneligible`。"""
    from PIL import ImageFont

    key = (bool(bold), int(size), bool(cjk))
    if key in font_cache:
        return font_cache[key]
    if cjk:
        candidates = _CJK_BOLD_CANDIDATES if bold else _CJK_FONT_CANDIDATES
    else:
        candidates = _LATIN_BOLD_CANDIDATES if bold else _LATIN_FONT_CANDIDATES
    for path, index in candidates:
        if Path(path).exists():
            try:
                font = ImageFont.truetype(path, int(size), index=index)
                font_cache[key] = font
                return font
            except Exception:
                continue
    raise FastPathIneligible("找不到可用字体（PIL 无法测量/绘制文本）")


def _text_run(text: str, bold: bool, size: int) -> list[tuple[str, Any]]:
    """把文本切成 [字符 → 字体]（与 Chrome 的 sans-serif + CJK 回退同构）。"""
    px_size = max(1, int(round(size)))
    return [(ch, resolve_font(bold, px_size, _is_cjk(ch))) for ch in text]


def _measure_run(run: list[tuple[str, Any]], letter_spacing: float) -> float:
    """文本盒宽度：逐字符 advance 之和 + letter-spacing × 字符数（Chrome 含末字间距）。"""
    return float(sum(font.getlength(ch) for ch, font in run)) + letter_spacing * len(run)


def _draw_run(draw, run: list[tuple[str, Any]], x: float, baseline: float,
              color, letter_spacing: float) -> None:
    """逐字符绘制（不同字符可能用不同字体）。"""
    cursor = x
    for ch, font in run:
        draw.text((cursor, baseline), ch, font=font, fill=color, anchor="ls")
        cursor += font.getlength(ch) + letter_spacing


def _run_metrics(run: list[tuple[str, Any]]) -> tuple[int, int]:
    """取运行内最大的 (ascent, descent)（混排时行盒取最高者）。"""
    asc = desc = 0
    for _ch, font in run:
        a, d = font.getmetrics()
        asc, desc = max(asc, a), max(desc, d)
    return asc, desc


def _is_bold(weight: str) -> bool:
    w = (weight or "").strip().lower()
    if w in ("bold", "bolder"):
        return True
    try:
        return int(w) >= 600
    except ValueError:
        return False


def layout_specs(specs: list[ElementSpec], width: int, height: int) -> list[ElementSpec]:
    """补齐文本盒尺寸与绝对坐标（需要字体度量）。原地修改并返回。"""
    for spec in specs:
        p = spec.payload
        if spec.kind == "text":
            run = _text_run(spec.content, _is_bold(spec.font_weight), spec.font_size)
            spec.width = float(_measure_run(run, spec.letter_spacing))
            spec.height = float(spec.line_height)
            if spec.width <= 0:
                spec.width = 1.0
        w, h = spec.width, spec.height
        # 这里给出 CSS **变换前**的盒位置（`left:50%` 就是 50），
        # `translateX(-50%)` 的折算放在 `_transform_paste` 里做——位置只算一次。
        if p["x_anchor"] == "center":
            l = width * 0.5 + p["x_off"]
        elif p["x_anchor"] == "right":
            l = width - p["left"] - w
        else:
            l = p["left"]
        if p["y_anchor"] == "middle":
            t = height * 0.5 + p["y_off"]
        elif p["y_anchor"] == "bottom":
            t = height - p["top"] - h
        else:
            t = p["top"]
        spec.left, spec.top = float(l), float(t)
    return specs


# ── 绘制 ─────────────────────────────────────────────────


def _ramp_image(grad: Gradient, w: int, h: int, cache: dict | None = None):
    """线性渐变 → 尺寸 (w,h) 的 RGBA 图（按 CSS 渐变线做**精确投影**）。

    做法：先按 CSS 规则算出渐变线长度 `L = |w·sinθ| + |h·cosθ|`，把色标渲成一条
    1×L 的斜坡带；再用一次仿射变换把每个输出像素 x 映射到斜坡带的对应列
    （源 y 恒为 0）。投影公式就是 CSS 的定义，因此**不存在未覆盖区域**。

    （旧实现用"旋转斜坡带再中心裁切"，非 45° 时旋转后的画布装不下目标尺寸，
    裁切区域被补齐成透明 → 渐变背景右侧/下侧整片消失。这是实测发现的真 bug，
    `tests/clipwright/test_mg_fastpath.py::TestShapes::test_gradient_covers_box_at_all_angles`
    锁死该回归。）
    """
    from PIL import Image

    w, h = max(1, int(w)), max(1, int(h))
    key = ("lin", grad.angle_deg, tuple(grad.stops), w, h)
    if cache is not None and key in cache:
        return cache[key]
    theta = math.radians(grad.angle_deg)
    sin_t, cos_t = math.sin(theta), math.cos(theta)
    length = abs(w * sin_t) + abs(h * cos_t)
    if length <= 0.5:
        length = 1.0
    n = max(2, int(math.ceil(length)) + 1)
    strip = Image.new("RGBA", (n, 1))
    stops = grad.stops
    px = strip.load()
    for i in range(n):
        u = i / (n - 1)
        color = stops[-1][1]
        for j in range(len(stops) - 1):
            p0, c0 = stops[j]
            p1, c1 = stops[j + 1]
            if p0 <= u <= p1:
                span = p1 - p0
                f = 0.0 if span <= 0 else (u - p0) / span
                color = tuple(int(round(c0[k] + (c1[k] - c0[k]) * f)) for k in range(4))
                break
        px[i, 0] = color
    # CSS：0deg = to top，90deg = to right（屏幕 y 向下）→ 递增方向
    dx, dy = sin_t, -cos_t
    a = (n - 1) / length * dx
    b = (n - 1) / length * dy
    c = ((n - 1) / length) * ((0.5 - w / 2.0) * dx + (0.5 - h / 2.0) * dy) + 0.5 * (n - 1)
    out = strip.transform((w, h), Image.AFFINE, (a, b, c, 0.0, 0.0, 0.0),
                          resample=Image.BILINEAR)
    if cache is not None:
        cache[key] = out
    return out


def _radial_image(radial: RadialGradient, w: int, h: int, cache: dict | None = None):
    """径向渐变 → (w,h) RGBA 图：方形斜坡图缩放到目标尺寸即得椭圆分布。"""
    from PIL import Image

    w, h = max(1, int(w)), max(1, int(h))
    key = ("rad", tuple(radial.stops), radial.center, radial.shape, w, h)
    if cache is not None and key in cache:
        return cache[key]
    n = 160
    base = Image.new("RGBA", (n, n))
    px = base.load()
    stops = radial.stops
    cx, cy = radial.center[0], radial.center[1]
    # CSS 默认结束形状 = farthest-corner：最后一个色标恰落在最远角上。
    # 椭圆用归一化正方形（缩放到盒尺寸后自然成为椭圆）；正圆把盒的宽高比代入距离度量，
    # 这样同一个正方形底图缩放后得到的仍是正圆。
    ax = 1.0 if radial.shape != "circle" else float(w)
    ay = 1.0 if radial.shape != "circle" else float(h)
    far = max(
        math.hypot((px_ - cx) * ax, (py_ - cy) * ay)
        for px_, py_ in ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0))
    ) or 1.0
    for yy in range(n):
        for xx in range(n):
            u = math.hypot((xx / (n - 1) - cx) * ax, (yy / (n - 1) - cy) * ay) / far
            u = max(0.0, min(1.0, u))
            color = stops[-1][1]
            for j in range(len(stops) - 1):
                p0, c0 = stops[j]
                p1, c1 = stops[j + 1]
                if p0 <= u <= p1:
                    span = p1 - p0
                    f = 0.0 if span <= 0 else (u - p0) / span
                    color = tuple(int(round(c0[k] + (c1[k] - c0[k]) * f)) for k in range(4))
                    break
            px[xx, yy] = color
    out = base.resize((w, h), Image.BICUBIC)
    if cache is not None:
        cache[key] = out
    return out


def _shape_mask(spec: ElementSpec, w: int, h: int):
    """形状遮罩（L 模式，255 = 实心）。"""
    from PIL import Image, ImageDraw

    mask = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(mask)
    br = spec.border_radius
    if spec.kind in ("circle", "ring") or br == "50%":
        d.ellipse([0, 0, w - 1, h - 1], fill=255)
    elif spec.kind == "arc" or br == "arc-top":
        # border-radius: 50% 50% 0 0 → 上半椭圆 + 下半矩形
        d.pieslice([0, 0, w - 1, h - 1], 180, 360, fill=255)
        d.rectangle([0, h // 2, w - 1, h - 1], fill=255)
    else:
        radius = 0.0
        try:
            radius = float(br)
        except (TypeError, ValueError):
            radius = 0.0
        if isinstance(br, float) and 0 < br < 1:
            radius = br * min(w, h) / 2  # 百分比圆角
        radius = max(0.0, min(radius, min(w, h) / 2.0))
        if radius > 0.5:
            d.rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=255)
        else:
            d.rectangle([0, 0, w - 1, h - 1], fill=255)
    return mask


def _paint_element(spec: ElementSpec, cache: dict | None = None):
    """把一个元素画进它自己的 RGBA 图层（含阴影，含 padding）。返回 (img, pad)。"""
    from PIL import Image, ImageDraw, ImageFilter

    pad = 0.0
    for sh in spec.shadows:
        pad = max(pad, abs(sh.dx) + sh.blur, abs(sh.dy) + sh.blur)
    pad = int(math.ceil(pad)) + 2

    w = max(1, int(math.ceil(spec.width)))
    h = max(1, int(math.ceil(spec.height)))
    lw, lh = w + 2 * pad, h + 2 * pad
    layer = Image.new("RGBA", (lw, lh), (0, 0, 0, 0))

    def _body_image() -> "Image.Image":
        """不含阴影的元素本体（尺寸 w×h）。"""
        body = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        if spec.kind == "text":
            run = _text_run(spec.content, _is_bold(spec.font_weight), spec.font_size)
            draw = ImageDraw.Draw(body)
            ascent, descent = _run_metrics(run)
            half_leading = max(0.0, (spec.line_height - (ascent + descent)) / 2.0)
            _draw_run(draw, run, 0.0, half_leading + ascent, spec.font_color, spec.letter_spacing)
            return body
        mask = _shape_mask(spec, w, h)
        fill = spec.fill
        if fill is not None and not (spec.kind == "ring"):
            body.paste(Image.new("RGBA", (w, h), fill), (0, 0), mask)
        elif fill is not None and spec.kind == "ring":
            pass  # ring 只有描边
        if spec.gradient is not None:
            body.paste(_ramp_image(spec.gradient, w, h, cache), (0, 0), mask)
        elif spec.radial is not None:
            body.paste(_radial_image(spec.radial, w, h, cache), (0, 0), mask)
        if spec.border_width > 0 and spec.border_color is not None:
            border_mask = Image.new("L", (w, h), 0)
            bd = ImageDraw.Draw(border_mask)
            sw = max(1, int(round(spec.border_width)))
            if spec.kind in ("circle", "ring") or spec.border_radius == "50%":
                bd.ellipse([0, 0, w - 1, h - 1], outline=255, width=sw)
            elif spec.kind == "arc" or spec.border_radius == "arc-top":
                bd.arc([0, 0, w - 1, h - 1], 180, 360, fill=255, width=sw)
                bd.line([0, h - 1, w - 1, h - 1], fill=255, width=sw)
            else:
                br = spec.border_radius
                radius = float(br) if isinstance(br, (int, float)) else 0.0
                if isinstance(br, float) and 0 < br < 1:
                    radius = br * min(w, h) / 2
                radius = max(0.0, min(radius, min(w, h) / 2.0))
                if radius > 0.5:
                    bd.rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, outline=255, width=sw)
                else:
                    bd.rectangle([0, 0, w - 1, h - 1], outline=255, width=sw)
            body.paste(Image.new("RGBA", (w, h), spec.border_color), (0, 0), border_mask)
        return body

    # 阴影（先画，位于本体之下）
    for sh in spec.shadows:
        shadow_layer = Image.new("RGBA", (lw, lh), (0, 0, 0, 0))
        core = _body_image()
        if spec.kind == "text":
            core = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            run = _text_run(spec.content, _is_bold(spec.font_weight), spec.font_size)
            ascent, descent = _run_metrics(run)
            half_leading = max(0.0, (spec.line_height - (ascent + descent)) / 2.0)
            sd = ImageDraw.Draw(core)
            _draw_run(sd, run, 0.0, half_leading + ascent, sh.color, spec.letter_spacing)
        else:
            # 形状阴影：用纯色重画本体
            mask = _shape_mask(spec, w, h)
            core = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            core.paste(Image.new("RGBA", (w, h), sh.color), (0, 0), mask)
            if spec.kind == "ring" and spec.border_width > 0:
                bm = Image.new("L", (w, h), 0)
                ImageDraw.Draw(bm).ellipse([0, 0, w - 1, h - 1], outline=255,
                                           width=max(1, int(round(spec.border_width))))
                core.paste(Image.new("RGBA", (w, h), sh.color), (0, 0), bm)
        shadow_layer.paste(core, (int(pad + sh.dx), int(pad + sh.dy)), core)
        if sh.blur > 0:
            shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(sh.blur / 2.0))
        layer = Image.alpha_composite(layer, shadow_layer)

    body = _body_image()
    layer.alpha_composite(body, (pad, pad))
    return layer, pad


def _transform_paste(frame, layer, pad: int, spec: ElementSpec, scale: float, angle: float,
                     tx: float, ty: float, opacity: float) -> None:
    """把元素图层按 CSS 变换链贴到帧画布上。

    变换链（与 MGRenderer 输出一致）：
    `translateX(-50%) translateY(-50%) scale rotate translateY(ty) translateX(tx)`
    —— CSS 左→右是矩阵乘法顺序，作用于点是从右往左。百分比位移按元素自身盒尺寸折算。
    """
    from PIL import Image

    if opacity <= 0.0:
        return
    w, h = spec.width, spec.height
    x_pct = (-w / 2.0) if spec.payload.get("x_anchor") == "center" else 0.0
    y_pct = (-h / 2.0) if spec.payload.get("y_anchor") == "middle" else 0.0
    L, T = spec.left, spec.top
    # 元素盒的 transform-origin 在盒坐标系中的位置（**不含**阴影 padding）
    oxb = spec.origin[0] * w
    oyb = spec.origin[1] * h
    # 图层坐标系里的同一原点（图层比盒四周各大 pad）
    ox = pad + oxb
    oy = pad + oyb
    cx0 = int(round(L + x_pct - pad))
    cy0 = int(round(T + y_pct - pad))
    # 恒等变换（无缩放/旋转/位移）走直贴：避免 1920×1080 元素每帧重采样
    if scale == 1.0 and angle == 0.0 and tx == 0.0 and ty == 0.0:
        paste = _apply_opacity(layer, opacity) if opacity < 1.0 else layer
        frame.alpha_composite(paste, (cx0, cy0))
        return
    # 纯缩放（无旋转）：resize + 整数贴图。PIL 的 resize 比通用仿射快约 3 倍
    # （实测 540×540×50 次：仿射 BICUBIC 19.2ms vs resize BICUBIC 6.2ms），
    # 代价是亚像素定位退化为整数像素（≤0.5px），已在文档里标注。
    if angle == 0.0:
        from PIL import Image as _Image

        ow = max(1, int(round(layer.width * scale)))
        oh = max(1, int(round(layer.height * scale)))
        x0 = L + oxb + x_pct + scale * (tx - ox)
        y0 = T + oyb + y_pct + scale * (ty - oy)
        scaled = layer if scale == 1.0 else layer.resize((ow, oh), _Image.BICUBIC)
        if opacity < 1.0:
            scaled = _apply_opacity(scaled, opacity)
        frame.alpha_composite(scaled, (int(round(x0)), int(round(y0))))
        return
    # 点 u（图层坐标）→ 画布坐标：先绕 origin 做 scale·rotate，
    # 再叠加百分比位移与关键帧位移，最后放到 (L,T)
    rad = math.radians(angle)
    cos_a, sin_a = math.cos(rad), math.sin(rad)

    def fwd(ux: float, uy: float) -> tuple[float, float]:
        # 关键帧位移（先 translateX 后 translateY，见变换链从右往左）
        px = ux - ox + tx
        py = uy - oy + ty
        # rotate（CSS 正角 = 顺时针；屏幕 y 向下）
        rx = px * cos_a - py * sin_a
        ry = px * sin_a + py * cos_a
        # scale
        sx, sy = rx * scale, ry * scale
        # 绕原点还原 + 百分比位移 + 盒左上角
        return (L + oxb + x_pct + sx, T + oyb + y_pct + sy)

    corners = [fwd(0, 0), fwd(layer.width, 0), fwd(layer.width, layer.height), fwd(0, layer.height)]
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    bx0, bx1 = math.floor(min(xs)), math.ceil(max(xs))
    by0, by1 = math.floor(min(ys)), math.ceil(max(ys))
    # 与画布求交（画布外的部分不必生成）
    cx0, cy0 = max(0, bx0), max(0, by0)
    cx1, cy1 = min(frame.width, bx1), min(frame.height, by1)
    if cx1 <= cx0 or cy1 <= cy0:
        return
    out_w, out_h = cx1 - cx0, cy1 - cy0

    # 反向映射：画布点 → 图层采样点
    a = cos_a / scale
    b = sin_a / scale
    c = -sin_a / scale
    d = cos_a / scale

    def inv(px: float, py: float) -> tuple[float, float]:
        dx = px - L - oxb - x_pct - tx
        dy = py - T - oyb - y_pct - ty
        return (ox + a * dx + b * dy, oy + c * dx + d * dy)

    # 输出窗口左上角 (cx0, cy0) 的仿射系数
    ux0, uy0 = inv(cx0, cy0)
    affine = (a, b, ux0, c, d, uy0)
    warped = layer.transform((out_w, out_h), Image.AFFINE, affine, resample=Image.BICUBIC)
    if opacity < 1.0:
        alpha = warped.getchannel("A").point(lambda v: int(v * opacity))
        warped.putalpha(alpha)
    frame.alpha_composite(warped, (cx0, cy0))


def _element_layer(spec: ElementSpec, cache: dict | None):
    """取（或构建）元素图层。尺寸不变的元素跨帧复用——这是快路径能"快"的关键：

    元素图层只与"盒尺寸"有关（opacity/scale/rotate/translate 都在贴图时施加），
    因此 90 帧里同一元素的图层**只画一次**，除非它的尺寸关键帧在变。
    """
    key = ("layer", spec.index, round(spec.width), round(spec.height))
    if cache is not None and key in cache:
        return cache[key]
    built = _paint_element(spec, cache)
    if cache is not None:
        cache[key] = built
    return built


def _background_layer(spec: ElementSpec, frame_size: tuple[int, int], cache: dict | None):
    """全幅背景层（渐变/纯色），按尺寸缓存。"""
    from PIL import Image

    key = ("bg", spec.index, frame_size, id(spec.gradient), id(spec.radial))
    if cache is not None and key in cache:
        return cache[key]
    full = Image.new("RGBA", frame_size, (0, 0, 0, 0))
    if spec.gradient is not None:
        full.alpha_composite(_ramp_image(spec.gradient, *frame_size, cache=cache))
    elif spec.radial is not None:
        full.alpha_composite(_radial_image(spec.radial, *frame_size, cache=cache))
    elif spec.fill is not None:
        full = Image.new("RGBA", frame_size, spec.fill)
    if cache is not None:
        cache[key] = full
    return full


def _apply_opacity(img, opacity: float):
    if opacity >= 1.0:
        return img
    out = img.copy()
    out.putalpha(img.getchannel("A").point(lambda v: int(v * opacity)))
    return out


def composite_frame(specs: list[ElementSpec], frame_size: tuple[int, int], t: float, dur: float,
                    cache: dict | None = None):
    """合成单帧（返回 RGBA 图）。`specs` 已 layout；`cache` 跨帧复用图层。"""
    from PIL import Image

    frame = Image.new("RGBA", frame_size, (0, 0, 0, 0))
    # z 序：bg 在最底，其余按文档顺序
    ordered = sorted(specs, key=lambda s: (0 if s.kind == "bg" else 1, s.index))
    for spec in ordered:
        under = spec.payload.get("underlying", {})
        opacity = track_value(spec.tracks.get("opacity", []), t, spec.easings, dur,
                              under.get("opacity", 1.0))
        if opacity <= 0.002:
            continue
        if spec.kind == "bg":
            full = _background_layer(spec, frame_size, cache)
            frame.alpha_composite(_apply_opacity(full, opacity))
            continue
        scale = track_value(spec.tracks.get("scale", []), t, spec.easings, dur, under.get("scale", 1.0))
        angle = track_value(spec.tracks.get("rotate", []), t, spec.easings, dur, under.get("rotate", 0.0))
        tx = track_value(spec.tracks.get("translate_x", []), t, spec.easings, dur, under.get("translate_x", 0.0))
        ty = track_value(spec.tracks.get("translate_y", []), t, spec.easings, dur, under.get("translate_y", 0.0))
        if "width" in spec.tracks:
            spec.width = max(0.0, track_value(spec.tracks["width"], t, spec.easings, dur,
                                              under.get("width", spec.width)))
        if "height" in spec.tracks:
            spec.height = max(0.0, track_value(spec.tracks["height"], t, spec.easings, dur,
                                               under.get("height", spec.height)))
        if spec.width <= 0.01 or spec.height <= 0.01 or scale <= 0.0:
            continue
        layer, pad = _element_layer(spec, cache)
        _transform_paste(frame, layer, pad, spec, scale, angle, tx, ty, opacity)
    return frame


# ── 编码 ─────────────────────────────────────────────────


def encoder_args(codec: str = "") -> list[str]:
    """带 alpha 的 MOV 编码参数（CDP 渲染器与快路径共用）。"""
    c = (codec or "qtrle").strip().lower()
    if c in ("prores", "prores4444", "prores_ks"):
        return ["-c:v", "prores_ks", "-profile:v", "4", "-pix_fmt", "yuva444p10le"]
    if c in ("ffv1",):
        return ["-c:v", "ffv1", "-pix_fmt", "yuva444p"]
    return ["-c:v", "qtrle", "-pix_fmt", "argb"]


def _encoder_args(codec: str) -> list[str]:
    """向后兼容别名。"""
    return encoder_args(codec)


def render_frames(
    mg_def: dict,
    frames_dir: str | Path,
    frame_size: tuple[int, int],
    fps: float,
    *,
    params: dict | None = None,
    variables: dict | None = None,
    save_png: bool = True,
) -> tuple[list[Path], float]:
    """逐帧渲染 PNG（`save_png=False` 时只合成不落盘，交给 `encode_raw` 走管道）。"""
    specs = _build_specs(mg_def, {**(params or {}), **(variables or {})})
    specs = layout_specs(specs, *frame_size)
    dur = float(mg_def.get("duration_sec") or 0)
    n = max(1, int(round(dur * fps)))
    out = Path(frames_dir)
    out.mkdir(parents=True, exist_ok=True)
    cache: dict = {}
    paths: list[Path] = []
    for i in range(n):
        t = i / float(fps)
        frame = composite_frame(specs, frame_size, t, dur, cache=cache)
        if save_png:
            path = out / f"f{i:05d}.png"
            frame.save(path)
            paths.append(path)
    return paths, dur


def encode_raw(
    mg_def: dict,
    out_mov: str | Path,
    frame_size: tuple[int, int],
    fps: float,
    frame_count: int,
    *,
    params: dict | None = None,
    variables: dict | None = None,
    codec: str = "",
    timeout: float = 900.0,
) -> bool:
    """直接把 RGBA 原始帧喂给 ffmpeg（跳过 PNG 落盘/编码，省一次全画幅压缩）。"""
    if frame_count <= 0:
        return False
    w, h = int(frame_size[0]), int(frame_size[1])
    out_mov = Path(out_mov)
    out_mov.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{w}x{h}",
        "-framerate", f"{float(fps):g}",
        "-i", "-", *_encoder_args(codec), str(out_mov),
    ]
    cache: dict = {}
    stderr_chunks: list[bytes] = []
    try:
        specs = layout_specs(_build_specs(mg_def, {**(params or {}), **(variables or {})}), w, h)
        dur = float(mg_def.get("duration_sec") or 0)
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE)
        for i in range(frame_count):
            frame = composite_frame(specs, (w, h), i / float(fps), dur, cache=cache)
            proc.stdin.write(frame.tobytes())
        proc.stdin.close()
        rc = proc.wait(timeout=timeout)
        err = (proc.stderr.read() if proc.stderr else b"").decode("utf-8", errors="replace")
    except Exception as exc:
        logger.warning("MG 快路径管道编码失败: %s", exc)
        return False
    if rc != 0:
        logger.warning("MG 快路径管道编码失败 rc=%s: %s", rc, err[-300:])
        return False
    return out_mov.exists() and out_mov.stat().st_size > 0


def encode_frames(
    frames: list[Path], out_mov: str | Path, fps: float, *, codec: str = "",
    timeout: float = 600.0,
) -> bool:
    """PNG 序列 → 带 alpha 的 MOV（qtrle 默认；`prores` 可换）。"""
    if not frames:
        return False
    out_mov = Path(out_mov)
    out_mov.parent.mkdir(parents=True, exist_ok=True)
    pattern = str(frames[0].parent / "f%05d.png")
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-framerate", f"{float(fps):g}", "-start_number", "0",
        "-i", pattern, *_encoder_args(codec), str(out_mov),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception as exc:
        logger.warning("MG 快路径编码失败: %s", exc)
        return False
    if r.returncode != 0:
        logger.warning("MG 快路径编码失败 rc=%s: %s", r.returncode, (r.stderr or "")[-300:])
        return False
    return out_mov.exists() and out_mov.stat().st_size > 0


def render_mg_fastpath(
    mg_def: dict,
    out_mov: str | Path,
    frame_size: tuple[int, int],
    fps: float,
    *,
    params: dict | None = None,
    variables: dict | None = None,
    work_dir: str | Path | None = None,
    codec: str = "",
) -> str | None:
    """`mg_def` → 带 alpha 的 MOV（不经 Chrome）。失败返回 None（调用方回退 Chrome）。

    绝不抛异常：快路径是**优化**，任何失败都必须无声地让位给 Chrome 路径。
    """
    try:
        ok, reason = fastpath_eligible(mg_def, params)
        if not ok:
            logger.info("MG 快路径不适用: %s", reason)
            return None
        dur = float(mg_def.get("duration_sec") or 0)
        n = max(1, int(round(dur * fps)))
        if not encode_raw(mg_def, out_mov, frame_size, fps, n,
                          params=params, variables=variables, codec=codec):
            return None
        return str(out_mov)
    except FastPathIneligible as exc:
        logger.info("MG 快路径不适用: %s", exc)
        return None
    except Exception as exc:
        logger.warning("MG 快路径失败，回退 Chrome: %s", exc)
        return None


def coverage_report(mg_defs: Iterable[tuple[str, dict]], params: dict | None = None) -> dict:
    """批量统计快路径覆盖率（哪些模板能走快路径、不能的原因）。

    这是判断"V3 到底省了多少"的关键度量：覆盖率低说明白名单太窄，
    覆盖率虚高说明白名单太松（要去人眼比对）。
    """
    usable, rejected = [], []
    total = 0
    for name, mg_def in mg_defs:
        total += 1
        ok, reason = fastpath_eligible(mg_def, params)
        (usable if ok else rejected).append(name if ok else {"name": name, "reason": reason})
    return {
        "total": total,
        "usable": len(usable),
        "usable_names": usable,
        "rejected": rejected,
        "ratio": (len(usable) / total) if total else 0.0,
    }


def load_template_defs(templates_dir: str | Path | None = None) -> list[tuple[str, dict]]:
    """加载内置 MG 模板定义（覆盖率统计用）。"""
    base = Path(templates_dir) if templates_dir else (
        Path(__file__).resolve().parent / "mg" / "templates"
    )
    out: list[tuple[str, dict]] = []
    if not base.is_dir():
        return out
    for path in sorted(base.glob("*.json")):
        try:
            out.append((path.stem, json.loads(path.read_text(encoding="utf-8"))))
        except Exception as exc:
            logger.warning("模板加载失败 %s: %s", path.name, exc)
    return out
