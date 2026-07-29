"""文字样式定义 — drawtext filter 构建 + 主题预设。

供 RenderService._build_drawtext_filter() 使用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class TextStyle:
    """文字叠加样式 — 转为 FFmpeg drawtext filter 参数。"""

    font_size: int = 48
    font_color: str = "#ffffff"
    stroke_width: int = 0
    stroke_color: str = "#000000"
    position: str = "bottom"
    offset_y: int = 0
    box: bool = False
    box_color: str = "#00000080"
    box_border: int = 8
    shadow_x: int = 0
    shadow_y: int = 0
    shadow_color: str = "#00000080"

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "TextStyle":
        """从字典构造 TextStyle。"""
        if not d:
            return cls()
        return cls(
            font_size=d.get("font_size", 48),
            font_color=d.get("font_color", "#ffffff"),
            stroke_width=d.get("stroke_width", 0),
            stroke_color=d.get("stroke_color", "#000000"),
            position=d.get("position", "bottom"),
            offset_y=d.get("offset_y", 0),
            box=d.get("box", False),
            box_color=d.get("box_color", "#00000080"),
            box_border=d.get("box_border", 8),
            shadow_x=d.get("shadow_x", 0),
            shadow_y=d.get("shadow_y", 0),
            shadow_color=d.get("shadow_color", "#00000080"),
        )

    def build_drawtext_filter(self, text: str, start_sec: float, duration_sec: float, font_file: str = "") -> str:
        """构建 FFmpeg drawtext filter 字符串。"""
        safe = text.replace("'", "'\\''").replace(":", "\\:")
        font_arg = f":fontfile={font_file}" if font_file and Path(font_file).exists() else ""

        pos_map = {
            "center": ("(w-text_w)/2", "(h-text_h)/2"),
            "bottom": ("(w-text_w)/2", f"h-text_h-20-{self.offset_y}"),
            "top": ("(w-text_w)/2", f"20+{self.offset_y}"),
            "left": ("20", "(h-text_h)/2"),
            "right": (f"w-text_w-20", "(h-text_h)/2"),
        }
        xp, yp = pos_map.get(self.position, pos_map["bottom"])

        parts = [
            f"drawtext=text='{safe}'{font_arg}",
            f"fontsize={self.font_size}",
            f"fontcolor={self.font_color}",
            f"x={xp}",
            f"y={yp}",
        ]

        if self.stroke_width > 0:
            parts.append(f"bordercolor={self.stroke_color}")
            parts.append(f"borderw={self.stroke_width}")

        if self.box:
            parts.append(f"box=1:boxcolor={self.box_color}:boxborderw={self.box_border}")

        if self.shadow_x != 0 or self.shadow_y != 0:
            parts.append(f"shadowx={self.shadow_x}:shadowy={self.shadow_y}:shadowcolor={self.shadow_color}")

        parts.append(f"enable='between(t,{start_sec},{start_sec + duration_sec})'")
        return ":".join(parts)
