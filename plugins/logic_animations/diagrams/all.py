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


RENDERER_MAP: dict[str, tuple[str, callable]] = {
    "mindmap": ("思维导图", mindmap),
}
