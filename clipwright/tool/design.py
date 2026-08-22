"""文字样式定义 — drawtext filter 构建 + 主题预设。

供 RenderService._build_drawtext_filter() 使用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


def color_to_drawtext(color: str | None) -> str:
    """将 schema 颜色 `#RRGGBB` / `#RRGGBBAA` 转为 drawtext 的 `0xRRGGBB` / `0xRRGGBB@alpha`。

    drawtext 用 0x 前缀解析颜色；8 位 hex 的 alpha 转为 @ 浮点透明度（0-1）。
    审计 P3 修复：非法颜色（命名颜色/空串等）不再静默透传，回退白色，
    避免 drawtext 因无法解析颜色而整条滤镜报错。
    """
    c = (color or "").strip()
    if c.startswith("0x"):
        return c
    if c.startswith("#") and len(c) in (7, 9):
        rgb, aa = c[1:7], c[7:9]
        if len(c) == 9:
            return f"0x{rgb}@{int(aa, 16) / 255.0:.3f}"
        return f"0x{rgb}"
    return "0xFFFFFF"


def escape_drawtext_text(text: str) -> str:
    """将任意用户文本转义为可安全嵌入 drawtext=text='...' 的滤镜字符串。

    审计 P0 修复，规则经 ffmpeg 8.x 实机渲染校准（tests/test_drawtext_escape.py）：
    - 换行：drawtext 单行渲染，\\r\\n / \\n / \\r 一律替换为空格；
    - 反斜杠：filtergraph 层与 drawtext 文本扩展层各消费一层 → 1 个变 4 个；
    - 百分号：% 触发文本扩展（裸 % 直接导致文本消失/报错）→ 滤镜串 \\%；
    - 冒号：选项分隔符，引号不保护 → \\:；
    - 单引号：实测 '\\'' / '' / \\' 均无法正确渲染（空白或解析错误），
      统一替换为视觉等价的 ’（U+2019）；
    - 逗号/分号/方括号：引号内受保护，无需转义。
    """
    t = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    t = t.replace("\\", "\\\\\\\\")
    t = t.replace("%", "\\\\%")
    t = t.replace(":", "\\:")
    t = t.replace("'", "’")
    return t


def color_to_ass(color: str | None) -> str:
    """将 schema 颜色 `#RRGGBB` / `#RRGGBBAA` 转为 ASS 颜色 `&HAABBGGRR`。

    ASS 用 ``&HAABBGGRR``：AA 为透明度（00 = 完全不透明，FF = 全透明），
    BBGGRR 为 BGR 字节序（与 CSS 的 RGB 顺序相反）。8 位 hex 的 AA 直接映射。
    非 `#` 前缀或非法长度原样返回。
    """
    c = (color or "").strip()
    if not c.startswith("#") or len(c) not in (7, 9):
        return c
    rgb, aa = c[1:7], c[7:9] if len(c) == 9 else "00"
    r, g, b = rgb[0:2], rgb[2:4], rgb[4:6]
    return f"&H{aa.upper()}{b.upper()}{g.upper()}{r.upper()}"


# position → ASS 对齐码（数字键盘布局）。
_ASS_ALIGNMENT = {
    "bottom": 2, "top": 8, "center": 5, "left": 4, "right": 6,
    "bottom_left": 1, "bottom_right": 3, "top_left": 7, "top_right": 9,
}


def ass_alignment(position: str | None) -> int:
    """把 TextStyle.position 映射为 ASS Alignment 码。

    bottom→2, top→8, center→5, left→4, right→6, bottom_left→1,
    bottom_right→3, top_left→7, top_right→9；未知回退 bottom(2)。
    """
    return _ASS_ALIGNMENT.get(position or "", 2)


def ass_time(sec: float) -> str:
    """秒 → ASS 时间 `H:MM:SS.cc`（厘秒精度）。"""
    cs = max(0, int(round(float(sec) * 100)))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, c = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


@dataclass
class TextStyle:
    """文字叠加样式 — 转为 FFmpeg drawtext filter 参数。

    字段名与 schema/timeline.py Clip 字幕样式字段（任务 28）对齐。
    glow 双通道由 render.py 实现（本文件只输出主文本参数）。
    """

    font_size: int = 48
    font_color: str = "#ffffff"
    font: str = ""  # 字幕字体族（clip.font / 前端可选）；空 = 安全默认 MSYH
    stroke_width: float = 0
    stroke_color: str = "#000000"
    position: str = "bottom"
    offset_y: int = 0
    box: bool = False
    box_color: str = "#00000080"
    box_border: int = 8
    shadow_x: float = 0
    shadow_y: float = 0
    shadow_color: str = "#00000080"
    # 任务 30 新增字段
    font_weight: str = "normal"
    font_italic: bool = False
    letter_spacing: float = 0  # drawtext 无此参数：字段保留，滤镜串不输出（前端生效）
    shadow_blur: float = 0  # ffmpeg drawtext 无阴影模糊：尽力而为 = 忽略 blur，仅偏移+颜色
    glow_color: str = ""  # 空 = 不发光
    glow_width: float = 0

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "TextStyle":
        """从字典构造 TextStyle。"""
        if not d:
            return cls()
        return cls(
            font_size=d.get("font_size", 48),
            font_color=d.get("font_color", "#ffffff"),
            font=d.get("font", ""),
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
            shadow_blur=d.get("shadow_blur", 0),
            font_weight=d.get("font_weight", "normal"),
            font_italic=d.get("font_italic", False),
            letter_spacing=d.get("letter_spacing", 0),
            glow_color=d.get("glow_color", ""),
            glow_width=d.get("glow_width", 0),
        )

    @staticmethod
    def _ass_fontname(font: str) -> str:
        """前端字体名 → ASS Fontname（清洗非法字符，空值回退 MSYH）。

        ASS Fontname 不支持逗号（那是样式行分隔符），且 libass/fontconfig
        在 Windows 上按系统注册的字体族名匹配——前端可选 'PingFang SC' /
        'Microsoft YaHei' / 'Noto Sans SC' 等，直接可用；含引号/逗号时剥离。
        """
        if not font:
            return "MSYH"
        cleaned = font.replace('"', "").replace("'", "").replace(",", " ").strip()
        return cleaned or "MSYH"

    def build_ass_style(self, play_res_x: int = 1920, play_res_y: int = 1080) -> str:
        """构建 ASS `[Script Info]` + `[V4+ Styles]` 段。

        Fontname←font（_ass_fontname 清洗，空回退 "MSYH"）——修复 ASS 字体
        硬编码缺陷：前端选择的字体族现在真实传入成片。libass/fontconfig 负责
        系统字体解析，非系统字体（如思源黑体）依赖本机注册；drawtext 回退
        路径的 _resolve_system_font 仍按需复制字体文件。
        14 个样式字段映射（与 drawtext 路径语义 1:1，drawtext 无法表达的
        shadow_blur / font_italic / letter_spacing / 9 点对齐 / glow 在此全部生效）：
          Fontsize←font_size / Primary←font_color / Secondary=Primary /
          OutlineColour←stroke_color / BackColour←shadow_color /
          Bold←font_weight=="bold" / Italic←font_italic /
          Spacing←letter_spacing（四舍五入）/ Outline 宽←stroke_width（四舍五入）/
          Shadow←shadow_x/y/blur 任一非零 / Alignment←position。
        """
        primary = color_to_ass(self.font_color)
        outline = color_to_ass(self.stroke_color or "#000000")
        back = color_to_ass(self.shadow_color)
        bold = -1 if self.font_weight == "bold" else 0
        italic = -1 if self.font_italic else 0
        spacing = int(round(self.letter_spacing))
        outline_w = int(round(self.stroke_width))
        shadow = 1 if (self.shadow_x or self.shadow_y or self.shadow_blur) else 0
        align = ass_alignment(self.position)
        fontname = self._ass_fontname(self.font)
        return (
            "[Script Info]\n"
            "ScriptType: v4.00+\n"
            f"PlayResX: {int(play_res_x)}\n"
            f"PlayResY: {int(play_res_y)}\n"
            "ScaledBorderAndShadow: yes\n"
            "\n"
            "[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            f"Style: Default,{fontname},{self.font_size},{primary},{primary},{outline},{back},"
            f"{bold},{italic},0,0,100,100,{spacing},0,1,{outline_w},{shadow},{align},10,10,10,1"
        )

    def build_ass_dialogue(self, text: str, start_sec: float, end_sec: float) -> str:
        """构建 ASS `Dialogue:` 行。

        start_sec/end_sec 已由调用方裁剪到成片实际时长（render._apply_text_ass），
        此处只负责格式化。时间格式 H:MM:SS.cc（厘秒）。
        override tags：\\an 对齐 / \\i1 斜体 / \\fsp 字距 / \\blur 阴影模糊 /
        \\bord 描边宽 / glow（\\bord+\\blur+\\c，libass 无原生外发光的双通道模拟）。
        """
        tags = [rf"\an{ass_alignment(self.position)}"]
        if self.font_italic:
            tags.append(r"\i1")
        if self.letter_spacing != 0:
            tags.append(rf"\fsp{int(round(self.letter_spacing))}")
        if self.shadow_blur > 0:
            tags.append(rf"\blur{self.shadow_blur:g}")
        if self.shadow_x or self.shadow_y or self.shadow_blur:
            tags.append(r"\shad1")
        if self.stroke_width > 0:
            tags.append(rf"\bord{int(round(self.stroke_width))}")
        if self.glow_width > 0 and self.glow_color:
            gw = int(round(self.glow_width))
            tags.append(rf"\bord{gw}\blur{gw}")
            tags.append(rf"\c{color_to_ass(self.glow_color)}")
        esc = text.replace("{", r"\{").replace("}", r"\}")
        # F3 实渲修复: ASS override tags 必须包裹在 {} 内才被 libass 当作样式解释，
        # 否则 \an2\i1... 会作为字面文本渲染进画面。
        tags_block = "{" + "".join(tags) + "}" if tags else ""
        return f"Dialogue: 0,{ass_time(start_sec)},{ass_time(end_sec)},Default,,0,0,0,,{tags_block}{esc}"

    def drawtext_position(self) -> tuple[str, str]:
        """返回当前 position 的 x/y 表达式（供 render.py glow 双通道复用坐标）。"""
        pos_map = {
            "center": ("(w-text_w)/2", "(h-text_h)/2"),
            "bottom": ("(w-text_w)/2", f"h-text_h-20-{self.offset_y}"),
            "top": ("(w-text_w)/2", f"20+{self.offset_y}"),
            "left": ("20", "(h-text_h)/2"),
            "right": (f"w-text_w-20", "(h-text_h)/2"),
        }
        return pos_map.get(self.position, pos_map["bottom"])

    def build_drawtext_filter(self, text: str, start_sec: float, duration_sec: float, font_file: str = "") -> str:
        """构建 FFmpeg drawtext filter 字符串（主文本通道；glow 由 render.py 双通道实现）。"""
        safe = escape_drawtext_text(text)
        font_arg = f":fontfile={font_file}" if font_file and Path(font_file).exists() else ""

        xp, yp = self.drawtext_position()

        parts = [
            f"drawtext=text='{safe}'{font_arg}",
            f"fontsize={self.font_size}",
            f"fontcolor={color_to_drawtext(self.font_color)}",
            f"x={xp}",
            f"y={yp}",
        ]

        if self.stroke_width > 0:
            parts.append(f"bordercolor={color_to_drawtext(self.stroke_color)}")
            parts.append(f"borderw={self.stroke_width}")

        if self.box:
            parts.append(f"box=1:boxcolor={color_to_drawtext(self.box_color)}:boxborderw={self.box_border}")

        if self.shadow_x != 0 or self.shadow_y != 0:
            parts.append(f"shadowx={self.shadow_x}:shadowy={self.shadow_y}:shadowcolor={color_to_drawtext(self.shadow_color)}")

        parts.append(f"enable='between(t,{start_sec},{start_sec + duration_sec})'")
        return ":".join(parts)
