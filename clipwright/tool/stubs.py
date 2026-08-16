"""视觉 LLM 工具（VisionLLMTool）— 真实实现。

P1 变更记录：本文件原含 6 个「占位实现返回假 SUCCESS」的 stub 工具，已按
fix-and-feature-plan P1 处理：
- frame_validator / black_frame_detect / audio_silence_detect / whisper_transcribe
  → 已切换为真实实现注册（tool/material.py、tool/quality.py、tool/transcribe.py）；
- video_filter / text_design → 无真实实现，已从 ToolRegistry 摘除
  （其唯一调用链为已删除的对话式编辑死代码，能力由时间线+Agent 返工替代）；
- subtitle_overflow → 切换为 tool/quality.py 的 SubtitleOverflowCheckTool（真实计算）。
本文件仅保留真实实现的 VisionLLMTool。
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.tool.base import BaseTool

logger = logging.getLogger("clipwright.tool.stubs")


class VisionLLMTool(BaseTool):
    """视觉 LLM 分析工具 — 用多模态 LLM 分析视频帧内容。"""
    name = "vision_llm"
    description = "视觉 LLM 分析：用多模态大模型分析视频帧内容、描述、标签"

    async def execute(self, **kwargs: Any) -> ToolExecResult:
        from clipwright.services.vision import VisionService
        from clipwright.tool.frame_extractor import extract_frames

        asset = kwargs.get("asset") or {}
        scene_context = kwargs.get("scene_context") or {}
        frame_count = kwargs.get("frame_count", 3)
        frame_paths: list[str] = []

        try:
            frame_paths = await extract_frames(asset, int(frame_count))
            if not frame_paths:
                return ToolExecResult(
                    status=ToolStatus.SUCCESS,
                    tool_name=self.name,
                    output={
                        "score": 0.5,
                        "tags": [],
                        "description": "",
                        "frames_analyzed": 0,
                        "extraction_method": "none",
                        "fallback": True,
                    },
                )

            service = VisionService()
            analyses = await asyncio.gather(
                *(service.analyze_image(path) for path in frame_paths)
            )
            tags = {
                str(tag).lower()
                for analysis in analyses
                for tag in analysis.get("tags", [])
                if tag
            }
            labels = {
                str(label)
                for analysis in analyses
                for label in analysis.get("labels", [])
                if label
            }
            descriptions = [
                str(analysis.get("description", ""))
                for analysis in analyses
                if analysis.get("description")
            ]
            description = " | ".join(descriptions)

            keywords = {
                str(keyword).lower()
                for keyword in scene_context.get("keywords", [])
                if keyword
            }
            tag_similarity = len(tags & keywords) / max(len(tags | keywords), 1)

            scene_words = set(str(scene_context.get("description", "")).lower().split())
            aggregated_description = description.lower()
            description_overlap = sum(
                word in aggregated_description for word in scene_words
            ) / max(len(scene_words), 1)
            score = min(max(tag_similarity * 0.6 + description_overlap * 0.4, 0.0), 1.0)

            url = asset.get("url") if isinstance(asset, dict) else None
            extraction_method = (
                "thumbnail"
                if len(frame_paths) == 1
                and os.path.basename(frame_paths[0]).startswith("thumbnail_")
                else "http_range" if url
                else "local"
            )
            return ToolExecResult(
                status=ToolStatus.SUCCESS,
                tool_name=self.name,
                output={
                    "score": round(score, 4),
                    "tags": list(tags),
                    "labels": list(labels),
                    "description": description,
                    "frames_analyzed": len(frame_paths),
                    "extraction_method": extraction_method,
                },
            )
        except Exception as exc:
            logger.exception("Vision LLM material analysis failed")
            return ToolExecResult(
                status=ToolStatus.SUCCESS,
                tool_name=self.name,
                output={
                    "score": 0.5,
                    "tags": [],
                    "description": "",
                    "frames_analyzed": 0,
                    "extraction_method": "error",
                    "fallback": True,
                    "error": str(exc),
                },
            )
        finally:
            for path in frame_paths:
                try:
                    os.remove(path)
                except OSError as exc:
                    logger.debug("Failed to remove temporary frame %s: %s", path, exc)
