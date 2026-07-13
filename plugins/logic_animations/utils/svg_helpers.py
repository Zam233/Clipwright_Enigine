"""公共 SVG 辅助工具。"""
from __future__ import annotations


def svg_frame(width: int, height: int) -> str:
    return f'<svg width="{width}" height="{height}" style="position:absolute;top:0;left:0">'


def html_esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def staggered(count: int, delay: float = 0, step: float = 0.25) -> list[float]:
    return [delay + i * step for i in range(count)]


def drop_shadow(filter_id: str = "ds") -> str:
    return f'<filter id="{filter_id}"><feDropShadow dx="0" dy="2" stdDeviation="3" flood-opacity="0.25"/></filter>'


def arrow_marker(marker_id: str, color: str = "#4f8cff") -> str:
    return (
        f'<marker id="{marker_id}" viewBox="0 0 10 10" refX="10" refY="5"'
        f' markerWidth="8" markerHeight="8" orient="auto">'
        f'<path d="M0,0L10,5L0,10Z" fill="{color}"/></marker>'
    )
