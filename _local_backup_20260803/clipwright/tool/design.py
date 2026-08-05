"""视觉 LLM 接口 + 文字设计工具。支持 LLM 调用设计文字样式。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Optional

from clipwright.config import logger
from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.tool.base import BaseTool


class VisionLLMTool(BaseTool):
    """视觉 LLM 通用接口 — 输入文本+图片，输出结构化分析。"""
    name = "vision_llm"
    description = "发送文本和图片到多模态 LLM，获取结构化响应，用于画面分析/描述/分类"
    dependencies = []

    async def execute(
        self,
        prompt: str = "",
        image_path: str = "",
        output_schema: str = "text",
        **kwargs: Any,
    ) -> ToolExecResult:
        """执行视觉 LLM 调用。

        Args:
            prompt: 文本提示词
            image_path: 图片路径（支持本地和 URL）
            output_schema: 输出格式 text / json
        """
        if not image_path:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error="image_path required")

        try:
            from clipwright.services.vision import ImageAnalyzer
            analyzer = ImageAnalyzer()
            result = await analyzer.analyze_image(image_path)
            description = result.get("description", "") or str(result.get("tags", []))
            tags = result.get("tags", [])

            if output_schema == "json":
                import re
                try:
                    parsed = json.loads(description)
                except (json.JSONDecodeError, TypeError):
                    parsed = {"description": description, "tags": tags}
                return ToolExecResult(
                    status=ToolStatus.SUCCESS, tool_name=self.name,
                    output=parsed,
                )

            return ToolExecResult(
                status=ToolStatus.SUCCESS, tool_name=self.name,
                output={"description": description, "tags": tags, "prompt": prompt[:100]},
            )
        except Exception as e:
            logger.error("VisionLLM 调用失败: %s", e)
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error=str(e))


class TextStyle:
    """文字样式配置 — 所有参数可被 LLM 调用填充。"""
    def __init__(
        self,
        font_family: str = "sans-serif",
        font_file: str = "",
        font_size: int = 48,
        font_color: str = "#ffffff",
        font_weight: str = "normal",  # normal / bold
        position: str = "center",     # center / top / bottom / left / right
        offset_x: int = 0,
        offset_y: int = 0,
        stroke_color: str = "#000000",
        stroke_width: int = 0,
        glow: bool = False,
        glow_color: str = "#ffffff",
        glow_radius: int = 10,
        shadow: bool = False,
        shadow_color: str = "#000000",
        shadow_offset_x: int = 2,
        shadow_offset_y: int = 2,
        opacity: float = 1.0,
        background_color: str = "",
        background_opacity: float = 0.0,
        letter_spacing: float = 0.0,
        line_height: float = 1.2,
        animation: str = "none",      # none / fade_in / typewriter / slide_up
    ):
        self.font_family = font_family
        self.font_file = font_file or self._default_font()
        self.font_size = font_size
        self.font_color = font_color
        self.font_weight = font_weight
        self.position = position
        self.offset_x = offset_x
        self.offset_y = offset_y
        self.stroke_color = stroke_color
        self.stroke_width = stroke_width
        self.glow = glow
        self.glow_color = glow_color
        self.glow_radius = glow_radius
        self.shadow = shadow
        self.shadow_color = shadow_color
        self.shadow_offset_x = shadow_offset_x
        self.shadow_offset_y = shadow_offset_y
        self.opacity = opacity
        self.background_color = background_color
        self.background_opacity = background_opacity
        self.letter_spacing = letter_spacing
        self.line_height = line_height
        self.animation = animation

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}

    @classmethod
    def from_dict(cls, d: dict) -> TextStyle:
        return cls(**{k: v for k, v in d.items() if hasattr(cls, k)})

    def build_drawtext_filter(self, text: str, start_sec: float, duration_sec: float) -> str:
        """生成 FFmpeg drawtext filter 字符串，包含所有效果。"""
        # 字体文件
        font_opt = f":fontfile={self.font_file.replace(chr(92), '/').replace(':', '\\\\:')}" if self.font_file else ""

        # 位置计算
        pos_map = {
            "center": "x=(w-text_w)/2:y=(h-text_h)/2",
            "top": "x=(w-text_w)/2:y=20",
            "bottom": "x=(w-text_w)/2:y=h-text_h-20",
            "left": "x=20:y=(h-text_h)/2",
            "right": "x=w-text_w-20:y=(h-text_h)/2",
            "top_left": "x=20:y=20",
            "top_right": "x=w-text_w-20:y=20",
            "bottom_left": "x=20:y=h-text_h-20",
            "bottom_right": "x=w-text_w-20:y=h-text_h-20",
        }
        pos_str = pos_map.get(self.position, "x=(w-text_w)/2:y=(h-text_h)/2")
        if self.offset_x or self.offset_y:
            pos_str += f":x={pos_str.split(':')[0].split('=')[1]}+{self.offset_x}" if self.offset_x else ""
            pos_str += f":y={pos_str.split(':')[1].split('=')[1]}+{self.offset_y}" if self.offset_y else ""

        # 基础参数
        safe_text = text.replace("'", "'\\''").replace(":", "\\:")
        parts = [
            f"drawtext=text='{safe_text}'",
            f"fontsize={self.font_size}",
            f"fontcolor={self.font_color}@{self.opacity}",
            f"{''.join(pos_str.split(':')[1:]) if 'x=' in pos_str else pos_str}",
            f":enable='between(t,{start_sec},{start_sec + duration_sec})'",
        ]
        if font_opt:
            parts.append(font_opt)

        # 描边
        if self.stroke_width > 0:
            parts.append(f":borderw={self.stroke_width}:bordercolor={self.stroke_color}")

        # 阴影（用 drawtext 的 shadowx/shadowy）
        if self.shadow:
            parts.append(f":shadowx={self.shadow_offset_x}:shadowy={self.shadow_offset_y}:shadowcolor={self.shadow_color}")

        # 发光：用两个重叠的 drawtext 模拟
        # （在 filter 层面通过多层实现，这里返回基础 filter）
        return ":".join(parts)

    @staticmethod
    def _default_font() -> str:
        if os.name == "nt":
            for fp in [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\SimSun.ttc", r"C:\Windows\Fonts\Deng.ttf"]:
                if Path(fp).exists():
                    return fp
        for fp in ["/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
                    "/System/Library/Fonts/PingFang.ttc",
                    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"]:
            if Path(fp).exists():
                return fp
        return ""


class TextDesignTool(BaseTool):
    """文字设计工具 — LLM 可调用此工具设计文字样式，返回完整样式配置。"""
    name = "text_design"
    description = "设计文字样式：字体/颜色/大小/位置/描边/发光/阴影/动画，返回完整配置"
    dependencies = []

    async def execute(
        self,
        text: str = "",
        style_description: str = "",
        font_family: str = "",
        font_size: int = 48,
        font_color: str = "#ffffff",
        position: str = "center",
        stroke_width: int = 0,
        stroke_color: str = "#000000",
        glow: bool = False,
        shadow: bool = False,
        animation: str = "none",
        **kwargs: Any,
    ) -> ToolExecResult:
        """设计文字样式。

        Args:
            text: 文字内容
            style_description: 自然语言风格描述（如："金色粗体居中，带发光效果，适合片头标题"）
            font_size: 字号
            font_color: 颜色 #RRGGBB
            position: center/top/bottom/left/right/top_left/top_right/bottom_left/bottom_right
            stroke_width: 描边宽度
            stroke_color: 描边颜色
            glow: 是否发光
            shadow: 是否投影
            animation: none/fade_in/typewriter/slide_up
        """
        # LLM 解析自然语言风格描述
        if style_description and not font_family:
            try:
                from clipwright.services.llm import LLMService
                llm = LLMService()
                resp = await llm.ask(
                    f"解析以下文字风格描述，提取参数。只返回 JSON，不要其他内容。\n\n"
                    f"描述: {style_description}\n\n"
                    f"JSON 格式:\n"
                    f"{{\n"
                    f'  "font_size": 字号(24-120),\n'
                    f'  "font_color": "十六进制颜色 #RRGGBB",\n'
                    f'  "position": "center/top/bottom",\n'
                    f'  "stroke_width": 描边宽度(0-5),\n'
                    f'  "glow": true/false,\n'
                    f'  "shadow": true/false,\n'
                    f'  "animation": "none/fade_in/typewriter/slide_up"\n'
                    f"}}"
                )
                if resp.success and resp.content:
                    import json
                    content = resp.content.strip()
                    if content.startswith("```"):
                        lines = content.splitlines()
                        content = "\n".join(lines[1:-1])
                    parsed = json.loads(content)
                    font_size = max(font_size, parsed.get("font_size", font_size))
                    font_color = parsed.get("font_color", font_color)
                    position = parsed.get("position", position)
                    stroke_width = max(stroke_width, parsed.get("stroke_width", stroke_width))
                    glow = parsed.get("glow", glow)
                    shadow = parsed.get("shadow", shadow)
                    animation = parsed.get("animation", animation)
            except Exception as e:
                logger.debug("TextDesignTool LLM 解析失败: %s", e)

        style = TextStyle(
            font_family=font_family or "sans-serif",
            font_size=font_size,
            font_color=font_color,
            position=position,
            stroke_width=stroke_width,
            stroke_color=stroke_color,
            glow=glow,
            shadow=shadow,
            animation=animation,
        )

        return ToolExecResult(
            status=ToolStatus.SUCCESS,
            tool_name=self.name,
            output={
                "text": text[:80],
                "style": style.to_dict(),
                "drawtext_filter_preview": style.build_drawtext_filter(text[:20], 0, 5),
            },
        )


class VideoFilterTool(BaseTool):
    """视频滤镜工具 — 调整色调/亮度/对比度/饱和度/位置。"""
    name = "video_filter"
    description = "调整视频画面的色调/亮度/对比度/饱和度/裁切位置等"
    dependencies = ["ffmpeg"]

    async def execute(
        self,
        input_path: str = "",
        brightness: float = 0.0,
        contrast: float = 1.0,
        saturation: float = 1.0,
        hue: float = 0.0,
        gamma: float = 1.0,
        crop_x: int = 0,
        crop_y: int = 0,
        crop_w: int = 0,
        crop_h: int = 0,
        rotate: float = 0.0,
        output_path: Optional[str] = None,
        **kwargs: Any,
    ) -> ToolExecResult:
        """调整视频画面。

        Args:
            input_path: 输入视频路径
            brightness: 亮度 (-1.0 ~ 1.0, 0=不变)
            contrast: 对比度 (0.0 ~ 3.0, 1.0=不变)
            saturation: 饱和度 (0.0 ~ 3.0, 1.0=不变)
            hue: 色调偏移度 (-180 ~ 180)
            gamma: 伽马值 (0.1 ~ 5.0)
            crop_x/crop_y/crop_w/crop_h: 裁切区域
            rotate: 旋转角度
        """
        out = output_path or Path(tempfile.mktemp(suffix=".mp4")).name
        if not Path(input_path).exists():
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error="file not found")

        try:
            # 构建 filter 链
            filters = []
            if brightness != 0 or contrast != 1.0 or saturation != 1.0:
                filters.append(f"eq=brightness={brightness}:contrast={contrast}:saturation={saturation}:gamma={gamma}")
            if hue != 0:
                filters.append(f"hue=h={hue}")
            if crop_w > 0 and crop_h > 0:
                filters.append(f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y}")
            if rotate != 0:
                filters.append(f"rotate={rotate}*PI/180:fill=black")

            if not filters:
                import shutil
                shutil.copy2(input_path, out)
                return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name,
                                      output={"output_path": out, "note": "no filter applied"}, output_path=out)

            filter_str = ",".join(filters)
            cmd = [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", input_path,
                "-vf", filter_str,
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "copy", out,
            ]
            import subprocess
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                      error=f"filter error: {result.stderr[:200]}")
            return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name,
                                  output={"output_path": out}, output_path=out)
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            return ToolExecResult(status=ToolStatus.ERROR if isinstance(e, subprocess.TimeoutExpired) else ToolStatus.DEPENDENCY_MISSING,
                                  tool_name=self.name, error=str(e))
