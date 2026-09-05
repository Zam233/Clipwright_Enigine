"""高级视频效果工具集 — 转场/变速/模糊/暗角/水印。"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

from clipwright.config import logger
from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.tool.base import BaseTool


class VideoSpeedTool(BaseTool):
    """变速工具 — 支持帧插值的慢动作/快进。"""
    name = "video_speed"
    description = "视频变速：慢动作(0.25x~0.99x)或快进(1.01x~10x)，支持运动插值"
    dependencies = ["ffmpeg"]

    async def execute(
        self,
        input_path: str = "",
        speed: float = 1.0,
        interpolate: bool = False,
        output_path: Optional[str] = None,
        **kwargs: Any,
    ) -> ToolExecResult:
        out = output_path or Path(tempfile.mktemp(suffix=".mp4")).name
        if not Path(input_path).exists():
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error="file not found")
        try:
            if speed <= 0:
                return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error="speed must be > 0")
            if interpolate and speed < 1.0:
                # 带运动插值的慢动作（minterpolate）
                cmd = [
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-i", input_path,
                    "-vf", f"setpts={1/speed}*PTS,minterpolate=fps=30:mi_mode=mci",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k", "-af", f"atempo={min(speed, 2.0)}",
                    out,
                ]
            else:
                # 简单变速
                atempo = speed
                # FFmpeg atempo 范围 0.5~2.0，用多个串联
                audio_filters = []
                while atempo > 2.0:
                    audio_filters.append("atempo=2.0")
                    atempo /= 2.0
                while atempo < 0.5:
                    audio_filters.append("atempo=0.5")
                    atempo /= 0.5
                audio_filters.append(f"atempo={atempo}")

                cmd = [
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-i", input_path,
                    "-vf", f"setpts={1/speed}*PTS",
                    "-af", ",".join(audio_filters),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    out,
                ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if result.returncode != 0:
                return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                      error=f"speed error: {result.stderr[:200]}")
            return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name,
                                  output={"output_path": out, "speed": speed, "interpolated": interpolate},
                                  output_path=out)
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error=str(e))


class TransitionApplyTool(BaseTool):
    """转场渲染工具 — 在两个片段之间渲染真实转场效果。"""
    name = "transition_apply"
    description = "在两个视频片段之间渲染转场：fade/crossfade/slide/push/glitch/wipe/zoom"
    dependencies = ["ffmpeg"]

    async def execute(
        self,
        clip_a: str = "",
        clip_b: str = "",
        transition: str = "crossfade",
        duration_sec: float = 0.5,
        output_path: Optional[str] = None,
        **kwargs: Any,
    ) -> ToolExecResult:
        out = output_path or Path(tempfile.mktemp(suffix=".mp4")).name
        for path, name in [(clip_a, "clip_a"), (clip_b, "clip_b")]:
            if not Path(path).exists():
                return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                      error=f"{name} not found: {path}")
        try:
            if transition == "hard_cut":
                import shutil
                shutil.copy2(clip_a, out)
                return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name,
                                      output={"output_path": out, "transition": "hard_cut"}, output_path=out)

            t = duration_sec
            filter_map = {
                "crossfade": f"xfade=transition=fade:duration={t}:offset=1",
                "fade": f"xfade=transition=fadeblack:duration={t}:offset=1",
                "fade_to_black": f"xfade=transition=fadeblack:duration={t}:offset=1",
                "dissolve": f"xfade=transition=fade:duration={t}:offset=1",
                "slide_left": f"xfade=transition=slideleft:duration={t}:offset=1",
                "slide_right": f"xfade=transition=slideright:duration={t}:offset=1",
                "slide_up": f"xfade=transition=slideup:duration={t}:offset=1",
                "slide_down": f"xfade=transition=slidedown:duration={t}:offset=1",
                "push_left": f"xfade=transition=pushleft:duration={t}:offset=1",
                "push_right": f"xfade=transition=pushright:duration={t}:offset=1",
                "wipe_left": f"xfade=transition=wiperight:duration={t}:offset=1",
                "wipe_right": f"xfade=transition=wipeleft:duration={t}:offset=1",
                "zoom_in": f"xfade=transition=zoomin:duration={t}:offset=1",
                "pixel_dissolve": f"xfade=transition=pixelize:duration={t}:offset=1",
                "radial": f"xfade=transition=radial:duration={t}:offset=1",
                "rect": f"xfade=transition=rectcrop:duration={t}:offset=1",
                "clock": f"xfade=transition=circleopen:duration={t}:offset=1",
            }
            xfade = filter_map.get(transition, filter_map["crossfade"])

            # 获取 clip_a 的时长
            probe_a = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "json", clip_a],
                capture_output=True, text=True, timeout=10,
            )
            dur_a = 0
            if probe_a.returncode == 0:
                dur_a = float(json.loads(probe_a.stdout).get("format", {}).get("duration", 0))

            offset = max(0, dur_a - t) if dur_a > 0 else 0
            xfade = xfade.replace("offset=1", f"offset={offset}")

            cmd = [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", clip_a, "-i", clip_b,
                "-filter_complex", xfade,
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k",
                out,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if result.returncode != 0:
                return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                      error=f"transition error: {result.stderr[:200]}")
            return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name,
                                  output={"output_path": out, "transition": transition, "duration": t},
                                  output_path=out)
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error=str(e))


class VideoBlurTool(BaseTool):
    """视频模糊工具 — 高斯/动感/像素化/隐私保护。"""
    name = "video_blur"
    description = "应用视频模糊效果：gaussian/motion/pixelize/box"
    dependencies = ["ffmpeg"]

    async def execute(
        self,
        input_path: str = "",
        blur_type: str = "gaussian",
        radius: int = 5,
        output_path: Optional[str] = None,
        **kwargs: Any,
    ) -> ToolExecResult:
        out = output_path or Path(tempfile.mktemp(suffix=".mp4")).name
        if not Path(input_path).exists():
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error="file not found")
        try:
            filter_map = {
                "gaussian": f"gblur=sigma={radius}",
                "box": f"boxblur=luma_radius={radius}:luma_power=2",
                "motion": f"gblur=sigma={radius}",
                "pixelate": f"pixelize=width=32:height=32",
            }
            vf = filter_map.get(blur_type, filter_map["gaussian"])
            cmd = [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", input_path,
                "-vf", vf,
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "copy", out,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                      error=f"blur error: {result.stderr[:200]}")
            return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name,
                                  output={"output_path": out, "blur_type": blur_type}, output_path=out)
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error=str(e))


class WatermarkTool(BaseTool):
    """水印工具 — 图片/文字水印叠加。"""
    name = "watermark"
    description = "在视频上叠加图片水印或文字水印，支持位置和透明度"
    dependencies = ["ffmpeg"]

    async def execute(
        self,
        input_path: str = "",
        image_path: str = "",
        text: str = "",
        position: str = "bottom_right",
        opacity: float = 0.7,
        scale: float = 0.15,
        output_path: Optional[str] = None,
        **kwargs: Any,
    ) -> ToolExecResult:
        out = output_path or Path(tempfile.mktemp(suffix=".mp4")).name
        if not Path(input_path).exists():
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error="file not found")
        try:
            pos_map = {
                "top_left": "10:10",
                "top_right": "W-w-10:10",
                "bottom_left": "10:H-h-10",
                "bottom_right": "W-w-10:H-h-10",
                "center": "(W-w)/2:(H-h)/2",
            }
            pos = pos_map.get(position, "W-w-10:H-h-10")

            if image_path and Path(image_path).exists():
                # 图片水印
                cmd = [
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-i", input_path, "-i", image_path,
                    "-filter_complex",
                    f"[1:v]format=rgba,scale=iw*{scale}:ih*{scale}[wm];"
                    f"[0:v][wm]overlay={pos}:format=auto,format=yuv420p",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "copy", out,
                ]
            elif text:
                # 文字水印（审计 P0 修复：完整 drawtext 转义）
                from clipwright.tool.design import escape_drawtext_text
                safe_text = escape_drawtext_text(text)
                cmd = [
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-i", input_path,
                    "-vf",
                    f"drawtext=text='{safe_text}':fontsize=24:fontcolor=white@{opacity}:"
                    f"x={pos.split(':')[0]}:y={pos.split(':')[1]}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "copy", out,
                ]
            else:
                return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error="image_path or text required")

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                      error=f"watermark error: {result.stderr[:200]}")
            return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name,
                                  output={"output_path": out, "position": position}, output_path=out)
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error=str(e))


class EffectVignetteTool(BaseTool):
    """电影效果工具 — 暗角/颗粒/扫描线。"""
    name = "effect_vignette"
    description = "应用电影级视觉效果：暗角(vignette)/胶片颗粒(grain)/扫描线(scanline)"
    dependencies = ["ffmpeg"]

    async def execute(
        self,
        input_path: str = "",
        effect: str = "vignette",
        intensity: float = 0.5,
        output_path: Optional[str] = None,
        **kwargs: Any,
    ) -> ToolExecResult:
        out = output_path or Path(tempfile.mktemp(suffix=".mp4")).name
        if not Path(input_path).exists():
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error="file not found")
        try:
            effect_map = {
                "vignette": f"vignette=PI*{intensity}",
                "grain": f"noise=alls={intensity * 20}:allf=t+u",
                "scanline": f"drawbox=y=ih/2:color=black@0.05:width=iw:height=1:t=max(1,ih*0.01)",
                "sepia": ("colorchannelmixer=rr=0.393:rg=0.769:rb=0.189:gr=0.349:"
                          "gg=0.686:gb=0.168:br=0.272:bg=0.534:bb=0.131"),
                "old_film": f"vignette=PI*{intensity},noise=alls=15:allf=t+u",
            }
            vf = effect_map.get(effect, effect_map["vignette"])

            cmd = [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", input_path,
                "-vf", vf,
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "copy", out,
            ]

            # sepia/old_film 需要额外音频
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                      error=f"effect error: {result.stderr[:200]}")
            return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name,
                                  output={"output_path": out, "effect": effect}, output_path=out)
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error=str(e))


class TextDiagramTool(BaseTool):
    """文本图解工具 — 箭头/高亮/变色/动效文字，用于展示逻辑关系。"""
    name = "text_diagram"
    description = "在视频上生成文字图解动画：箭头、高亮、变色、移动文字，用于展示A→B→C逻辑关系"
    dependencies = ["ffmpeg"]

    async def execute(
        self,
        input_path: str = "",
        items: list[str] = None,
        relations: list[dict[str, Any]] = None,
        title: str = "",
        duration_sec: float = 5.0,
        start_sec: float = 0.0,
        output_path: Optional[str] = None,
        **kwargs: Any,
    ) -> ToolExecResult:
        """生成逻辑关系图解动画。

        Args:
            items: 概念列表 ["A概念", "B概念", "C概念"]
            relations: 关系描述 [{"from": 0, "to": 1, "label": "导致"}, ...]
            title: 标题文字
            duration_sec: 动画时长
        """
        out = output_path or Path(tempfile.mktemp(suffix=".mp4")).name
        if not Path(input_path).exists():
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error="file not found")

        items = items or []
        relations = relations or []
        n = len(items)
        if n == 0:
            import shutil
            shutil.copy2(input_path, out)
            return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name,
                                  output={"output_path": out}, output_path=out)

        try:
            # 用 drawtext 画文字 + 箭头(→) = 简单的 ASCII 图解
            import os as _os
            font_spec = ""
            if _os.name == "nt":
                from clipwright.services.fontconfig import FontConfig
                font_spec = FontConfig.ffmpeg_fontspec(FontConfig.get_font_path())

            filters = []
            # 标题
            if title:
                safe_title = title.replace("'", "'\\''").replace(":", "\\:")
                filters.append(
                    f"drawtext=text='{safe_title}'"
                    f":fontsize=48:fontcolor=white:x=(w-text_w)/2:y=40{font_spec}"
                    f":enable='between(t,{start_sec},{start_sec + duration_sec})'"
                )

            # 每个 item 的位置 - 水平排列
            spacing = min(200, int(1800 / max(n, 1)))
            start_x = int((1920 - (n - 1) * spacing) / 2)
            y_pos = 300

            for i, item in enumerate(items):
                x = start_x + i * spacing
                safe_item = item.replace("'", "'\\''").replace(":", "\\:")

                # item 文字
                highlight = ""
                for r in relations:
                    if r.get("highlight") == i:
                        highlight = ":fontcolor=yellow"
                        break
                color = highlight or ":fontcolor=white"

                # 淡入效果（每个 item 依次出现）
                item_start = start_sec + i * 0.3
                filters.append(
                    f"drawtext=text='{safe_item}'"
                    f":fontsize={36 + (8 if i == 0 else 0)}"
                    f"{color}:x={x}:y={y_pos}{font_spec}"
                    f":enable='between(t,{item_start},{start_sec + duration_sec})'"
                )

                # 箭头 → 到下一个
                if i < n - 1:
                    arrow_x = x + spacing // 2 - 15
                    for r in relations:
                        if r.get("from") == i and r.get("to") == i + 1:
                            label = r.get("label", "→")
                            safe_label = label.replace("'", "'\\''").replace(":", "\\:")
                            filters.append(
                                f"drawtext=text='{safe_label}'"
                                f":fontsize=24:fontcolor=#888888:x={arrow_x}:y={y_pos + 50}{font_spec}"
                                f":enable='between(t,{item_start + 0.5},{start_sec + duration_sec})'"
                            )
                            break
                    # 箭头符号
                    filters.append(
                        f"drawtext=text='→':fontsize=36:fontcolor=#aaaaaa"
                        f":x={arrow_x}:y={y_pos}{font_spec}"
                        f":enable='between(t,{item_start + 0.3},{start_sec + duration_sec})'"
                    )

            if not filters:
                import shutil
                shutil.copy2(input_path, out)
                return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name,
                                      output={"output_path": out}, output_path=out)

            cmd = [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", input_path,
                "-vf", ",".join(filters),
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "copy", out,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                      error=f"diagram error: {result.stderr[:200]}")
            return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name,
                                  output={"output_path": out, "items": n, "relations": len(relations)},
                                  output_path=out)
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error=str(e))


# ── 人脸/背景相关 ──

class FaceDetectTool(BaseTool):
    """人脸检测工具 — 检测视频帧中的人脸位置和数量。"""
    name = "face_detect"
    description = "检测视频中的人脸，返回每帧的人脸位置/数量/置信度"
    dependencies = []

    async def execute(
        self,
        input_path: str = "",
        time_sec: float = 0.0,
        **kwargs: Any,
    ) -> ToolExecResult:
        if not Path(input_path).exists():
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error="file not found")
        try:
            # 使用 ffmpeg 的 dnn/detect 或简单 fallback
            # 提取一帧，用 vision service 分析
            frame = Path(tempfile.mktemp(suffix=".jpg"))
            try:
                extract = subprocess.run(
                    ["ffmpeg", "-y", "-loglevel", "error", "-ss", str(time_sec),
                     "-i", input_path, "-vframes", "1", str(frame)],
                    capture_output=True, text=True, timeout=15,
                )
                if extract.returncode != 0 or not frame.exists():
                    return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error="frame extraction failed")

                from clipwright.services.vision import ImageAnalyzer
                from clipwright.services.llm import LLMService
                analyzer = ImageAnalyzer()
                result = await analyzer.analyze_image(str(frame))
                description = result.get("description", "") or ""
                tags = result.get("tags", [])

                # LLM 判断是否含人脸
                has_face = False
                try:
                    llm = LLMService()
                    resp = await llm.ask(
                        f"图片描述: {description[:200]}\n标签: {', '.join(tags[:10])}\n\n这张图片中是否包含人脸？只回答 true 或 false。"
                    )
                    if resp.success and resp.content:
                        has_face = "true" in resp.content.strip().lower()
                except Exception:
                    # fallback: 关键字检测
                    has_face = any("人" in str(t) or "face" in str(t).lower() or "person" in str(t).lower() for t in tags)

                return ToolExecResult(
                    status=ToolStatus.SUCCESS, tool_name=self.name,
                    output={
                        "has_face": has_face,
                        "tags": tags,
                        "description": description[:100],
                        "frame_time": time_sec,
                        "note": "检测到人脸" if has_face else "未检测到人脸",
                    },
                )
            finally:
                if frame.exists():
                    frame.unlink(missing_ok=True)
        except Exception as e:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error=str(e))


class BackgroundRemoveTool(BaseTool):
    """背景去除/替换工具 — 绿幕抠像 + AI 无绿幕抠图。"""
    name = "background_remove"
    description = "移除或替换视频背景：chroma_key(绿幕)/ai(无绿幕)/blur(背景模糊)"
    dependencies = ["ffmpeg"]

    async def execute(
        self,
        input_path: str = "",
        method: str = "chroma_key",
        color: str = "0x00ff00",
        similarity: float = 0.3,
        blend: float = 0.1,
        background_path: str = "",
        background_color: str = "0x000000",
        output_path: Optional[str] = None,
        **kwargs: Any,
    ) -> ToolExecResult:
        out = output_path or Path(tempfile.mktemp(suffix=".mp4")).name
        if not Path(input_path).exists():
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error="file not found")
        try:
            if method == "chroma_key":
                if background_path and Path(background_path).exists():
                    # 绿幕 + 替换背景
                    cmd = [
                        "ffmpeg", "-y", "-loglevel", "error",
                        "-i", input_path, "-i", background_path,
                        "-filter_complex",
                        f"[0:v]chromakey={color}:similarity={similarity}:blend={blend}[ck];"
                        f"[ck][1:v]overlay=format=auto,format=yuv420p[out]",
                        "-map", "[out]", "-map", "0:a",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", out,
                    ]
                else:
                    # 绿幕 + 纯色背景
                    cmd = [
                        "ffmpeg", "-y", "-loglevel", "error",
                        "-i", input_path,
                        "-vf", f"chromakey={color}:similarity={similarity}:blend={blend}:color={background_color}",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-c:a", "copy", out,
                    ]
            elif method == "blur":
                cmd = [
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-i", input_path,
                    "-vf", "split[fg][bg];[bg]gblur=sigma=20[bg];[bg][fg]overlay=format=auto",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "copy", out,
                ]
            else:
                return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                      error=f"unsupported method: {method}")

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if result.returncode != 0:
                return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                      error=f"bgremove error: {result.stderr[:200]}")
            return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name,
                                  output={"output_path": out, "method": method}, output_path=out)
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error=str(e))
