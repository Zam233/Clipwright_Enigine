"""插件专属图解：思维导图（mindmap）。

轮 88（去重）：本文件原有的 radar/gantt/venn3/heatmap/sankey/concept/
codeblock/datatable/quote/compcard/orgchart 12 个渲染函数是从核心
``clipwright/animation/diagram_svg.py`` 复制的陈旧副本，且经
DIAGRAM_RENDERER_EXTEND 覆盖核心实现（插件副本后注册、优先命中），
导致行为分叉（如 sankey 非法值处理不一致）。已全部删除，
这些类型统一由核心 DiagramRenderer 渲染；本插件仅保留核心没有的
mindmap。
"""

from __future__ import annotations

import math as _m

from clipwright.animation.diagram_svg import DiagramStyle
from plugins.logic_animations.utils.svg_helpers import (
    svg_frame, html_esc, staggered, drop_shadow,
)


def mindmap(items: list[str], title: str, s: DiagramStyle, w: int, h: int) -> str:
    """中心节点 → 多级辐射分支。items[0] 中心词，其余为子节点。"""
    cx, cy = w // 2, h // 2
    children = items[1:] if items else []
    n = len(children)
    delays = staggered(n + 1, 0, s.stagger_delay)
    parts = [svg_frame(w, h), "<defs>" + drop_shadow("ds_mm") + "</defs>"]

    if title:
        parts.append(f'<text x="{cx}" y="40" font-size="{s.title_font_size}" fill="{s.text_color}" text-anchor="middle" font-weight="bold">{html_esc(title[:60])}</text>')

    # 中心节点
    parts.append(
        f'<g style="animation-delay:{delays[0]}s">'
        f'<circle cx="{cx}" cy="{cy}" r="50" fill="{s.primary_color}44" stroke="{s.primary_color}" stroke-width="3" filter="url(#ds_mm)"/>'
        f'<text x="{cx}" y="{cy + 5}" font-size="18" fill="{s.text_color}" text-anchor="middle" font-weight="bold">{html_esc(items[0][:12])}</text></g>'
    )

    # 辐射分支
    angle_step = 2 * _m.pi / max(n, 1)
    radius = 220
    for i, child in enumerate(children[:12]):
        angle = angle_step * i - _m.pi / 2
        ex = cx + radius * _m.cos(angle)
        ey = cy + radius * _m.sin(angle)
        # 连接线
        parts.append(
            f'<g style="animation-delay:{delays[min(i + 1, len(delays) - 1)]}s">'
            f'<line x1="{cx + 50 * _m.cos(angle)}" y1="{cy + 50 * _m.sin(angle)}" x2="{ex}" y2="{ey}" stroke="{s.primary_color}44" stroke-width="1.5"/>'
            f'<rect x="{ex - 55}" y="{ey - 18}" width="110" height="36" rx="8" fill="{s.item_bg}" stroke="{s.primary_color}44" filter="url(#ds_mm)"/>'
            f'<text x="{ex}" y="{ey + 5}" font-size="14" fill="{s.text_color}" text-anchor="middle">{html_esc(child[:12])}</text></g>'
        )

    parts.append("</svg>")
    return "\n".join(parts)


# ── 迁入自 engine 的共用助手与 11 个图解渲染器（B8-T8.2 第二步）──

