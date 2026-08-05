"""素材筛选与帧验证工具 — 可被 Agent 独立调用。"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

from clipwright.config import logger, settings
from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.tool.base import BaseTool


def _lavfi_escape(path: str) -> str:
    """转义 lavfi movie= 滤镜路径中的特殊字符（\\ : '）。"""
    return path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


class MaterialFilterTool(BaseTool):
    """素材筛选工具 — 按方向、分辨率筛选/排序素材。"""
    name = "material_filter"
    description = "按方向(横屏/竖屏)、分辨率筛选和排序素材"
    dependencies = []

    async def execute(
        self,
        candidates: list[dict[str, Any]],
        orientation: str = "landscape",
        **kwargs: Any,
    ) -> ToolExecResult:
        filtered = []
        for c in candidates:
            score = 0.0
            w, h = c.get("width", 0), c.get("height", 0)
            if w > 0 and h > 0:
                orient = "landscape" if w > h else "portrait"
                if orient == orientation:
                    score += 0.5
                quality = min(0.5, (w * h) / (1920 * 1080) * 0.5)
                score += quality
            else:
                score += 0.3
            c["_filter_score"] = round(score, 3)
            filtered.append(c)
        filtered.sort(key=lambda x: x.get("_filter_score", 0), reverse=True)
        for c in filtered:
            c.pop("_filter_score", None)
        return ToolExecResult(
            status=ToolStatus.SUCCESS,
            tool_name=self.name,
            output={"candidates": filtered, "total": len(filtered)},
        )


class FrameValidatorTool(BaseTool):
    """帧验证工具 — 验证视频帧非全黑/全白，并用视觉模型校验与文案匹配度。"""
    name = "frame_validator"
    description = "抽取视频帧验证: 非全黑/全白 + 图片描述模型校验匹配度"
    dependencies = ["ffmpeg", "ffprobe"]

    async def execute(
        self,
        video_url: str,
        expected_text: str = "",
        **kwargs: Any,
    ) -> ToolExecResult:
        fd, tmp_path = tempfile.mkstemp(suffix=".jpg")
        os.close(fd)  # 关闭句柄，交由 ffmpeg -y 覆写（避免 mktemp 的 TOCTOU 竞态）
        frame = Path(tmp_path)
        try:
            result = subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-ss", "1",
                 "-i", video_url, "-vframes", "1", "-vf", "scale=320:-1",
                 "-q:v", "5", str(frame)],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode != 0 or not frame.exists() or frame.stat().st_size < 100:
                return ToolExecResult(
                    status=ToolStatus.ERROR, tool_name=self.name,
                    error=f"无法抽取帧: {result.stderr[:200] if result.stderr else 'empty frame'}",
                )

            try:
                # ffprobe 无 "mean" 键；改用 lavfi signalstats 的 YAVG 标签测平均亮度
                stats = subprocess.run(
                    ["ffprobe", "-v", "error", "-f", "lavfi",
                     "-i", f"movie='{_lavfi_escape(str(frame))}',signalstats",
                     "-show_entries", "frame_tags=lavfi.signalstats.YAVG",
                     "-of", "json"],
                    capture_output=True, text=True, timeout=10,
                )
                mean_data = json.loads(stats.stdout)
                frames_info = mean_data.get("frames", [])
                tag = (frames_info[0].get("tags", {}) if frames_info else {}).get(
                    "lavfi.signalstats.YAVG"
                )
                mean_val = float(tag) if tag is not None else 128
                is_blank = mean_val < 10 or mean_val > 245
            except (ValueError, json.JSONDecodeError):
                mean_val, is_blank = 128, False

            match_score = 0.5
            description = ""
            # 视觉 LLM 门控（全局 settings.enable_visual_llm，与 agent 的 plugin_config 门控
            # 共存：agent 读插件配置，工具读全局配置）。视觉关时跳过 ImageAnalyzer（省钱），
            # 且输出不含 match_score → material_agent._validate_video_frame 落到启发式标题匹配。
            if settings.enable_visual_llm and expected_text and not is_blank:
                try:
                    from clipwright.services.vision import VisionService
                    analyzer = VisionService()
                    vision_result = await analyzer.analyze_image(str(frame))
                    description = vision_result.get("description", "") or str(vision_result.get("tags", []))
                    exp_words = set(expected_text.lower().split())
                    desc_words = set(description.lower().split())
                    if exp_words and desc_words:
                        overlap = len(exp_words & desc_words) / max(len(exp_words), 1)
                        match_score = min(1.0, overlap * 3)
                except Exception as e:
                    logger.debug("FrameValidator 图片描述失败: %s", e)

            out: dict[str, Any] = {
                "is_blank": is_blank,
                "mean_luminance": round(mean_val, 1),
                "frame_path": str(frame),
            }
            if settings.enable_visual_llm and not is_blank and expected_text:
                out["match_score"] = round(match_score, 3)
                out["description"] = description[:100] if description else ""

            return ToolExecResult(
                status=ToolStatus.SUCCESS,
                tool_name=self.name,
                output=out,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            logger.warning("FrameValidator FFmpeg 异常: %s", e)
            return ToolExecResult(
                status=ToolStatus.DEPENDENCY_MISSING if isinstance(e, FileNotFoundError) else ToolStatus.ERROR,
                tool_name=self.name, error=str(e),
            )
        finally:
            if frame.exists():
                frame.unlink(missing_ok=True)
