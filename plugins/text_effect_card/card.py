"""text_effect_card —— 生成带渐变/霓虹/故障/撕纸/圆角底板的透明文字卡 PNG。

为什么需要它：25 个 `text_*` 工具只能 drawtext 纯色字，ASS 也不能做渐变填充、
纸张纹理、glitch、真霓虹。这个工具把“静态文字视觉”先做成透明 PNG，
再由 `timeline_insert_image` / overlay 使用；复杂效果不再假装 ASS 能渲染。

它只负责**静态卡片**，不负责逐字/位移动画；动画仍由 ASS/MG 负责。
"""

from __future__ import annotations

import math
import random
from pathlib import Path
from typing import Any, Optional

from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.tool.base import BaseTool


def _hex(value: str, fallback=(255, 255, 255, 255)) -> tuple[int, int, int, int]:
    s = str(value or "").strip()
    if s.startswith("#") and len(s) in (4, 7, 9):
        if len(s) == 4:
            s = "#" + "".join(ch * 2 for ch in s[1:]) + "ff"
        elif len(s) == 7:
            s += "ff"
        return (int(s[1:3], 16), int(s[3:5], 16), int(s[5:7], 16), int(s[7:9], 16))
    return fallback


def _font_path() -> str:
    for p in (
        r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\msyh.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/System/Library/Fonts/PingFang.ttc",
    ):
        if Path(p).exists():
            return p
    return ""