def bar_chart(items: list[str], title: str, s: DiagramStyle,
               w: int, h: int) -> str:
    """垂直柱状图，每项格式：标签或标签:数值。"""
    n = min(len(items), 8)
    if n == 0:
        return ""
    cx, cy = w // 2, h // 2 + 60
    chart_w = min(800, w - 100)
    bar_w = chart_w // n - 20
    sx = cx - chart_w // 2
    chart_h = 300
    delays = staggered(range(n), 0, s.stagger_delay)

    # 解析数值（标签:数值 或纯标签）
    parsed = []
    for item in items[:8]:
        if ":" in item:
            parts_s = item.split(":", 1)
            parsed.append((parts_s[0], float(parts_s[1])))
        else:
            parsed.append((item, 50 + hash(item) % 50))
    max_val = max(v for _, v in parsed) or 1

    parts_svg = [svg_frame(w, h)]

    if title:
        parts_svg.append(
            f'<text x="{cx}" y="{cy - chart_h - 40}" font-size="{s.title_font_size}"'
            f' fill="{s.text_color}" text-anchor="middle" font-weight="bold">'
            f'{html_esc(title[:60])}</text>'
        )

    # 轴线
    parts_svg.append(
        f'<line x1="{sx - 10}" y1="{cy}" x2="{sx + chart_w + 10}" y2="{cy}"'
        f' stroke="{s.text_color}44" stroke-width="1"/>'
    )

    colors = [s.primary_color, s.secondary_color, s.accent_color,
              "#34d399", "#a855f7", "#f97316", "#06b6d4", "#ec4899"]

    for i, (label, val) in enumerate(parsed):
        x = sx + i * (chart_w // n)
        bar_h = (val / max_val) * chart_h
        color = colors[i % len(colors)]
        parts_svg.append(
            f'<g style="animation-delay:{delays[i]}s">'
            f'<rect x="{x + 10}" y="{cy - bar_h}" width="{bar_w}" height="{bar_h}"'
            f' rx="4" fill="{color}88" stroke="{color}" stroke-width="1"/>'
            f'<text x="{x + 10 + bar_w // 2}" y="{cy - bar_h - 8}" font-size="16"'
            f' fill="{s.text_color}" text-anchor="middle">{val:.0f}</text>'
            f'<text x="{x + 10 + bar_w // 2}" y="{cy + 20}" font-size="14"'
            f' fill="{s.text_color}" text-anchor="middle" transform="rotate(-15,{x + 10 + bar_w // 2},{cy + 20})">'
            f'{html_esc(label[:8])}</text></g>'
        )

    parts_svg.append('</svg>')
    return "\n".join(parts_svg)


# ── 雷达图 ──────────────────────────────────────────

def radar(items: list[str], title: str, s: DiagramStyle,
           w: int, h: int) -> str:
    """雷达图：多维指标对比。每项格式：标签:数值（>=3 项）。"""
    import math
    if not items:
        return ""
    parsed: list[tuple[str, float]] = []
    for item in items[:8]:
        ps = item.split(":", 1)
        if len(ps) == 2:
            try:
                parsed.append((ps[0], float(ps[1])))
            except ValueError:
                parsed.append((ps[0], 50.0))
        else:
            parsed.append((item, 50.0))
    n = len(parsed)
    if n < 3:
        return bar_chart(items, title, s, w, h)
    cx, cy = w // 2, h // 2 + 20
    r_max = min(w, h) // 4
    max_val = max(v for _, v in parsed) or 1
    delays = staggered(range(n), 0, s.stagger_delay)

    def pt(i: int, rr: float) -> tuple[float, float]:
        ang = -math.pi / 2 + 2 * math.pi * i / n
        return cx + rr * math.cos(ang), cy + rr * math.sin(ang)

    parts = [svg_frame(w, h)]
    if title:
        parts.append(
            f'<text x="{cx}" y="{cy - r_max - 60}" font-size="{s.title_font_size}"'
            f' fill="{s.text_color}" text-anchor="middle" font-weight="bold">'
            f'{html_esc(title[:60])}</text>')

    # 同心多边形环
    for ring in (0.33, 0.66, 1.0):
        pts = " ".join(f"{pt(i, r_max * ring)[0]:.1f},{pt(i, r_max * ring)[1]:.1f}"
                       for i in range(n))
        parts.append(
            f'<polygon points="{pts}" fill="none" stroke="{s.text_color}22" stroke-width="1"/>')

    # 值多边形
    vpts = " ".join(
        f"{pt(i, r_max * (v / max_val))[0]:.1f},"
        f"{pt(i, r_max * (v / max_val))[1]:.1f}"
        for i, (_, v) in enumerate(parsed)
    )
    parts.append(
        f'<polygon points="{vpts}" fill="{s.primary_color}44"'
        f' stroke="{s.primary_color}" stroke-width="2"'
        f' style="animation-delay:{delays[-1]}s"/>')

    # 轴线 + 值点 + 标签（逐元素入场）
    for i, (label, val) in enumerate(parsed):
        x2, y2 = pt(i, r_max)
        lx, ly = pt(i, r_max + 44)
        vx, vy = pt(i, r_max * (val / max_val))
        parts.append(
            f'<g style="animation-delay:{delays[i]}s">'
            f'<line x1="{cx}" y1="{cy}" x2="{x2:.1f}" y2="{y2:.1f}"'
            f' stroke="{s.text_color}33" stroke-width="1"/>'
            f'<circle cx="{vx:.1f}" cy="{vy:.1f}" r="6" fill="{s.accent_color}"'
            f' stroke="#fff" stroke-width="1.5"/>'
            f'<text x="{lx:.1f}" y="{ly:.1f}" font-size="{s.font_size}"'
            f' fill="{s.text_color}" text-anchor="middle">'
            f'{html_esc(label[:10])}</text></g>')

    parts.append('</svg>')
    return "\n".join(parts)

# ── 甘特图 ──────────────────────────────────────────

def gantt(items: list[str], title: str, s: DiagramStyle,
           w: int, h: int) -> str:
    """甘特图：任务排期。每项格式：任务名:开始:持续。"""
    if not items:
        return ""
    parsed: list[tuple[str, float, float]] = []
    for item in items[:10]:
        segs = item.split(":")
        name = segs[0]
        try:
            start = float(segs[1]) if len(segs) > 1 else 0.0
        except ValueError:
            start = 0.0
        try:
            dur = float(segs[2]) if len(segs) > 2 else 1.0
        except ValueError:
            dur = 1.0
        parsed.append((name, start, max(dur, 0.1)))
    n = len(parsed)
    t_end = max(st + du for _, st, du in parsed) or 1
    label_w = 220
    chart_x = 100 + label_w
    chart_w = w - chart_x - 100
    row_h = min(64, (h - 320) // max(n, 1))
    top_y = h // 2 - (n * row_h) // 2
    delays = staggered(range(n), 0, s.stagger_delay)

    def tx(t: float) -> float:
        return chart_x + (t / t_end) * chart_w

    colors = [s.primary_color, s.secondary_color, s.accent_color,
              "#34d399", "#a855f7", "#f97316", "#06b6d4", "#ec4899"]

    parts = [svg_frame(w, h)]
    if title:
        parts.append(
            f'<text x="{w // 2}" y="{top_y - 70}" font-size="{s.title_font_size}"'
            f' fill="{s.text_color}" text-anchor="middle" font-weight="bold">'
            f'{html_esc(title[:60])}</text>')

    # 顶部时间轴（0 / 中点 / 终点 三个刻度）
    parts.append(
        f'<line x1="{chart_x}" y1="{top_y - 20}" x2="{chart_x + chart_w}" y2="{top_y - 20}"'
        f' stroke="{s.text_color}44" stroke-width="1"/>')
    for tick in (0.0, t_end / 2, t_end):
        parts.append(
            f'<text x="{tx(tick):.1f}" y="{top_y - 30}" font-size="14"'
            f' fill="{s.text_color}88" text-anchor="middle">{tick:.0f}</text>')

    # 任务行 + 条形
    for i, (name, start, dur) in enumerate(parsed):
        yy = top_y + i * row_h
        color = colors[i % len(colors)]
        parts.append(
            f'<g style="animation-delay:{delays[i]}s">'
            f'<text x="{chart_x - 16}" y="{yy + row_h // 2 + 6}" font-size="{s.font_size - 6}"'
            f' fill="{s.text_color}" text-anchor="end">'
            f'{html_esc(name[:12])}</text>'
            f'<rect x="{tx(start):.1f}" y="{yy + 6}" width="{tx(start + dur) - tx(start):.1f}"'
            f' height="{row_h - 12}" rx="{s.border_radius // 2}"'
            f' fill="{color}88" stroke="{color}" stroke-width="1"/>'
            f'</g>')

    parts.append('</svg>')
    return "\n".join(parts)

# ── 三维恩图（三集合） ──────────────────────────────

def venn3(items: list[str], title: str, s: DiagramStyle,
           w: int, h: int) -> str:
    """三集合维恩图。items 至多 7 项：A,B,C,AB,AC,BC,ABC。"""
    if not items:
        return ""
    cx, cy = w // 2, h // 2 + 20
    r = 150
    centers = [(cx, cy - 70), (cx - 100, cy + 60), (cx + 100, cy + 60)]
    colors = [s.primary_color, s.secondary_color, s.accent_color]
    # 各区域标签位置（相对 cx, cy）
    label_pos = [(0, -200), (-200, 130), (200, 130),
                 (-85, -30), (85, -30), (0, 120), (0, 10)]
    delays = staggered(range(7), 0, s.stagger_delay)

    parts = [svg_frame(w, h)]
    if title:
        parts.append(
            f'<text x="{cx}" y="{cy - r - 110}" font-size="{s.title_font_size}"'
            f' fill="{s.text_color}" text-anchor="middle" font-weight="bold">'
            f'{html_esc(title[:60])}</text>')

    # 三个圆（三角排布）
    for i, (ccx, ccy) in enumerate(centers):
        parts.append(
            f'<circle cx="{ccx}" cy="{ccy}" r="{r}" fill="{colors[i]}22"'
            f' stroke="{colors[i]}" stroke-width="2"'
            f' style="animation-delay:{delays[i]}s"/>')

    # 区域标签
    for i, item in enumerate(items[:7]):
        dx, dy = label_pos[i]
        parts.append(
            f'<text x="{cx + dx}" y="{cy + dy}" font-size="{s.font_size}"'
            f' fill="{s.text_color}" text-anchor="middle"'
            f' style="animation-delay:{delays[min(i + 3, 6)]}s">'
            f'{html_esc(item[:12])}</text>')

    parts.append('</svg>')
    return "\n".join(parts)

# ── 热力图 ──────────────────────────────────────────

def heatmap(items: list[str], title: str, s: DiagramStyle,
             w: int, h: int) -> str:
    """热力图。首项为列头（逗号分隔），后续项：行名:v1,v2,...。"""
    if not items:
        return ""
    headers = [c.strip() for c in items[0].split(",") if c.strip()]
    rows: list[tuple[str, list[float]]] = []
    for item in items[1:8]:
        ps = item.split(":", 1)
        if len(ps) != 2:
            continue
        vals: list[float] = []
        for v in ps[1].split(","):
            try:
                vals.append(float(v))
            except ValueError:
                vals.append(0.0)
        rows.append((ps[0], vals))
    if not headers or not rows:
        return ""
    ncol = len(headers)
    all_vals = [v for _, vs in rows for v in vs[:ncol]]
    v_min = min(all_vals) if all_vals else 0.0
    v_max = max(all_vals) if all_vals else 1.0
    v_rng = (v_max - v_min) or 1

    def heat_color(t: float) -> str:
        # 蓝 → 红 渐变
        rr = int(79 + (255 - 79) * t)
        gg = int(140 + (107 - 140) * t)
        bb = int(255 + (107 - 255) * t)
        return f"#{rr:02x}{gg:02x}{bb:02x}"

    label_w = 160
    cell_w = min(160, (w - label_w - 240) // max(ncol, 1))
    cell_h = min(72, (h - 360) // max(len(rows), 1))
    grid_w = cell_w * ncol
    sx = (w - grid_w) // 2 + label_w // 2
    sy = h // 2 - (cell_h * len(rows)) // 2
    delays = staggered(range(len(rows) * ncol), 0, s.stagger_delay * 0.5)

    parts = [svg_frame(w, h)]
    if title:
        parts.append(
            f'<text x="{w // 2}" y="{sy - 80}" font-size="{s.title_font_size}"'
            f' fill="{s.text_color}" text-anchor="middle" font-weight="bold">'
            f'{html_esc(title[:60])}</text>')

    # 列头
    for j, hd in enumerate(headers):
        parts.append(
            f'<text x="{sx + j * cell_w + cell_w // 2}" y="{sy - 14}"'
            f' font-size="{s.font_size - 8}"'
            f' fill="{s.text_color}" text-anchor="middle">'
            f'{html_esc(hd[:10])}</text>')

    # 行标签 + 色块
    for i, (rname, vals) in enumerate(rows):
        yy = sy + i * cell_h
        parts.append(
            f'<text x="{sx - 16}" y="{yy + cell_h // 2 + 6}" font-size="{s.font_size - 8}"'
            f' fill="{s.text_color}" text-anchor="end">'
            f'{html_esc(rname[:10])}</text>')
        for j in range(ncol):
            val = vals[j] if j < len(vals) else 0.0
            t = (val - v_min) / v_rng
            color = heat_color(t)
            d = delays[min(i * ncol + j, len(delays) - 1)]
            parts.append(
                f'<g style="animation-delay:{d}s">'
                f'<rect x="{sx + j * cell_w + 2}" y="{yy + 2}" width="{cell_w - 4}"'
                f' height="{cell_h - 4}" rx="6" fill="{color}bb" stroke="{color}"/>'
                f'<text x="{sx + j * cell_w + cell_w // 2}" y="{yy + cell_h // 2 + 5}"'
                f' font-size="14" fill="#fff" text-anchor="middle">{val:.0f}</text></g>')

    parts.append('</svg>')
    return "\n".join(parts)

# ── 桑基图 ──────────────────────────────────────────

def sankey(items: list[str], title: str, s: DiagramStyle,
            w: int, h: int) -> str:
    """桑基图：流向图。每项格式：源:目标:值。"""
    if not items:
        return ""
    flows: list[tuple[str, str, float]] = []
    for item in items[:10]:
        segs = item.split(":")
        if len(segs) < 3:
            continue
        try:
            val = float(segs[2])
        except ValueError:
            continue
        flows.append((segs[0], segs[1], max(val, 0.1)))
    if not flows:
        return ""
    total = sum(v for _, _, v in flows) or 1
    # 节点顺序（按出现次序去重）
    sources: list[str] = []
    targets: list[str] = []
    for fr, to, _ in flows:
        if fr not in sources:
            sources.append(fr)
        if to not in targets:
            targets.append(to)
    src_total = {n: sum(v for f, _, v in flows if f == n) for n in sources}
    tgt_total = {n: sum(v for _, t, v in flows if t == n) for n in targets}

    top, avail_h = 260, h - 460
    scale = avail_h / total
    node_w = 24
    x_src = w // 2 - 380
    x_tgt = w // 2 + 380 - node_w
    # 节点纵向堆叠起点
    src_pos: dict[str, float] = {}
    tgt_pos: dict[str, float] = {}
    cur = float(top)
    for n_ in sources:
        src_pos[n_] = cur
        cur += src_total[n_] * scale + 20
    cur = float(top)
    for n_ in targets:
        tgt_pos[n_] = cur
        cur += tgt_total[n_] * scale + 20
    src_cur = dict(src_pos)
    tgt_cur = dict(tgt_pos)
    delays = staggered(range(len(flows) + 4), 0, s.stagger_delay)
    colors = [s.primary_color, s.secondary_color, s.accent_color,
              "#34d399", "#a855f7", "#f97316", "#06b6d4", "#ec4899"]

    parts = [svg_frame(w, h)]
    if title:
        parts.append(
            f'<text x="{w // 2}" y="{top - 60}" font-size="{s.title_font_size}"'
            f' fill="{s.text_color}" text-anchor="middle" font-weight="bold">'
            f'{html_esc(title[:60])}</text>')

    # 流带（矩形近似，厚度 ∝ 值）
    for i, (fr, to, val) in enumerate(flows):
        t = val * scale
        y1, y2 = src_cur[fr], tgt_cur[to]
        src_cur[fr] += t
        tgt_cur[to] += t
        color = colors[i % len(colors)]
        parts.append(
            f'<g style="animation-delay:{delays[i]}s">'
            f'<polygon points="{x_src + node_w},{y1:.1f} {x_tgt},{y2:.1f}'
            f' {x_tgt},{y2 + t:.1f} {x_src + node_w},{y1 + t:.1f}"'
            f' fill="{color}55" stroke="{color}88" stroke-width="1"/></g>')

    # 节点 + 标签
    for i, n_ in enumerate(sources):
        nh = src_total[n_] * scale
        d = delays[min(len(flows) + i, len(delays) - 1)]
        parts.append(
            f'<g style="animation-delay:{d}s">'
            f'<rect x="{x_src}" y="{src_pos[n_]:.1f}" width="{node_w}" height="{nh:.1f}"'
            f' rx="4" fill="{s.primary_color}"/>'
            f'<text x="{x_src - 12}" y="{src_pos[n_] + nh / 2 + 5:.1f}"'
            f' font-size="{s.font_size - 6}"'
            f' fill="{s.text_color}" text-anchor="end">'
            f'{html_esc(n_[:10])}</text></g>')
    for i, n_ in enumerate(targets):
        nh = tgt_total[n_] * scale
        d = delays[min(len(flows) + len(sources) + i, len(delays) - 1)]
        parts.append(
            f'<g style="animation-delay:{d}s">'
            f'<rect x="{x_tgt}" y="{tgt_pos[n_]:.1f}" width="{node_w}" height="{nh:.1f}"'
            f' rx="4" fill="{s.secondary_color}"/>'
            f'<text x="{x_tgt + node_w + 12}" y="{tgt_pos[n_] + nh / 2 + 5:.1f}"'
            f' font-size="{s.font_size - 6}" fill="{s.text_color}">'
            f'{html_esc(n_[:10])}</text></g>')

    parts.append('</svg>')
    return "\n".join(parts)

# ── 概念图 ──────────────────────────────────────────

def concept(items: list[str], title: str, s: DiagramStyle,
             w: int, h: int) -> str:
    """概念图：节点 id:x:y:标签（相对坐标缩放至画布）+ 边 from->to:标签。"""
    if not items:
        return ""
    nodes: list[dict[str, Any]] = []
    edges: list[tuple[str, str, str]] = []
    for item in items[:12]:
        if "->" in item:
            route, _, lbl = item.partition(":")
            fr, _, to = route.partition("->")
            edges.append((fr.strip(), to.strip(), lbl.strip()))
            continue
        segs = item.split(":")
        nid = segs[0].strip()
        if not nid:
            continue
        try:
            rx = float(segs[1]) if len(segs) > 1 else 400.0
        except ValueError:
            rx = 400.0
        try:
            ry = float(segs[2]) if len(segs) > 2 else 225.0
        except ValueError:
            ry = 225.0
        label = segs[3] if len(segs) > 3 else nid
        # 相对坐标（800x450 参考系）缩放进画布（留边距）
        nodes.append({
            "id": nid,
            "x": 140 + (rx / 800.0) * (w - 280),
            "y": 120 + (ry / 450.0) * (h - 240),
            "label": label,
        })
    if not nodes:
        return ""
    delays = staggered(range(len(nodes) + len(edges)), 0, s.stagger_delay)

    parts = [svg_frame(w, h),
             '<defs>'
             f'<marker id="a_cp" viewBox="0 0 10 10" refX="10" refY="5"'
             f' markerWidth="8" markerHeight="8" orient="auto">'
             f'<path d="M0,0L10,5L0,10Z" fill="{s.arrow_color}88"/></marker>'
             f'<filter id="ds_cp"><feDropShadow dx="0" dy="2" stdDeviation="3"'
             f' flood-opacity="0.25"/></filter>'
             '</defs>']
    if title:
        parts.append(
            f'<text x="{w // 2}" y="60" font-size="{s.title_font_size}"'
            f' fill="{s.text_color}" text-anchor="middle" font-weight="bold">'
            f'{html_esc(title[:60])}</text>')

    # 边
    for ei, (fr, to, lbl) in enumerate(edges):
        fn = next((nd for nd in nodes if nd["id"] == fr), None)
        tn = next((nd for nd in nodes if nd["id"] == to), None)
        if not fn or not tn:
            continue
        d = delays[min(len(nodes) + ei, len(delays) - 1)]
        parts.append(
            f'<g style="animation-delay:{d}s">'
            f'<line x1="{fn["x"]:.1f}" y1="{fn["y"]:.1f}" x2="{tn["x"]:.1f}" y2="{tn["y"]:.1f}"'
            f' stroke="{s.arrow_color}66" stroke-width="2" marker-end="url(#a_cp)"/>'
            + (f'<text x="{(fn["x"] + tn["x"]) / 2:.1f}" y="{(fn["y"] + tn["y"]) / 2 - 10:.1f}"'
               f' font-size="14" fill="{s.text_color}88" text-anchor="middle">'
               f'{html_esc(lbl[:15])}</text>' if lbl else "")
            + '</g>')

    # 节点
    for ni, nd in enumerate(nodes):
        parts.append(
            f'<g style="animation-delay:{delays[min(ni, len(delays) - 1)]}s">'
            f'<rect x="{nd["x"] - 80:.1f}" y="{nd["y"] - 28:.1f}" width="160" height="56"'
            f' rx="{s.border_radius}" fill="{s.item_bg}" stroke="{s.primary_color}"'
            f' stroke-width="2" filter="url(#ds_cp)"/>'
            f'<text x="{nd["x"]:.1f}" y="{nd["y"] + 6:.1f}" font-size="{s.font_size - 4}"'
            f' fill="{s.text_color}" text-anchor="middle">'
            f'{html_esc(nd["label"][:12])}</text></g>')

    parts.append('</svg>')
    return "\n".join(parts)

# ── 代码块 ──────────────────────────────────────────

def codeblock(items: list[str], title: str, s: DiagramStyle,
               w: int, h: int) -> str:
    """代码块：暗色面板 + 等宽字体逐行入场。items 每行为一行代码。"""
    if len(items) == 1 and "|" in items[0]:
        items = items[0].split("|")
    lines = items[:20]
    if not lines:
        return ""
    line_h = 42
    panel_w = min(1100, w - 240)
    panel_h = 70 + len(lines) * line_h
    px, py = (w - panel_w) // 2, (h - panel_h) // 2
    delays = staggered(range(len(lines)), 0, s.stagger_delay * 0.6)

    parts = [svg_frame(w, h),
             '<defs><filter id="ds_cb"><feDropShadow dx="0" dy="4"'
             ' stdDeviation="8" flood-opacity="0.4"/></filter></defs>']
    if title:
        parts.append(
            f'<text x="{w // 2}" y="{py - 30}" font-size="{s.title_font_size}"'
            f' fill="{s.text_color}" text-anchor="middle" font-weight="bold">'
            f'{html_esc(title[:60])}</text>')

    # 面板
    parts.append(
        f'<rect x="{px}" y="{py}" width="{panel_w}" height="{panel_h}"'
        f' rx="{s.border_radius}" fill="#1e1e2e" stroke="{s.text_color}22"'
        f' filter="url(#ds_cb)"/>')
    # 顶部装饰圆点
    for i, c in enumerate(("#ff5f56", "#ffbd2e", "#27c93f")):
        parts.append(
            f'<circle cx="{px + 30 + i * 26}" cy="{py + 26}" r="7" fill="{c}"/>')

    # 代码行（行号 + 文本，逐行入场）
    for i, line in enumerate(lines):
        yy = py + 70 + i * line_h
        parts.append(
            f'<g style="animation-delay:{delays[i]}s">'
            f'<text x="{px + 30}" y="{yy}" font-size="16" fill="{s.text_color}44"'
            f' font-family="JetBrains Mono, monospace">{i + 1:>2}</text>'
            f'<text x="{px + 80}" y="{yy}" font-size="20" fill="{s.text_color}"'
            f' font-family="JetBrains Mono, monospace">'
            f'{html_esc(line[:70])}</text></g>')

    parts.append('</svg>')
    return "\n".join(parts)

# ── 数据表 ──────────────────────────────────────────

def datatable(items: list[str], title: str, s: DiagramStyle,
               w: int, h: int) -> str:
    """数据表：首行为表头（逗号分隔），后续行为数据行。"""
    if not items:
        return ""
    headers = [c.strip() for c in items[0].split(",")]
    rows = [[c.strip() for c in it.split(",")] for it in items[1:8]]
    ncol = max(len(headers), 1)
    row_h = 58
    cell_w = min(240, (w - 320) // ncol)
    table_w = cell_w * ncol
    table_h = row_h * (len(rows) + 1)
    sx, sy = (w - table_w) // 2, (h - table_h) // 2 + 20
    delays = staggered(range(len(rows) + 1), 0, s.stagger_delay)

    parts = [svg_frame(w, h),
             '<defs><filter id="ds_dt"><feDropShadow dx="0" dy="2"'
             ' stdDeviation="4" flood-opacity="0.25"/></filter></defs>']
    if title:
        parts.append(
            f'<text x="{w // 2}" y="{sy - 30}" font-size="{s.title_font_size}"'
            f' fill="{s.text_color}" text-anchor="middle" font-weight="bold">'
            f'{html_esc(title[:60])}</text>')

    # 表头行（主色高亮）
    parts.append(
        f'<g style="animation-delay:{delays[0]}s">'
        f'<rect x="{sx}" y="{sy}" width="{table_w}" height="{row_h}"'
        f' rx="{s.border_radius}" fill="{s.primary_color}aa" filter="url(#ds_dt)"/>')
    for j, hd in enumerate(headers):
        parts.append(
            f'<text x="{sx + j * cell_w + cell_w // 2}" y="{sy + row_h // 2 + 7}"'
            f' font-size="{s.font_size - 4}" fill="#fff" text-anchor="middle"'
            f' font-weight="bold">{html_esc(hd[:12])}</text>')
    parts.append('</g>')

    # 数据行（交替底色 + 细边框）
    for i, row in enumerate(rows):
        yy = sy + row_h * (i + 1)
        bg = s.item_bg if i % 2 == 0 else s.item_bg_alt
        parts.append(
            f'<g style="animation-delay:{delays[min(i + 1, len(delays) - 1)]}s">'
            f'<rect x="{sx}" y="{yy}" width="{table_w}" height="{row_h}"'
            f' fill="{bg}" stroke="{s.text_color}22" stroke-width="1"/>')
        for j in range(ncol):
            cell = row[j] if j < len(row) else ""
            parts.append(
                f'<text x="{sx + j * cell_w + cell_w // 2}" y="{yy + row_h // 2 + 6}"'
                f' font-size="{s.font_size - 6}" fill="{s.text_color}" text-anchor="middle">'
                f'{html_esc(cell[:14])}</text>')
        parts.append('</g>')

    # 竖向分隔线
    for j in range(1, ncol):
        xx = sx + j * cell_w
        parts.append(
            f'<line x1="{xx}" y1="{sy}" x2="{xx}" y2="{sy + table_h}"'
            f' stroke="{s.text_color}22" stroke-width="1"/>')

    parts.append('</svg>')
    return "\n".join(parts)

# ── 引用 ────────────────────────────────────────────

def quote(items: list[str], title: str, s: DiagramStyle,
           w: int, h: int) -> str:
    """引用卡：大引号 + 居中引文（~20 字/行换行）+ 底部署名。"""
    if not items:
        return ""
    quote = items[0]
    author = items[1] if len(items) > 1 else ""
    if not quote:
        return ""
    text = quote[:80]
    lines = [text[i:i + 20] for i in range(0, len(text), 20)]
    box_w = min(920, w - 240)
    box_h = 180 + len(lines) * 56 + (60 if author else 0)
    bx, by = (w - box_w) // 2, (h - box_h) // 2
    delays = staggered(range(len(lines) + 2), 0, s.stagger_delay)

    parts = [svg_frame(w, h),
             '<defs><filter id="ds_qt"><feDropShadow dx="0" dy="4"'
             ' stdDeviation="8" flood-opacity="0.3"/></filter></defs>']
    if title:
        parts.append(
            f'<text x="{w // 2}" y="{by - 30}" font-size="{s.title_font_size}"'
            f' fill="{s.text_color}" text-anchor="middle" font-weight="bold">'
            f'{html_esc(title[:60])}</text>')

    # 底板
    parts.append(
        f'<rect x="{bx}" y="{by}" width="{box_w}" height="{box_h}"'
        f' rx="{s.border_radius}" fill="{s.item_bg}" stroke="{s.primary_color}44"'
        f' filter="url(#ds_qt)"/>')
    # 装饰大引号
    parts.append(
        f'<text x="{bx + 50}" y="{by + 110}" font-size="120" fill="{s.accent_color}"'
        f' font-weight="bold" style="animation-delay:{delays[0]}s">“</text>')

    # 引文（逐行入场）
    for i, ln in enumerate(lines):
        yy = by + 130 + i * 56
        parts.append(
            f'<text x="{w // 2}" y="{yy}" font-size="{s.font_size + 4}"'
            f' fill="{s.text_color}" text-anchor="middle"'
            f' style="animation-delay:{delays[min(i + 1, len(delays) - 1)]}s">'
            f'{html_esc(ln)}</text>')

    # 署名
    if author:
        parts.append(
            f'<text x="{w // 2}" y="{by + box_h - 40}" font-size="{s.font_size}"'
            f' fill="{s.accent_color}" text-anchor="middle"'
            f' style="animation-delay:{delays[-1]}s">'
            f'—— {html_esc(author[:20])}</text>')

    parts.append('</svg>')
    return "\n".join(parts)

# ── 对比卡 ──────────────────────────────────────────

def compcard(items: list[str], title: str, s: DiagramStyle,
              w: int, h: int) -> str:
    """对比卡：双栏 A vs B。每项格式：特性,A值,B值,胜出方(1/2)。"""
    if not items:
        return ""
    rows: list[tuple[str, str, str, str]] = []
    for item in items[:10]:
        segs = [c.strip() for c in item.split(",")]
        if len(segs) < 3:
            continue
        win = segs[3] if len(segs) > 3 else "0"
        rows.append((segs[0], segs[1], segs[2], win))
    if not rows:
        return ""
    cx = w // 2
    col_w = 380
    gap = 220  # 中间特性列宽
    lx, rx = cx - gap // 2 - col_w, cx + gap // 2
    row_h = 64
    top_y = h // 2 - (len(rows) * row_h) // 2 + 30
    panel_h = len(rows) * row_h + 40
    delays = staggered(range(len(rows) + 2), 0, s.stagger_delay)

    parts = [svg_frame(w, h),
             '<defs><filter id="ds_cc"><feDropShadow dx="0" dy="3"'
             ' stdDeviation="6" flood-opacity="0.3"/></filter></defs>']
    if title:
        parts.append(
            f'<text x="{cx}" y="{top_y - 60}" font-size="{s.title_font_size}"'
            f' fill="{s.text_color}" text-anchor="middle" font-weight="bold">'
            f'{html_esc(title[:60])}</text>')

    # 左右栏面板
    parts.append(
        f'<g style="animation-delay:{delays[0]}s">'
        f'<rect x="{lx}" y="{top_y - 20}" width="{col_w}" height="{panel_h}"'
        f' rx="{s.border_radius}" fill="{s.primary_color}22" stroke="{s.primary_color}"'
        f' stroke-width="2" filter="url(#ds_cc)"/>'
        f'<text x="{lx + col_w // 2}" y="{top_y + 18}" font-size="{s.font_size}"'
        f' fill="{s.primary_color}" text-anchor="middle" font-weight="bold">A</text></g>')
    parts.append(
        f'<g style="animation-delay:{delays[1]}s">'
        f'<rect x="{rx}" y="{top_y - 20}" width="{col_w}" height="{panel_h}"'
        f' rx="{s.border_radius}" fill="{s.secondary_color}22" stroke="{s.secondary_color}"'
        f' stroke-width="2" filter="url(#ds_cc)"/>'
        f'<text x="{rx + col_w // 2}" y="{top_y + 18}" font-size="{s.font_size}"'
        f' fill="{s.secondary_color}" text-anchor="middle" font-weight="bold">B</text></g>')

    # 特性行 + 两侧值 + 胜者高亮
    for i, (feat, va, vb, win) in enumerate(rows):
        yy = top_y + 40 + i * row_h
        d = delays[min(i + 2, len(delays) - 1)]
        parts.append(
            f'<g style="animation-delay:{d}s">'
            f'<text x="{cx}" y="{yy + row_h // 2}" font-size="{s.font_size - 6}"'
            f' fill="{s.text_color}" text-anchor="middle">'
            f'{html_esc(feat[:10])}</text>')
        # A 值
        parts.append(
            f'<text x="{lx + col_w // 2}" y="{yy + row_h // 2}" font-size="{s.font_size - 4}"'
            f' fill="{s.text_color}" text-anchor="middle">'
            f'{html_esc(va[:12])}</text>')
        # B 值
        parts.append(
            f'<text x="{rx + col_w // 2}" y="{yy + row_h // 2}" font-size="{s.font_size - 4}"'
            f' fill="{s.text_color}" text-anchor="middle">'
            f'{html_esc(vb[:12])}</text>')
        # 胜者徽章（accent 边框 + 星标）
        if win in ("1", "2"):
            wx = lx if win == "1" else rx
            parts.append(
                f'<rect x="{wx + 14}" y="{yy + 4}" width="{col_w - 28}" height="{row_h - 12}"'
                f' rx="{s.border_radius // 2}" fill="none" stroke="{s.accent_color}"'
                f' stroke-width="2"/>'
                f'<text x="{wx + col_w - 34}" y="{yy + row_h // 2}" font-size="20"'
                f' fill="{s.accent_color}" text-anchor="middle">★</text>')
        parts.append('</g>')

    parts.append('</svg>')
    return "\n".join(parts)

# ── 组织架构图 ──────────────────────────────────────

def orgchart(items: list[str], title: str, s: DiagramStyle,
              w: int, h: int) -> str:
    """组织架构图：缩进表示层级（2 空格/级），首项为根。"""
    if len(items) == 1 and "|" in items[0]:
        items = items[0].split("|")
    if not items:
        return ""
    parsed: list[tuple[int, str]] = []
    for raw in items[:15]:
        label = raw.strip()
        if not label:
            continue
        depth = (len(raw) - len(raw.lstrip(" "))) // 2
        parsed.append((depth, label))
    if not parsed:
        return ""
    base_depth = parsed[0][0]

    # 依据缩进建树（更深节点挂在最近的更浅节点下）
    node_t = dict[str, Any]
    root: node_t = {"label": parsed[0][1], "children": []}
    stack: list[tuple[int, node_t]] = [(base_depth, root)]
    for depth, label in parsed[1:]:
        node: node_t = {"label": label, "children": []}
        while len(stack) > 1 and stack[-1][0] >= depth:
            stack.pop()
        stack[-1][1]["children"].append(node)
        stack.append((depth, node))

    # 叶子计数 → 叶子均布 x，父节点取子节点均值
    def count_leaves(nd: node_t) -> int:
        if not nd["children"]:
            return 1
        return sum(count_leaves(c) for c in nd["children"])

    n_leaves = count_leaves(root)
    leaf_w = min(200, (w - 200) / max(n_leaves, 1))
    counter = [0]
    flat: list[node_t] = []

    def layout(nd: node_t, depth: int) -> float:
        nd["y"] = 190 + depth * 150
        if not nd["children"]:
            nd["x"] = 100 + (counter[0] + 0.5) * leaf_w
            counter[0] += 1
        else:
            xs = [layout(c, depth + 1) for c in nd["children"]]
            nd["x"] = sum(xs) / len(xs)
        flat.append(nd)
        return nd["x"]

    layout(root, 0)
    delays = staggered(range(len(flat)), 0, s.stagger_delay)

    parts = [svg_frame(w, h),
             '<defs><filter id="ds_oc"><feDropShadow dx="0" dy="2"'
             ' stdDeviation="3" flood-opacity="0.25"/></filter></defs>']
    if title:
        parts.append(
            f'<text x="{w // 2}" y="80" font-size="{s.title_font_size}"'
            f' fill="{s.text_color}" text-anchor="middle" font-weight="bold">'
            f'{html_esc(title[:60])}</text>')

    # 连接线（父底 → 子顶）
    def draw_edges(nd: node_t) -> None:
        for c in nd["children"]:
            parts.append(
                f'<line x1="{nd["x"]:.1f}" y1="{nd["y"] + 26:.1f}"'
                f' x2="{c["x"]:.1f}" y2="{c["y"] - 26:.1f}"'
                f' stroke="{s.primary_color}44" stroke-width="1.5"/>')
            draw_edges(c)

    draw_edges(root)

    # 节点框（DFS 序逐元素入场）
    for i, nd in enumerate(flat):
        is_root = nd is root
        parts.append(
            f'<g style="animation-delay:{delays[i]}s">'
            f'<rect x="{nd["x"] - 75:.1f}" y="{nd["y"] - 26:.1f}" width="150" height="52"'
            f' rx="{s.border_radius}"'
            f' fill="{s.primary_color + "44" if is_root else s.item_bg}"'
            f' stroke="{s.primary_color}" stroke-width="{2 if is_root else 1}"'
            f' filter="url(#ds_oc)"/>'
            f'<text x="{nd["x"]:.1f}" y="{nd["y"] + 6:.1f}" font-size="{s.font_size - 6}"'
            f' fill="{s.text_color}" text-anchor="middle">'
            f'{html_esc(nd["label"][:12])}</text></g>')

    parts.append('</svg>')
    return "\n".join(parts)

RENDERER_MAP: dict[str, tuple[str, callable]] = {
    "mindmap": ("思维导图", mindmap),
    "radar": ("雷达图", radar),
    "gantt": ("甘特图", gantt),
    "venn3": ("三维图", venn3),
    "heatmap": ("热力图", heatmap),
    "sankey": ("桑基图", sankey),
    "concept": ("概念图", concept),
    "codeblock": ("代码块", codeblock),
    "datatable": ("数据表", datatable),
    "quote": ("引用", quote),
    "compcard": ("对比卡", compcard),
    "orgchart": ("组织架构图", orgchart),
}

