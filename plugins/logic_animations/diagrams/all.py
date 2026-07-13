"""12 种高级图解 SVG 生成函数。

每个函数签名: (items: list[str], title: str, style: DiagramStyle, w: int, h: int) -> str
"""

from __future__ import annotations
import math as _m

from clipwright.animation.diagram_svg import DiagramStyle
from plugins.logic_animations.utils.svg_helpers import (
    svg_frame, html_esc, staggered, drop_shadow, arrow_marker,
)


# ── 1. 思维导图 ──────────────────────────────────────

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


# ── 2. 雷达图 ────────────────────────────────────────

def radar(items: list[str], title: str, s: DiagramStyle, w: int, h: int) -> str:
    """多维度能力对比雷达图。每项格式 "标签:数值" 或纯标签（等值）。"""
    n = min(len(items), 8)
    if n < 3:
        n = 3
    cx, cy = w // 2, h // 2 - 20
    r = 180
    delays = staggered(2, 0, s.stagger_delay)

    parsed = []
    for item in items[:8]:
        if ":" in item:
            ps = item.split(":", 1)
            parsed.append((ps[0], max(0, min(100, float(ps[1])))))
        else:
            parsed.append((item, 80.0))
    max_val = max(v for _, v in parsed) or 100

    parts = [svg_frame(w, h), "<defs>" + drop_shadow("ds_rd") + "</defs>"]
    if title:
        parts.append(f'<text x="{cx}" y="40" font-size="{s.title_font_size}" fill="{s.text_color}" text-anchor="middle" font-weight="bold">{html_esc(title[:60])}</text>')

    angle_step = 2 * _m.pi / n

    # 网格（3 层）
    for level in [0.33, 0.66, 1.0]:
        pts = []
        for i in range(n):
            a = angle_step * i - _m.pi / 2
            pts.append(f"{cx + r * level * _m.cos(a):.0f},{cy + r * level * _m.sin(a):.0f}")
        parts.append(f'<polygon points="{" ".join(pts)}" fill="none" stroke="{s.text_color}22" stroke-width="1"/>')

    # 数据多边形
    pts = []
    for i, (label, val) in enumerate(parsed):
        a = angle_step * i - _m.pi / 2
        dist = (val / max_val) * r
        pts.append(f"{cx + dist * _m.cos(a):.1f},{cy + dist * _m.sin(a):.1f}")
    parts.append(
        f'<polygon points="{" ".join(pts)}" fill="{s.primary_color}33" stroke="{s.primary_color}" stroke-width="2" style="animation-delay:{delays[0]}s"/>'
    )

    # 轴 + 标签
    for i, (label, val) in enumerate(parsed):
        a = angle_step * i - _m.pi / 2
        lx = cx + (r + 10) * _m.cos(a)
        ly = cy + (r + 10) * _m.sin(a)
        mx = cx + r * _m.cos(a)
        my = cy + r * _m.sin(a)
        anchor = "middle"
        if abs(_m.cos(a)) < 0.1: anchor = "middle"
        elif _m.cos(a) > 0: anchor = "start"
        else: anchor = "end"
        dy = 0 if abs(_m.sin(a)) < 0.3 else (8 if _m.sin(a) > 0 else -8)
        parts.append(
            f'<g style="animation-delay:{delays[1]}s">'
            f'<line x1="{cx}" y1="{cy}" x2="{mx}" y2="{my}" stroke="{s.text_color}15" stroke-width="1"/>'
            f'<text x="{lx}" y="{ly + dy}" font-size="13" fill="{s.text_color}" text-anchor="{anchor}">{html_esc(label[:8])}</text></g>'
        )

    parts.append("</svg>")
    return "\n".join(parts)


# ── 3. 甘特图 ────────────────────────────────────────