class TextEffectCardTool(BaseTool):
    name = "text_effect_card"
    capability = "text_effect"
    permission_tier = "write_local"
    agent_callable = True
    description = (
        "生成透明文字卡 PNG，支持渐变填充、圆角/撕纸底板、描边、阴影、发光/霓虹、"
        "RGB 故障偏移。用于图片式文字效果；不负责逐字/位移动画。"
    )
    dependencies = []

    async def execute(
        self,
        text: str,
        *,
        style: Optional[dict[str, Any]] = None,
        width: int = 1920,
        height: int = 1080,
        output_path: str = "",
        **kwargs: Any,
    ) -> ToolExecResult:
        try:
            from PIL import Image, ImageDraw, ImageFilter, ImageFont
        except Exception as exc:  # noqa: BLE001
            return ToolExecResult(status=ToolStatus.DEPENDENCY_MISSING,
                                  tool_name=self.name, error=f"Pillow 不可用: {exc}")

        st = dict(style or {})
        text = str(text or "").strip()
        if not text:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error="text 为空")
        font_size = int(max(8, min(240, float(st.get("font_size") or 72))))
        font_path = _font_path()
        try:
            font = ImageFont.truetype(font_path, font_size) if font_path else ImageFont.load_default()
        except Exception:
            font = ImageFont.load_default()

        img = Image.new("RGBA", (int(width), int(height)), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        bbox = draw.multiline_textbbox((0, 0), text, font=font, spacing=int(font_size * 0.25), align="center")
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        pad_x = int(st.get("box_padding_x") or 28)
        pad_y = int(st.get("box_padding_y") or 18)
        cx, cy = width / 2, height / 2
        x0, y0 = cx - tw / 2 - pad_x, cy - th / 2 - pad_y
        x1, y1 = cx + tw / 2 + pad_x, cy + th / 2 + pad_y

        # 底板：solid / paper（撕纸锯齿）/ gradient 背景
        bg = st.get("background") or {}
        if isinstance(bg, str):
            bg = {"type": bg}
        if st.get("box") or bg:
            radius = int(st.get("box_radius") or 12)
            if str(bg.get("type")) == "paper":
                paper = bg.get("color") or st.get("box_color") or "#f2efe6"
                rnd = random.Random(42)
                pts = []
                steps = 14
                for i in range(steps):
                    px = x0 + (x1 - x0) * i / steps
                    pts.append((px, y0 + rnd.randint(-10, 10)))
                for i in range(steps):
                    py = y0 + (y1 - y0) * i / steps
                    pts.append((x1 + rnd.randint(-12, 12), py))
                for i in range(steps):
                    px = x1 - (x1 - x0) * i / steps
                    pts.append((px, y1 + rnd.randint(-10, 10)))
                for i in range(steps):
                    py = y1 - (y1 - y0) * i / steps
                    pts.append((x0 + rnd.randint(-12, 12), py))
                shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
                ImageDraw.Draw(shadow).polygon([(x + 8, y + 10) for x, y in pts],
                                               fill=(0, 0, 0, 90))
                img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(8)))
                draw.polygon(pts, fill=_hex(str(paper)))
            elif str(bg.get("type")) == "gradient" and isinstance(bg.get("colors"), list):
                colors = [_hex(c) for c in bg["colors"]]
                if len(colors) >= 2:
                    grad = Image.new("RGBA", img.size, (0, 0, 0, 0))
                    gd = ImageDraw.Draw(grad)
                    for y in range(int(y0), int(y1) + 1):
                        t = (y - y0) / max(1, y1 - y0)
                        c = tuple(int(colors[0][k] * (1 - t) + colors[1][k] * t) for k in range(4))
                        gd.line([(x0, y), (x1, y)], fill=c)
                    mask = Image.new("L", img.size, 0)
                    ImageDraw.Draw(mask).rounded_rectangle((x0, y0, x1, y1), radius, fill=255)
                    img.alpha_composite(Image.composite(grad, Image.new("RGBA", img.size, (0, 0, 0, 0)), mask))
            else:
                draw.rounded_rectangle((x0, y0, x1, y1), radius,
                                       fill=_hex(str(st.get("box_color") or "#000000aa")))

        # 文字层（可多次：glitch RGB 偏移 / 霓虹发光）
        text_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        td = ImageDraw.Draw(text_layer)
        stroke_w = int(st.get("stroke_width") or 0)
        stroke_c = _hex(str(st.get("stroke_color") or "#000000"))
        fill_val = st.get("fill") or "#ffffff"
        if isinstance(fill_val, dict) and str(fill_val.get("type")) == "gradient":
            colors = [_hex(c) for c in fill_val.get("colors", [])]
            if len(colors) >= 2:
                grad = Image.new("RGBA", img.size, (0, 0, 0, 0))
                gd = ImageDraw.Draw(grad)
                for y in range(0, height, 2):
                    t = y / max(1, height - 1)
                    c = tuple(int(colors[0][k] * (1 - t) + colors[1][k] * t) for k in range(4))
                    gd.line([(0, y), (width, y)], fill=c)
                mask = Image.new("L", img.size, 0)
                md = ImageDraw.Draw(mask)
                md.multiline_text((cx - tw / 2, cy - th / 2), text, font=font, fill=255,
                                  align="center", stroke_width=stroke_w, stroke_fill=255)
                text_layer.alpha_composite(Image.composite(grad, Image.new("RGBA", img.size, (0, 0, 0, 0)), mask))
            else:
                td.multiline_text((cx - tw / 2, cy - th / 2), text, font=font,
                                  fill=_hex(str(fill_val)), align="center",
                                  stroke_width=stroke_w, stroke_fill=stroke_c)
        else:
            td.multiline_text((cx - tw / 2, cy - th / 2), text, font=font,
                              fill=_hex(str(fill_val)), align="center",
                              stroke_width=stroke_w, stroke_fill=stroke_c)

        if st.get("glitch"):
            base = text_layer.copy()
            for dx, color in ((-8, (255, 40, 80, 180)), (8, (40, 220, 255, 180))):
                tint = Image.new("RGBA", img.size, color)
                tint.putalpha(base.getchannel("A").point(lambda a: int(a * 0.6)))
                img.alpha_composite(tint, (dx, 0))
        if st.get("glow") or st.get("neon"):
            glow_color = _hex(str(st.get("glow_color") or "#00e5ff"))
            glow = text_layer.filter(ImageFilter.GaussianBlur(int(st.get("glow_width") or 12)))
            tint = Image.new("RGBA", img.size, glow_color)
            tint.putalpha(glow.getchannel("A"))
            img.alpha_composite(tint)
        img.alpha_composite(text_layer)

        out = Path(output_path) if output_path else Path("_cache") / f"text_card_{abs(hash(text)) & 0xFFFFFF}.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        img.save(out)
        return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name,
                              output={"path": str(out), "width": width, "height": height},
                              output_path=str(out))
