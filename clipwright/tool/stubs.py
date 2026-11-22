"""读图工具（ReadImageTool）— 真实实现。

P1 变更记录：本文件原含 6 个「占位实现返回假 SUCCESS」的 stub 工具，已按
fix-and-feature-plan P1 处理：
- frame_validator / black_frame_detect / audio_silence_detect / whisper_transcribe
  → 已切换为真实实现注册（tool/material.py、tool/quality.py、tool/transcribe.py）；
- video_filter / text_design → 无真实实现，已从 ToolRegistry 摘除
  （其唯一调用链为已删除的对话式编辑死代码，能力由时间线+Agent 返工替代）；
- subtitle_overflow → 切换为 tool/quality.py 的 SubtitleOverflowCheckTool（真实计算）。
本文件仅保留真实实现的 ReadImageTool。
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.tool.base import BaseTool

logger = logging.getLogger("clipwright.tool.stubs")


class ReadImageTool(BaseTool):
    """读取图片/视频帧内容 — 供所有 Agent 直接看图。"""
    name = "read_image"
    capability = "read_image"
    permission_tier = "costly"
    agent_callable = True
    dry_run_capable = False
    description = "读取图片（或视频帧）内容：返回画面描述、标签与匹配分；path 支持本地图片。"

    parameters_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "本地图片路径；省略时用 asset"},
            "asset": {"type": "object", "description": "素材对象 {local_path,url,...}"},
            "scene_context": {"type": "object"},
            "frame_count": {"type": "integer", "default": 1, "minimum": 1, "maximum": 6},
            "brief": {"type": "string"},
        },
        "required": [],
    }

    async def execute(self, path: str = "", **kwargs: Any) -> ToolExecResult:
        from clipwright.services.vision import VisionService
        from clipwright.tool.frame_extractor import extract_frames

        asset = dict(kwargs.get("asset") or {})
        scene_context = kwargs.get("scene_context") or {}
        frame_count = int(kwargs.get("frame_count", 1) or 1)
        frame_paths: list[str] = []
        cleanup_frames = True

        direct = str(path or "").strip()
        if direct and Path(direct).is_file() and Path(direct).suffix.lower() in {
            ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif",
        }:
            frame_paths = [direct]
            cleanup_frames = False
        else:
            if direct and not asset:
                asset = {"local_path": direct, "duration_sec": 1}
        try:
            if not frame_paths:
                frame_paths = await extract_frames(asset, frame_count)
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

            # 描述词表 = 场景描述 + 场景级旁白 + 素材意图（对旁白/意图词敏感）
            scene_words = set(
                " ".join(filter(None, [
                    str(scene_context.get("description", "")),
                    str(scene_context.get("narration_text", "")),
                    str(scene_context.get("material_intent", "")),
                ])).lower().split()
            )
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
                    "path": direct or asset.get("local_path") or asset.get("url") or "",
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
                    "path": direct or asset.get("local_path") or "",
                    "frames_analyzed": 0,
                    "extraction_method": "error",
                    "fallback": True,
                    "error": str(exc),
                },
            )
        finally:
            for path in (frame_paths if cleanup_frames else []):
                try:
                    os.remove(path)
                except OSError as exc:
                    logger.debug("Failed to remove temporary frame %s: %s", path, exc)