def gantt(items: list[str], title: str, s: DiagramStyle, w: int, h: int) -> str:
    """横向条状图。每项格式 "标签:开始天:持续天"。"""
    n = min(len(items), 10)
    if n == 0:
        return ""
    delays = staggered(n, 0, s.stagger_delay)
    parts = [svg_frame(w, h), "<defs>" + drop_shadow("ds_gt") + "</defs>"]
    colors = [s.primary_color, s.secondary_color, s.accent_color, "#34d399", "#a855f7", "#f97316", "#06b6d4", "#ec4899", "#84cc16", "#14b8a6"]

    if title:
        parts.append(f'<text x="{w // 2}" y="30" font-size="{s.title_font_size}" fill="{s.text_color}" text-anchor="middle" font-weight="bold">{html_esc(title[:60])}</text>')

    chart_left, chart_top = 120, 70
    bar_h, gap = 24, 16
    max_day = 0
    rows = []
    for item in items[:10]:
        segs = item.split(":")
        label = segs[0]
        start = int(segs[1]) if len(segs) > 1 and segs[1].strip().isdigit() else 0
        dur = int(segs[2]) if len(segs) > 2 and segs[2].strip().isdigit() else 10
        rows.append((label, start, dur))
        if start + dur > max_day:
            max_day = start + dur
    max_day = max(max_day, 1)
    chart_w = w - chart_left - 60
    px_per_day = chart_w / max_day

    for i, (label, start, dur) in enumerate(rows):
        yy = chart_top + i * (bar_h + gap)
        x1 = chart_left + start * px_per_day
        bw = dur * px_per_day
        c = colors[i % len(colors)]
        parts.append(
            f'<g style="animation-delay:{delays[i]}s">'
            f'<text x="{chart_left - 8}" y="{yy + bar_h // 2 + 4}" font-size="12" fill="{s.text_color}" text-anchor="end">{html_esc(label[:15])}</text>'
            f'<rect x="{x1}" y="{yy}" width="{max(bw, 4)}" height="{bar_h}" rx="4" fill="{c}88" stroke="{c}" filter="url(#ds_gt)"/>'
            f'<text x="{x1 + 6}" y="{yy + bar_h // 2 + 4}" font-size="11" fill="#fff">{start}-{start + dur}</text></g>'
        )

    # 时间线刻度
    for d in range(0, max_day + 1, max(1, max_day // 8)):
        x = chart_left + d * px_per_day
        parts.append(f'<line x1="{x}" y1="{chart_top - 8}" x2="{x}" y2="{chart_top + n * (bar_h + gap)}" stroke="{s.text_color}10" stroke-width="1"/>')
        parts.append(f'<text x="{x}" y="{chart_top - 12}" font-size="10" fill="{s.text_color}66" text-anchor="middle">{d}</text>')

    parts.append("</svg>")
    return "\n".join(parts)


# ── 4. 维恩 3 圆 ─────────────────────────────────────

def venn3(items: list[str], title: str, s: DiagramStyle, w: int, h: int) -> str:
    """三圆维恩图。items: [左, 右, 底, 左右交, 左底交, 右底交, 三圆交]。"""
    delays = staggered(3, 0, s.stagger_delay)
    cx, cy = w // 2, h // 2
    r = 130
    parts = [svg_frame(w, h)]
    if title:
        parts.append(f'<text x="{cx}" y="{cy - r - 50}" font-size="{s.title_font_size}" fill="{s.text_color}" text-anchor="middle" font-weight="bold">{html_esc(title[:60])}</text>')

    # 三个圆
    circles = [
        (cx - 70, cy - 40, s.primary_color, delays[0]),
        (cx + 70, cy - 40, s.secondary_color, delays[1]),
        (cx, cy + 60, s.accent_color, delays[2]),
    ]
    for cxx, cyy, color, delay in circles:
        parts.append(
            f'<circle cx="{cxx}" cy="{cyy}" r="{r}" fill="{color}22" stroke="{color}" stroke-width="2" style="animation-delay:{delay}s"/>'
        )

    # 标签位置（7 个区域）
    labels_v3 = [("", "", "", ""), ("", "", "", "")]
    labels_data = [
        (cx - 120, cy - 80, 0, items[0] if len(items) > 0 else ""),
        (cx + 120, cy - 80, 1, items[1] if len(items) > 1 else ""),
        (cx, cy + 90, 2, items[2] if len(items) > 2 else ""),
        (cx, cy - 50, 0, items[3] if len(items) > 3 else ""),
        (cx - 65, cy + 30, 1, items[4] if len(items) > 4 else ""),
        (cx + 65, cy + 30, 2, items[5] if len(items) > 5 else ""),
        (cx, cy + 10, 0, items[6] if len(items) > 6 else ""),
    ]
    for lx, ly, di, txt in labels_data:
        if txt:
            parts.append(
                f'<text x="{lx}" y="{ly + 4}" font-size="13" fill="{s.text_color}" text-anchor="middle">{html_esc(txt[:8])}</text>'
            )

    parts.append("</svg>")
    return "\n".join(parts)


# ── 5. 热力图 ────────────────────────────────────────

def heatmap(items: list[str], title: str, s: DiagramStyle, w: int, h: int) -> str:
    """色块矩阵。首项为行标题（逗号分隔），其余为列标签+数值。"""
    if not items:
        return ""
    delays = staggered(2, 0, s.stagger_delay)
    parts = [svg_frame(w, h)]
    if title:
        parts.append(f'<text x="{w // 2}" y="30" font-size="{s.title_font_size}" fill="{s.text_color}" text-anchor="middle" font-weight="bold">{html_esc(title[:60])}</text>')

    # 解析：首行为列标签
    col_labels = items[0].split(",") if items else []
    rows_raw = items[1:] if len(items) > 1 else []
    n_cols = len(col_labels) or 1
    n_rows = len(rows_raw) or 1
    cell_w = min(80, (w - 160) // n_cols)
    cell_h = 36
    start_x = (w - n_cols * cell_w) // 2
    start_y = 60

    # 解析数值
    data: list[list[float]] = []
    row_labels: list[str] = []
    for rr in rows_raw:
        cells = rr.split(",")
        row_labels.append(cells[0] if cells else "")
        vals = []
        for c in cells[1:1 + n_cols]:
            try:
                vals.append(float(c.strip()))
            except:
                vals.append(0.0)
        while len(vals) < n_cols:
            vals.append(0.0)
        data.append(vals)
    all_vals = [v for row in data for v in row]
    max_v = max(all_vals) if all_vals else 1
    min_v = min(all_vals) if all_vals else 0
    v_range = max_v - min_v or 1

    # 颜色函数：值 → 色相
    def _heat_color(val: float) -> str:
        t = (val - min_v) / v_range
        # 蓝→青→黄→红
        r = min(255, int(255 * t))
        g = min(255, int(200 * (1 - abs(t - 0.5) * 2)))
        b = min(255, int(255 * (1 - t)))
        return f"#{r:02x}{g:02x}{b:02x}"

    # 表头
    for ci in range(n_cols):
        x = start_x + ci * cell_w
        parts.append(f'<text x="{x + cell_w // 2}" y="{start_y - 8}" font-size="11" fill="{s.text_color}" text-anchor="middle">{html_esc(col_labels[ci][:6])}</text>')

    for ri, row in enumerate(data):
        yy = start_y + ri * cell_h
        parts.append(f'<g style="animation-delay:{delays[min(ri, len(delays) - 1)]}s">')
        if ri < len(row_labels):
            parts.append(f'<text x="{start_x - 8}" y="{yy + cell_h // 2 + 4}" font-size="11" fill="{s.text_color}" text-anchor="end">{html_esc(row_labels[ri][:8])}</text>')
        for ci, val in enumerate(row):
            x = start_x + ci * cell_w
            color = _heat_color(val)
            parts.append(
                f'<rect x="{x}" y="{yy}" width="{cell_w}" height="{cell_h}" fill="{color}" stroke="#fff" stroke-width="0.5"/>'
                f'<text x="{x + cell_w // 2}" y="{yy + cell_h // 2 + 4}" font-size="11" fill="#fff" text-anchor="middle">{val:.0f}</text>'
            )
        parts.append("</g>")

    parts.append("</svg>")
    return "\n".join(parts)


# ── 6. 桑基图 ────────────────────────────────────────

def sankey(items: list[str], title: str, s: DiagramStyle, w: int, h: int) -> str:
    """流量/流向图。items: 格式 "源:目标:流量值"。"""
    if not items:
        return ""
    parts = [svg_frame(w, h)]
    if title:
        parts.append(f'<text x="{w // 2}" y="30" font-size="{s.title_font_size}" fill="{s.text_color}" text-anchor="middle" font-weight="bold">{html_esc(title[:60])}</text>')

    # 解析流量
    flows: list[tuple[str, str, float]] = []
    nodes_set: set[str] = set()
    for item in items:
        segs = item.split(":")
        if len(segs) >= 3:
            src, dst = segs[0], segs[1]
            try:
                val = float(segs[2])
            except:
                val = 10
            flows.append((src, dst, val))
            nodes_set.add(src)
            nodes_set.add(dst)

    if not flows:
        return ""

    # 简单布局：左侧源节点，右侧目标节点
    sources = list(dict.fromkeys([f[0] for f in flows]))
    targets = list(dict.fromkeys([f[1] for f in flows]))
    total = max(sum(f[2] for f in flows), 1)
    max_h = 400
    left_x, right_x = 200, w - 200
    top_y = 80
    colors = [s.primary_color, s.secondary_color, s.accent_color, "#34d399", "#a855f7"]

    delays = staggered(len(flows), 0, s.stagger_delay * 0.8)

    # 源节点
    cur_y = top_y
    for i, src in enumerate(sources):
        h_src = max(20, (sum(f[2] for f in flows if f[0] == src) / total) * max_h)
        parts.append(
            f'<g style="animation-delay:{delays[min(i, len(delays) - 1)]}s">'
            f'<rect x="{left_x - 10}" y="{cur_y}" width="10" height="{h_src}" fill="{colors[i % len(colors)]}88" stroke="{colors[i % len(colors)]}"/>'
            f'<text x="{left_x - 16}" y="{cur_y + h_src // 2 + 4}" font-size="12" fill="{s.text_color}" text-anchor="end">{html_esc(src[:10])}</text></g>'
        )
        cur_y += h_src + 10

    # 目标节点
    cur_y = top_y
    for i, dst in enumerate(targets):
        h_dst = max(20, (sum(f[2] for f in flows if f[1] == dst) / total) * max_h)
        parts.append(
            f'<g style="animation-delay:{delays[min(i, len(delays) - 1)]}s">'
            f'<rect x="{right_x}" y="{cur_y}" width="10" height="{h_dst}" fill="{colors[(i + 2) % len(colors)]}88" stroke="{colors[(i + 2) % len(colors)]}"/>'
            f'<text x="{right_x + 16}" y="{cur_y + h_dst // 2 + 4}" font-size="12" fill="{s.text_color}" text-anchor="start">{html_esc(dst[:10])}</text></g>'
        )
        cur_y += h_dst + 10

    # 流量路径
    for fi, (src, dst, val) in enumerate(flows):
        si = sources.index(src) if src in sources else 0
        ti = targets.index(dst) if dst in targets else 0
        src_h = sum(f[2] for f in flows if f[0] == src) or 1
        src_y = top_y + sum((sum(f[2] for f in flows if f[0] == s) / total) * max_h + 10 for s in sources[:si]) + (val / src_h) * (sum(f[2] for f in flows if f[0] == src) / total) * max_h
        dst_h = sum(f[2] for f in flows if f[1] == dst) or 1
        dst_y = top_y + sum((sum(f[2] for f in flows if f[1] == t) / total) * max_h + 10 for t in targets[:ti]) + (val / dst_h) * (sum(f[2] for f in flows if f[1] == dst) / total) * max_h
        bw = max(val / total * max_h * 0.5, 2)
        bar_h = max(bw, 2)
        parts.append(
            f'<g style="animation-delay:{delays[min(fi, len(delays) - 1)]}s">'
            f'<line x1="{left_x}" y1="{src_y}" x2="{right_x}" y2="{dst_y}" stroke="{colors[fi % len(colors)]}66" stroke-width="{bar_h}" opacity="0.6"/>'
            f'</g>'
        )

    parts.append("</svg>")
    return "\n".join(parts)


# ── 7. 概念图 ────────────────────────────────────────

def concept(items: list[str], title: str, s: DiagramStyle, w: int, h: int) -> str:
    """自由节点 + 任意连线。items: "节点ID:x:y:标签", "节点ID->节点ID:标签"。"""
    delays = staggered(8, 0, s.stagger_delay)
    parts = [svg_frame(w, h), "<defs>" + drop_shadow("ds_cp") + arrow_marker("a_cp", s.arrow_color) + "</defs>"]
    if title:
        parts.append(f'<text x="{w // 2}" y="30" font-size="{s.title_font_size}" fill="{s.text_color}" text-anchor="middle" font-weight="bold">{html_esc(title[:60])}</text>')

    # 解析节点和边
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    for item in items:
        if "->" in item:
            segs = item.split(":", 1)
            route = segs[0]
            label = segs[1] if len(segs) > 1 else ""
            if "->" in route:
                p = route.split("->", 1)
                edges.append({"from": p[0].strip(), "to": p[1].strip(), "label": label})
        else:
            segs = item.split(":")
            nid = segs[0]
            nodes[nid] = {
                "x": int(segs[1]) if len(segs) > 1 and segs[1].strip().isdigit() else w // 2,
                "y": int(segs[2]) if len(segs) > 2 and segs[2].strip().isdigit() else 200 + len(nodes) * 80,
                "label": segs[3] if len(segs) > 3 else nid,
            }

    ci = 0
    for edge in edges:
        fn = nodes.get(edge["from"])
        tn = nodes.get(edge["to"])
        if fn and tn:
            d = delays[min(ci, len(delays) - 1)]
            parts.append(f'<g style="animation-delay:{d}s"><line x1="{fn["x"]}" y1="{fn["y"]}" x2="{tn["x"]}" y2="{tn["y"]}" stroke="{s.arrow_color}55" stroke-width="2" marker-end="url(#a_cp)"/>')
            if edge["label"]:
                parts.append(f'<text x="{(fn["x"] + tn["x"]) // 2}" y="{(fn["y"] + tn["y"]) // 2 - 8}" font-size="12" fill="{s.text_color}88" text-anchor="middle">{html_esc(edge["label"][:15])}</text>')
            parts.append("</g>")
            ci += 1

    for ni, (nid, nd) in enumerate(nodes.items()):
        d = delays[min(ni, len(delays) - 1)]
        parts.append(
            f'<g style="animation-delay:{d}s">'
            f'<circle cx="{nd["x"]}" cy="{nd["y"]}" r="6" fill="{s.primary_color}"/>'
            f'<text x="{nd["x"]}" y="{nd["y"] - 16}" font-size="14" fill="{s.text_color}" text-anchor="middle">{html_esc(nd["label"][:15])}</text></g>'
        )

    parts.append("</svg>")
    return "\n".join(parts)


# ── 8. 代码块 ────────────────────────────────────────

def codeblock(items: list[str], title: str, s: DiagramStyle, w: int, h: int) -> str:
    """等宽代码块 + 行号。items 每行为一行代码。"""
    n = min(len(items), 20)
    delays = staggered(n, 0, s.stagger_delay * 0.15)
    parts = [svg_frame(w, h)]
    if title:
        parts.append(f'<text x="{w // 2}" y="30" font-size="{s.title_font_size}" fill="{s.text_color}" text-anchor="middle" font-weight="bold">{html_esc(title[:60])}</text>')

    block_x, block_y = 120, 60
    line_h = 28
    code_w = w - block_x * 2
    block_h = n * line_h + 20

    # 背景
    parts.append(f'<rect x="{block_x}" y="{block_y}" width="{code_w}" height="{block_h}" rx="8" fill="#1e1e2e" stroke="#333"/>')
    # 顶栏圆点
    for dot_x in [block_x + 16, block_x + 36, block_x + 56]:
        parts.append(f'<circle cx="{dot_x}" cy="{block_y + 16}" r="5" fill="#ff5f56"/>')
    # 代码行
    for i, line in enumerate(items[:20]):
        yy = block_y + 40 + i * line_h
        safe = html_esc(line[:80])
        # 行号
        parts.append(f'<text x="{block_x + 80}" y="{yy}" font-size="13" fill="#555" font-family="monospace" text-anchor="end" style="animation-delay:{delays[i]}s">{i + 1}</text>')
        # 简易语法高亮：检测 # 注释 和 关键字
        if "//" in safe or "#" in safe:
            code_part = safe.split("//")[0] if "//" in safe else safe.split("#")[0]
            comment_part = ("//" + safe.split("//")[1]) if "//" in safe else ("#" + safe.split("#")[1])
            parts.append(f'<text x="{block_x + 92}" y="{yy}" font-size="13" fill="#cdd6f4" font-family="monospace" style="animation-delay:{delays[i]}s">{html_esc(code_part[:60])}</text>')
            parts.append(f'<text x="{block_x + 92 + len(code_part) * 7.8}" y="{yy}" font-size="13" fill="#6c7086" font-family="monospace" style="animation-delay:{delays[i]}s">{html_esc(comment_part[:40])}</text>')
        else:
            color = "#cba6f7" if any(kw in safe for kw in ["def ", "class ", "import ", "from ", "return"]) else "#cdd6f4"
            parts.append(f'<text x="{block_x + 92}" y="{yy}" font-size="13" fill="{color}" font-family="monospace" style="animation-delay:{delays[i]}s">{safe[:70]}</text>')

    parts.append("</svg>")
    return "\n".join(parts)


# ── 9. 数据表格 ──────────────────────────────────────

def datatable(items: list[str], title: str, s: DiagramStyle, w: int, h: int) -> str:
    """表格。首行为表头（逗号分隔），其余为数据行。"""
    if not items:
        return ""
    delays = staggered(2, 0, s.stagger_delay)
    parts = [svg_frame(w, h)]
    if title:
        parts.append(f'<text x="{w // 2}" y="30" font-size="{s.title_font_size}" fill="{s.text_color}" text-anchor="middle" font-weight="bold">{html_esc(title[:60])}</text>')

    headers = items[0].split(",") if items else ["A", "B"]
    rows_data = [r.split(",") for r in items[1:]]
    n_cols = len(headers)
    n_rows = len(rows_data)
    col_w = min(140, (w - 80) // n_cols)
    row_h = 32
    start_x = (w - n_cols * col_w) // 2
    start_y = 60

    for ci, hdr in enumerate(headers):
        x = start_x + ci * col_w
        parts.append(
            f'<rect x="{x}" y="{start_y}" width="{col_w}" height="{row_h}" fill="{s.primary_color}33" stroke="{s.primary_color}66" stroke-width="1"/>'
            f'<text x="{x + col_w // 2}" y="{start_y + row_h // 2 + 4}" font-size="13" fill="{s.text_color}" text-anchor="middle" font-weight="bold">{html_esc(hdr[:10])}</text>'
        )

    for ri, row in enumerate(rows_data[:12]):
        yy = start_y + (ri + 1) * row_h
        bg = s.item_bg if ri % 2 == 0 else "transparent"
        parts.append(f'<g style="animation-delay:{delays[min(ri, len(delays) - 1)]}s">')
        for ci in range(n_cols):
            x = start_x + ci * col_w
            val = row[ci] if ci < len(row) else ""
            parts.append(
                f'<rect x="{x}" y="{yy}" width="{col_w}" height="{row_h}" fill="{bg}" stroke="{s.text_color}10" stroke-width="0.5"/>'
                f'<text x="{x + col_w // 2}" y="{yy + row_h // 2 + 4}" font-size="12" fill="{s.text_color}" text-anchor="middle">{html_esc(val[:12])}</text>'
            )
        parts.append("</g>")

    parts.append("</svg>")
    return "\n".join(parts)


# ── 10. 引用卡片 ─────────────────────────────────────

def quote(items: list[str], title: str, s: DiagramStyle, w: int, h: int) -> str:
    """引用卡片。items[0] 引用内容，items[1] 作者（可选）。"""
    delays = staggered(2, 0, s.stagger_delay)
    parts = [svg_frame(w, h), "<defs>" + drop_shadow("ds_qt") + "</defs>"]
    if title:
        parts.append(f'<text x="{w // 2}" y="30" font-size="{s.title_font_size}" fill="{s.text_color}" text-anchor="middle" font-weight="bold">{html_esc(title[:60])}</text>')

    card_w, card_h = min(700, w - 80), min(300, h - 120)
    cx, cy = (w - card_w) // 2, (h - card_h) // 2 + 20

    # 卡片背景
    parts.append(
        f'<g style="animation-delay:{delays[0]}s">'
        f'<rect x="{cx}" y="{cy}" width="{card_w}" height="{card_h}" rx="16" fill="{s.item_bg}" stroke="{s.primary_color}44" stroke-width="1" filter="url(#ds_qt)"/>'
        # 左上引号
        f'<text x="{cx + 30}" y="{cy + 60}" font-size="64" fill="{s.primary_color}44" font-family="serif">“</text>'
        f'</g>'
    )

    # 引用文字
    quote_text = items[0] if items else ""
    lines = []
    line_buf = ""
    for ch in quote_text[:120]:
        line_buf += ch
        if len(line_buf) >= 28 or ch in "，。！？；":
            lines.append(line_buf)
            line_buf = ""
    if line_buf:
        lines.append(line_buf)

    for li, line in enumerate(lines[:5]):
        parts.append(
            f'<text x="{cx + 70}" y="{cy + 80 + li * 32}" font-size="20" fill="{s.text_color}" style="animation-delay:{delays[0]}s">{html_esc(line)}</text>'
        )

    # 作者
    if len(items) > 1 and items[1]:
        parts.append(
            f'<text x="{cx + card_w - 30}" y="{cy + card_h - 20}" font-size="15" fill="{s.text_color}66" text-anchor="end" font-style="italic" style="animation-delay:{delays[1]}s">— {html_esc(items[1][:30])}</text>'
        )

    parts.append("</svg>")
    return "\n".join(parts)


# ── 11. 对比卡片 ─────────────────────────────────────

def compcard(items: list[str], title: str, s: DiagramStyle, w: int, h: int) -> str:
    """左右双列详细对比。items 每项格式 "特征,A方,B方,胜出方(0/1/2)"。"""
    n = min(len(items), 10)
    delays = staggered(n, 0, s.stagger_delay)
    parts = [svg_frame(w, h)]
    if title:
        parts.append(f'<text x="{w // 2}" y="30" font-size="{s.title_font_size}" fill="{s.text_color}" text-anchor="middle" font-weight="bold">{html_esc(title[:60])}</text>')

    table_x, table_y = 60, 70
    col_w1, col_w23 = 140, (w - table_x * 2 - col_w1) // 2
    row_h = 36

    # 表头
    parts.append(
        f'<rect x="{table_x}" y="{table_y}" width="{col_w1}" height="{row_h}" fill="{s.primary_color}44" stroke="{s.primary_color}66"/>'
        f'<text x="{table_x + col_w1 // 2}" y="{table_y + row_h // 2 + 4}" font-size="13" fill="{s.text_color}" text-anchor="middle" font-weight="bold">特征</text>'
    )
    for ci, label, color in [(0, "A方", s.secondary_color), (1, "B方", s.accent_color)]:
        x = table_x + col_w1 + ci * col_w23
        parts.append(f'<rect x="{x}" y="{table_y}" width="{col_w23}" height="{row_h}" fill="{color}44" stroke="{color}66"/><text x="{x + col_w23 // 2}" y="{table_y + row_h // 2 + 4}" font-size="13" fill="{s.text_color}" text-anchor="middle" font-weight="bold">{label}</text>')

    for ri, item in enumerate(items[:10]):
        segs = item.split(",")
        feat = segs[0] if len(segs) > 0 else ""
        a = segs[1] if len(segs) > 1 else ""
        b = segs[2] if len(segs) > 2 else ""
        winner = int(segs[3]) if len(segs) > 3 and segs[3].strip().isdigit() else 0
        yy = table_y + (ri + 1) * row_h
        bg = s.item_bg if ri % 2 == 0 else "transparent"
        parts.append(f'<g style="animation-delay:{delays[ri]}s">')
        parts.append(f'<rect x="{table_x}" y="{yy}" width="{col_w1}" height="{row_h}" fill="{bg}" stroke="{s.text_color}10" stroke-width="0.5"/><text x="{table_x + col_w1 // 2}" y="{yy + row_h // 2 + 4}" font-size="12" fill="{s.text_color}" text-anchor="middle">{html_esc(feat[:10])}</text>')
        for ci, val, win_val in [(0, a, 1), (1, b, 2)]:
            x = table_x + col_w1 + ci * col_w23
            hl = "font-weight:bold" if winner == win_val else ""
            clr = s.text_color if winner == win_val else f"{s.text_color}88"
            parts.append(f'<rect x="{x}" y="{yy}" width="{col_w23}" height="{row_h}" fill="{bg}" stroke="{s.text_color}10" stroke-width="0.5"/><text x="{x + col_w23 // 2}" y="{yy + row_h // 2 + 4}" font-size="12" fill="{clr}" text-anchor="middle" {hl}>{html_esc(val[:12])}</text>')
        parts.append("</g>")

    parts.append("</svg>")
    return "\n".join(parts)


# ── 12. 组织结构图 ──────────────────────────────────

def orgchart(items: list[str], title: str, s: DiagramStyle, w: int, h: int) -> str:
    """自上而下组织结构图。items[0] 根节点，其余为子节点（用缩进表层级）。"""
    delays = staggered(12, 0, s.stagger_delay)
    parts = [svg_frame(w, h), "<defs>" + drop_shadow("ds_oc") + "</defs>"]
    if title:
        parts.append(f'<text x="{w // 2}" y="30" font-size="{s.title_font_size}" fill="{s.text_color}" text-anchor="middle" font-weight="bold">{html_esc(title[:60])}</text>')

    # 解析层级（缩进 = 空格数 // 2）
    levels: list[tuple[int, str]] = []
    for item in items:
        stripped = item.lstrip()
        indent = (len(item) - len(stripped)) // 2
        levels.append((indent, stripped))

    if not levels:
        return ""

    cx = w // 2
    level_h = 80
    node_w, node_h = 140, 44

    # 分配每层位置
    from collections import defaultdict
    level_nodes: dict[int, list[dict]] = defaultdict(list)
    for li, (lv, lbl) in enumerate(levels):
        level_nodes[lv].append({"label": lbl, "x": 0, "y": 100 + lv * level_h})

    # 每层节点水平居中
    for lv, nodes in level_nodes.items():
        n = len(nodes)
        spacing = min(200, (w - 100) // max(n, 1))
        total_w = (n - 1) * spacing
        sx = cx - total_w // 2
        for i, nd in enumerate(nodes):
            nd["x"] = sx + i * spacing

    # 连线（从上层到下层）
    prev_nodes = []
    ni = 0
    for lv in sorted(level_nodes.keys()):
        curr_nodes = level_nodes[lv]
        if prev_nodes:
            for ci, cn in enumerate(curr_nodes):
                pi = min(ci, len(prev_nodes) - 1)
                pn = prev_nodes[pi]
                d = delays[min(ni, len(delays) - 1)]
                parts.append(
                    f'<g style="animation-delay:{d}s">'
                    f'<line x1="{pn["x"]}" y1="{pn["y"] + node_h // 2}" x2="{cn["x"]}" y2="{cn["y"] - node_h // 2}" stroke="{s.primary_color}44" stroke-width="1.5"/></g>'
                )
                ni += 1
        prev_nodes = curr_nodes

    # 节点
    ni = 0
    for lv, nodes in level_nodes.items():
        for nd in nodes:
            d = delays[min(ni, len(delays) - 1)]
            is_top = lv == 0
            color = s.primary_color if is_top else s.item_bg
            stroke = s.primary_color if is_top else f"{s.primary_color}66"
            fill = f"{s.primary_color}44" if is_top else s.item_bg
            parts.append(
                f'<g style="animation-delay:{d}s">'
                f'<rect x="{nd["x"] - node_w // 2}" y="{nd["y"] - node_h // 2}" width="{node_w}" height="{node_h}" rx="8" fill="{fill}" stroke="{stroke}" stroke-width="2" filter="url(#ds_oc)"/>'
                f'<text x="{nd["x"]}" y="{nd["y"] + 5}" font-size="13" fill="{s.text_color}" text-anchor="middle" font-weight="{"bold" if is_top else "normal"}">{html_esc(nd["label"][:12])}</text></g>'
            )
            ni += 1

    parts.append("</svg>")
    return "\n".join(parts)


# ── 导出映射表 ───────────────────────────────────────

RENDERER_MAP: dict[str, tuple[str, callable]] = {
    "mindmap":     ("思维导图", mindmap),
    "radar":       ("雷达图", radar),
    "gantt":       ("甘特图", gantt),
    "venn3":       ("维恩3圆", venn3),
    "heatmap":     ("热力图", heatmap),
    "sankey":      ("桑基图", sankey),
    "concept":     ("概念图", concept),
    "codeblock":   ("代码块", codeblock),
    "datatable":   ("数据表格", datatable),
    "quote":       ("引用卡片", quote),
    "compcard":    ("对比卡片", compcard),
    "orgchart":    ("组织结构图", orgchart),
}
