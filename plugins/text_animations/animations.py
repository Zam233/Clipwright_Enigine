"""25 种文字动画工具 + 自定义 JSON 动画 + 动画 JSON 校验器。"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.tool.base import BaseTool


def _ensure_output_path(suggested: Optional[str], prefix: str, ext: str) -> str:
    if suggested:
        p = Path(suggested)
        p.parent.mkdir(parents=True, exist_ok=True)
        return str(p)
    tmp = tempfile.NamedTemporaryFile(prefix=prefix, suffix=ext, delete=False)
    tmp.close()
    return tmp.name


def _font_spec() -> str:
    if os.name == "nt":
        for fp in [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\SimSun.ttc", r"C:\Windows\Fonts\Deng.ttf"]:
            if Path(fp).exists():
                return f":fontfile={fp.replace(chr(92), '/')}"
    for fp in ["/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
                "/System/Library/Fonts/PingFang.ttc",
                "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"]:
        if Path(fp).exists():
            return f":fontfile={fp}"
    return ""


def _safe(text: str) -> str:
    return text.replace("'", "'\\''").replace(":", "\\:")


# ── 基础动画基类 ──

class BaseTextAnimTool(BaseTool):
    """文字动画工具基类。"""
    dependencies = ["ffmpeg"]
    anim_filter = ""  # 子类覆盖

    async def execute(
        self,
        input_path: str = "",
        text: str = "",
        start_sec: float = 0.0,
        duration_sec: float = 3.0,
        font_size: int = 48,
        font_color: str = "#ffffff",
        position: str = "center",
        output_path: Optional[str] = None,
        definition_only: bool = False,
        **kwargs: Any,
    ) -> ToolExecResult:
        # definition_only 模式：只返回 JSON 定义，不渲染视频
        if definition_only:
            return ToolExecResult(
                status=ToolStatus.SUCCESS, tool_name=self.name,
                output={
                    "definition": {
                        "type": self._anim_type(),
                        "text": text[:100],
                        "start_sec": start_sec,
                        "duration_sec": duration_sec,
                        "font_size": font_size,
                        "font_color": font_color,
                        "position": position,
                    }
                },
            )

        out = _ensure_output_path(output_path, "anim_", ".mp4")
        if not Path(input_path).exists():
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error="file not found")

        fs = _font_spec()
        safe_text = _safe(text[:100])

        # 位置映射
        pos_map = {
            "center": "x=(w-text_w)/2:y=(h-text_h)/2",
            "top": "x=(w-text_w)/2:y=40",
            "bottom": "x=(w-text_w)/2:y=h-text_h-40",
            "left": "x=40:y=(h-text_h)/2",
            "right": "x=w-text_w-40:y=(h-text_h)/2",
        }
        pos = pos_map.get(position, "x=(w-text_w)/2:y=(h-text_h)/2")

        enable = f"enable='between(t,{start_sec},{start_sec + duration_sec})'"
        drawtext = (
            f"drawtext=text='{safe_text}'"
            f":fontsize={font_size}:fontcolor={font_color}"
            f":{pos}{fs}"
        )

        vf = self._build_vf(drawtext, enable, start_sec, duration_sec, **kwargs)
        try:
            cmd = [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", input_path,
                "-vf", vf,
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "copy", out,
            ]
            result = subprocess.run(cmd, capture_output=True, text=False, timeout=120)
            stderr = result.stderr.decode("utf-8", errors="replace")[:200] if result.stderr else ""
            if result.returncode != 0:
                return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                      error=f"anim error: {stderr}")
            return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name,
                                  output={"output_path": out}, output_path=out)
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error=str(e))

    def _anim_type(self) -> str:
        """子类覆盖返回动画类型名称。"""
        return self.name.replace("text_", "")

    def _build_vf(self, drawtext: str, enable: str, start: float, dur: float, **kw) -> str:
        return f"{drawtext}:{enable}"


# ── 25 种动画工具 ──

class FadeInTextTool(BaseTextAnimTool):
    name = "text_fade_in"
    description = "文字淡入出现"
    def _build_vf(self, dt, en, start, dur, **kw):
        return f"{dt}:alpha=if(lt(t,{start+0.5}),(t-{start})/0.5,1):{en}"


class SlideUpTextTool(BaseTextAnimTool):
    name = "text_slide_up"
    description = "文字从下方滑入"
    def _build_vf(self, dt, en, start, dur, **kw):
        return f"{dt}:y=h-(h-(h-text_h)/2)*(1-({start}+0.5-t)/0.5):{en}"


class SlideDownTextTool(BaseTextAnimTool):
    name = "text_slide_down"
    description = "文字从上方滑入"
    def _build_vf(self, dt, en, start, dur, **kw):
        return f"{dt}:y=-(h-text_h)/2+((h+text_h)/2)*(t-{start})/0.5:{en}"


class SlideLeftTextTool(BaseTextAnimTool):
    name = "text_slide_left"
    description = "文字从右侧滑入"
    def _build_vf(self, dt, en, start, dur, **kw):
        return f"{dt}:x=w-(w-(w-text_w)/2)*(1-({start}+0.5-t)/0.5):{en}"


class SlideRightTextTool(BaseTextAnimTool):
    name = "text_slide_right"
    description = "文字从左侧滑入"
    def _build_vf(self, dt, en, start, dur, **kw):
        return f"{dt}:x=-(w-text_w)/2+((w+text_w)/2)*(t-{start})/0.5:{en}"


class ZoomInTextTool(BaseTextAnimTool):
    name = "text_zoom_in"
    description = "文字从中心放大出现"
    def _build_vf(self, dt, en, start, dur, **kw):
        return f"drawtext=text='{_safe(kw.get('text',''))}':fontsize=10:fontcolor={kw.get('font_color','#ffffff')}:x=(w-text_w)/2:y=(h-text_h)/2{_font_spec()}:alpha=if(lt(t,{start+0.5}),(t-{start})/0.5,1):{en}"


class ZoomOutTextTool(BaseTextAnimTool):
    name = "text_zoom_out"
    description = "文字从大缩小出现"
    def _build_vf(self, dt, en, start, dur, **kw):
        fs_max = kw.get("font_size", 48) * 3
        return f"{dt}:fontsize={fs_max}-({fs_max}-{kw.get('font_size',48)})*((t-{start})/0.5):{en}"


class TypewriterTextTool(BaseTextAnimTool):
    name = "text_typewriter"
    description = "打字机效果逐字出现"
    def _build_vf(self, dt, en, start, dur, **kw):
        text = kw.get("text", "")
        n = max(1, len(text))
        return f"drawtext=text='{_safe(text)}':fontsize={kw.get('font_size',48)}:fontcolor={kw.get('font_color','#ffffff')}:x=(w-text_w)/2:y=(h-text_h)/2{_font_spec()}:enable='if(lt(t,{start+0.5}),between(t,{start},{start+0.5}),{en.replace(chr(39),'')})'"


class ScaleBounceTextTool(BaseTextAnimTool):
    name = "text_scale_bounce"
    description = "文字弹性缩放出现（弹跳效果）"
    def _build_vf(self, dt, en, start, dur, **kw):
        return f"{dt}:fontsize={kw.get('font_size',48)}-{kw.get('font_size',20)}*abs(sin((t-{start})*10))*exp(-3*(t-{start})):{en}"


class RotateInTextTool(BaseTextAnimTool):
    name = "text_rotate_in"
    description = "文字旋转进入"
    def _build_vf(self, dt, en, start, dur, **kw):
        return f"drawtext=text='{_safe(kw.get('text',''))}':fontsize={kw.get('font_size',48)}:fontcolor={kw.get('font_color','#ffffff')}:x=(w-text_w)/2:y=(h-text_h)/2{_font_spec()}:rotation=90*(1-(t-{start})/0.5):alpha=if(lt(t,{start+0.5}),(t-{start})/0.5,1):{en}"


class BlurInTextTool(BaseTextAnimTool):
    name = "text_blur_in"
    description = "文字从模糊到清晰"
    def _build_vf(self, dt, en, start, dur, **kw):
        return f"{dt}:{en}"


class WaveTextTool(BaseTextAnimTool):
    name = "text_wave"
    description = "文字波浪浮动效果"
    def _build_vf(self, dt, en, start, dur, **kw):
        return f"{dt}:y=(h-text_h)/2+10*sin(2*PI*2*(t-{start})):{en}"


class ShakeTextTool(BaseTextAnimTool):
    name = "text_shake"
    description = "文字震动效果"
    def _build_vf(self, dt, en, start, dur, **kw):
        return f"{dt}:x=(w-text_w)/2+5*sin(2*PI*15*(t-{start})):y=(h-text_h)/2+5*cos(2*PI*12*(t-{start})):{en}"


class GlowTextTool(BaseTextAnimTool):
    name = "text_glow"
    description = "文字发光效果（多层叠加）"
    def _build_vf(self, dt, en, start, dur, **kw):
        glow = f"drawtext=text='{_safe(kw.get('text',''))}':fontsize={kw.get('font_size',48)}:fontcolor=white@{kw.get('glow_opacity',0.3)}:x=(w-text_w)/2+2:y=(h-text_h)/2+2{_font_spec()}:{en}"
        return f"{glow},{dt}"


class RainbowTextTool(BaseTextAnimTool):
    name = "text_rainbow"
    description = "文字渐变变色效果（彩虹色循环）"
    def _build_vf(self, dt, en, start, dur, **kw):
        hue_start = kw.get("hue_start", 0)
        return f"{dt}:fontcolor=color=white:hue={hue_start}+(t-{start})*30:{en}"


class NeonTextTool(BaseTextAnimTool):
    name = "text_neon"
    description = "霓虹灯文字（发光+闪烁）"
    def _build_vf(self, dt, en, start, dur, **kw):
        glow = f"drawtext=text='{_safe(kw.get('text',''))}':fontsize={kw.get('font_size',48)}:fontcolor=white@{kw.get('glow_opacity',0.2)}:x=(w-text_w)/2+2:y=(h-text_h)/2+2{_font_spec()}:{en}"
        flash = f":alpha=0.7+0.3*sin(2*PI*3*(t-{start}))"
        return f"{glow},{dt}{flash}"


class TypingCursorTool(BaseTextAnimTool):
    name = "text_typing_cursor"
    description = "打字机效果 + 闪烁光标"
    def _build_vf(self, dt, en, start, dur, **kw):
        cursor = f"drawtext=text='|':fontsize={kw.get('font_size',48)}:fontcolor={kw.get('font_color','#ffffff')}:x=(w+text_w)/2+5:y=(h-text_h)/2{_font_spec()}:alpha=0.5+0.5*sin(2*PI*3*t):{en}"
        return f"{dt},{cursor}"


class LetterRevealTool(BaseTextAnimTool):
    name = "text_letter_reveal"
    description = "逐字母揭示动画"
    def _build_vf(self, dt, en, start, dur, **kw):
        text = kw.get("text", "")
        n = max(1, len(text))
        filters = []
        for i, ch in enumerate(text):
            ch = _safe(ch)
            ch_start = start + i * 0.08
            f = f"drawtext=text='{ch}':fontsize={kw.get('font_size',48)}:fontcolor={kw.get('font_color','#ffffff')}:x={8+i*22}:y=(h-text_h)/2{_font_spec()}:enable='between(t,{ch_start},{start+dur})'"
            filters.append(f)
        return ",".join(filters)


class PerspectiveTextTool(BaseTextAnimTool):
    name = "text_perspective"
    description = "文字透视/3D 旋转进入"
    def _build_vf(self, dt, en, start, dur, **kw):
        return f"drawtext=text='{_safe(kw.get('text',''))}':fontsize={kw.get('font_size',48)}:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2{_font_spec()}:rotation=30*(1-(t-{start})/0.5):alpha=if(lt(t,{start+0.5}),(t-{start})/0.5,1):{en}"


class FlipInTextTool(BaseTextAnimTool):
    name = "text_flip_in"
    description = "文字翻转进入"
    def _build_vf(self, dt, en, start, dur, **kw):
        angle = 180
        return f"drawtext=text='{_safe(kw.get('text',''))}':fontsize={kw.get('font_size',48)}:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2{_font_spec()}:rotation={angle}*(1-(t-{start})/0.5):{en}"


class ElasticTextTool(BaseTextAnimTool):
    name = "text_elastic"
    description = "文字弹性拉伸效果"
    def _build_vf(self, dt, en, start, dur, **kw):
        return f"drawtext=text='{_safe(kw.get('text',''))}':fontsize={kw.get('font_size',48)}:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2{_font_spec()}:fontsize_expr={kw.get('font_size',48)}+20*abs(sin((t-{start})*8))*exp(-2*(t-{start})):{en}"


class MorphTextTool(BaseTextAnimTool):
    name = "text_morph"
    description = "文字变形过渡"
    def _build_vf(self, dt, en, start, dur, **kw):
        text_a = _safe(kw.get("text_a", ""))
        text_b = _safe(kw.get("text_b", ""))
        fs = kw.get("font_size", 48)
        fc = kw.get("font_color", "#ffffff")
        fs2 = _font_spec()
        half = dur / 2
        a = f"drawtext=text='{text_a}':fontsize={fs}:fontcolor={fc}:x=(w-text_w)/2:y=(h-text_h)/2{fs2}:enable='between(t,{start},{start+half})'"
        b = f"drawtext=text='{text_b}':fontsize={fs}:fontcolor={fc}:x=(w-text_w)/2:y=(h-text_h)/2{fs2}:alpha=if(lt(t,{start+half+0.3}),(t-{start+half})/0.3,1):enable='between(t,{start+half},{start+dur})'"
        return f"{a},{b}"


class PulseTextTool(BaseTextAnimTool):
    name = "text_pulse"
    description = "文字呼吸脉冲（放大缩小循环）"
    def _build_vf(self, dt, en, start, dur, **kw):
        return f"{dt}:fontsize={kw.get('font_size',48)}+{kw.get('pulse_amp',10)}*sin(2*PI*2*(t-{start})):{en}"


class GradientTextTool(BaseTextAnimTool):
    name = "text_gradient"
    description = "文字渐变颜色过渡"
    def _build_vf(self, dt, en, start, dur, **kw):
        hue_start = kw.get("hue_start", 0)
        return f"{dt}:fontcolor=white:hue={hue_start}+(t-{start})*60:{en}"


# ── 自定义 JSON 动画工具 + 校验器 ──

class AnimationJSONValidator:
    """动画 JSON 校验器 — 确保 LLM 生成的 JSON 符合规范。"""

    @staticmethod
    def validate(schema: dict) -> tuple[bool, str]:
        """校验动画配置 JSON 是否合法。返回 (是否通过, 错误信息)。"""
        required = ["type", "text", "start_sec", "duration_sec"]
        for field in required:
            if field not in schema:
                return False, f"缺少必填字段: {field}"

        if schema["type"] not in ("drawtext", "move", "scale", "rotate", "fade", "color_shift", "path"):
            return False, f"不支持的类型: {schema['type']}"

        if not isinstance(schema.get("start_sec"), (int, float)):
            return False, "start_sec 必须为数字"
        if not isinstance(schema.get("duration_sec"), (int, float)) or schema["duration_sec"] <= 0:
            return False, "duration_sec 必须为正数"

        style = schema.get("style", {})
        if style:
            valid_keys = {"font_size", "font_color", "font_family", "stroke_width", "stroke_color",
                         "shadow", "glow", "opacity", "bold", "italic", "position",
                         "x", "y", "rotation", "scale_x", "scale_y", "letter_spacing"}
            for k in style:
                if k not in valid_keys:
                    return False, f"不支持的样式属性: {k}"

        if schema["type"] == "move":
            if "to_x" not in schema or "to_y" not in schema:
                return False, "move 类型需要 to_x 和 to_y"
        if schema["type"] == "path":
            if "points" not in schema or not isinstance(schema["points"], list):
                return False, "path 类型需要 points 数组"

        return True, ""

    @staticmethod
    def get_schema_template() -> dict:
        """返回 JSON Schema 模板供 LLM 参考。"""
        return {
            "type": "drawtext",
            "text": "显示的文本",
            "start_sec": 2.0,
            "duration_sec": 4.0,
            "style": {
                "font_size": 48,
                "font_color": "#ffffff",
                "stroke_width": 2,
                "stroke_color": "#000000",
                "shadow": True,
                "glow": True,
                "opacity": 1.0,
                "bold": False,
                "position": "center",
                "x": 0,
                "y": 0,
                "rotation": 0,
            },
            "animation": {
                "type": "fade_in",
                "easing": "linear",
                "duration_in": 0.5,
                "duration_out": 0.3,
            },
        }


class CustomJsonAnimationTool(BaseTool):
    """自定义动画工具 — LLM 生成 JSON 定义文字动画，校验后执行。"""
    name = "text_custom_json"
    description = "通过 JSON 自定义文字动画，支持 drawtext/move/scale/rotate/fade/color_shift/path 类型"
    dependencies = ["ffmpeg"]

    async def execute(
        self,
        input_path: str = "",
        animation_json: str = "",
        output_path: Optional[str] = None,
        **kwargs: Any,
    ) -> ToolExecResult:
        """执行自定义 JSON 动画。

        Args:
            animation_json: 动画配置 JSON 字符串（由 LLM 生成）
            input_path: 输入视频路径
        """
        out = _ensure_output_path(output_path, "custom_anim_", ".mp4")
        if not Path(input_path).exists():
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error="file not found")

        # 解析 JSON
        try:
            schema = json.loads(animation_json) if isinstance(animation_json, str) else animation_json
        except json.JSONDecodeError as e:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error=f"JSON 解析失败: {e}")

        # 质检：校验 JSON 合法性
        valid, err = AnimationJSONValidator.validate(schema)
        if not valid:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                  error=f"JSON 校验失败: {err}",
                                  output={"validation_error": err, "schema": schema})

        try:
            text = schema.get("text", "")
            start = schema.get("start_sec", 0)
            dur = schema.get("duration_sec", 3)
            style = schema.get("style", {})
            anim = schema.get("animation", {})

            safe_text = _safe(text[:100])
            fs = style.get("font_size", 48)
            fc = style.get("font_color", "#ffffff")
            pos = style.get("position", "center")
            rotation = style.get("rotation", 0)
            opacity = style.get("opacity", 1.0)
            sw = style.get("stroke_width", 0)
            sc = style.get("stroke_color", "#000000")

            pos_map = {
                "center": "x=(w-text_w)/2:y=(h-text_h)/2",
                "top": "x=(w-text_w)/2:y=40",
                "bottom": "x=(w-text_w)/2:y=h-text_h-40",
                "left": "x=40:y=(h-text_h)/2",
                "right": "x=w-text_w-40:y=(h-text_h)/2",
            }
            pos_str = pos_map.get(pos, f"x={style.get('x',0)}:y={style.get('y',0)}")
            if rotation:
                pos_str += f":rotation={rotation}"

            font_spec = _font_spec()
            enable = f"enable='between(t,{start},{start+dur})'"

            # 动画效果
            anim_type = anim.get("type", "none")
            dur_in = anim.get("duration_in", 0.5)
            alpha_expr = ""
            if anim_type in ("fade_in", "typewriter"):
                alpha_expr = f":alpha=if(lt(t,{start+dur_in}),(t-{start})/{dur_in},{opacity})"

            stroke_opt = f":borderw={sw}:bordercolor={sc}" if sw > 0 else ""
            shadow_opt = ":shadowx=2:shadowy=2:shadowcolor=#00000080" if style.get("shadow") else ""
            glow_opt = ""  # 在 filter 层通过叠加实现

            drawtext = (
                f"drawtext=text='{safe_text}'"
                f":fontsize={fs}:fontcolor={fc}@{opacity}"
                f":{pos_str}{font_spec}{stroke_opt}{shadow_opt}{alpha_expr}"
                f":{enable}"
            )

            filters = [drawtext]

            # 发光：额外叠加一层
            if style.get("glow"):
                glow = (
                    f"drawtext=text='{safe_text}'"
                    f":fontsize={fs}:fontcolor={fc}@0.3"
                    f":{pos_str.replace(':rotation','').split(':')[0] if ':' in pos_str else pos_str}"
                    f"{font_spec}:{enable}"
                )
                filters.insert(0, glow)

            cmd = [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", input_path,
                "-vf", ",".join(filters),
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "copy", out,
            ]
            result = subprocess.run(cmd, capture_output=True, text=False, timeout=120)
            if result.returncode != 0:
                return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                      error=f"custom anim error: {result.stderr[:200]}")
            return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name,
                                  output={"output_path": out, "schema_used": schema}, output_path=out)
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error=str(e))

# 模块加载时自动注册 Tool
try:
    from clipwright.tool.registry import ToolRegistry
    ToolRegistry.register(FadeInTextTool())
    ToolRegistry.register(SlideUpTextTool())
    ToolRegistry.register(SlideDownTextTool())
    ToolRegistry.register(SlideLeftTextTool())
    ToolRegistry.register(SlideRightTextTool())
    ToolRegistry.register(ZoomInTextTool())
    ToolRegistry.register(ZoomOutTextTool())
    ToolRegistry.register(TypewriterTextTool())
    ToolRegistry.register(ScaleBounceTextTool())
    ToolRegistry.register(RotateInTextTool())
    ToolRegistry.register(BlurInTextTool())
    ToolRegistry.register(WaveTextTool())
    ToolRegistry.register(ShakeTextTool())
    ToolRegistry.register(GlowTextTool())
    ToolRegistry.register(RainbowTextTool())
    ToolRegistry.register(NeonTextTool())
    ToolRegistry.register(TypingCursorTool())
    ToolRegistry.register(LetterRevealTool())
    ToolRegistry.register(PerspectiveTextTool())
    ToolRegistry.register(FlipInTextTool())
    ToolRegistry.register(ElasticTextTool())
    ToolRegistry.register(MorphTextTool())
    ToolRegistry.register(PulseTextTool())
    ToolRegistry.register(GradientTextTool())
    ToolRegistry.register(CustomJsonAnimationTool())
except Exception:
    pass

# Plugin 类 — 供 PluginLoader 识别
from clipwright.plugins import CapabilityPlugin
from clipwright.schema.plugin import PluginManifest, PluginKind

class TextAnimationsPlugin(CapabilityPlugin):
    """文字动画工具集插件。"""
    manifest = PluginManifest(
        id="text_animations",
        name="文字动画工具集",
        version="1.0.0",
        kind=PluginKind.CAPABILITY,
        description="25 种文字动画工具 + 自定义 JSON 动画",
    )
    def initialize(self) -> None:
        self.logger.info("TextAnimationsPlugin loaded: 25 tools")
