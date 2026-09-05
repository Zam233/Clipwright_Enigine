"""视觉分析工具 — FFmpeg 场景检测 + 存根 CLIP 接口。

当前阶段：FFmpeg 场景检测已实现，CLIP 语义匹配为占位。
CLIP 集成推迟到对接开源推理服务后。
"""

from __future__ import annotations

import re
import subprocess
from typing import Any

from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.tool.base import BaseTool
from clipwright.tool.video import _ffmpeg


class SceneDetectTool(BaseTool):
    """场景检测工具（基于 FFmpeg scene detect filter）。"""
    name = "scene_detect"
    description = "检测视频中的场景切换点并返回时间戳列表"
    dependencies = ["ffmpeg"]

    async def execute(
        self,
        input_path: str,
        threshold: float = 0.3,
        **kwargs: Any,
    ) -> ToolExecResult:
        try:
            result = await _ffmpeg(
                "-i", input_path,
                "-filter:v", f"select='gt(scene,{threshold})',showinfo",
                "-f", "null", "-",
            )
            if result.returncode != 0:
                return ToolExecResult(
                    status=ToolStatus.ERROR,
                    tool_name=self.name,
                    error=f"ffmpeg error: {result.stderr[:500]}",
                )

            # 从 stderr 解析时间戳
            scenes = []
            for match in re.finditer(
                r"pts_time:([\d.]+)", result.stderr or ""
            ):
                ts = float(match.group(1))
                scenes.append({"timestamp_sec": ts})

            return ToolExecResult(
                status=ToolStatus.SUCCESS,
                tool_name=self.name,
                output={
                    "scene_changes": scenes,
                    "scene_count": len(scenes),
                    "threshold": threshold,
                },
            )
        except FileNotFoundError:
            return ToolExecResult(
                status=ToolStatus.DEPENDENCY_MISSING,
                tool_name=self.name,
                error="ffmpeg not found — install FFmpeg for scene detection",
            )
        except subprocess.TimeoutExpired:
            return ToolExecResult(
                status=ToolStatus.ERROR,
                tool_name=self.name,
                error="ffmpeg timed out during scene detection",
            )


class SemanticMatchTool(BaseTool):
    """语义匹配工具（CLIP）。

    Phase 1 占位：CLIP 依赖较大，推迟到对接推理服务后实现。
    预期接口：接收文本查询 + 视频片段列表，返回语义相关性分数。
    """
    name = "semantic_match"
    description = "用 CLIP 做文本-视频语义匹配（Phase 1 占位，待接入 CLIP 推理服务）"

    async def execute(
        self,
        query: str,
        candidate_paths: list[str],
        **kwargs: Any,
    ) -> ToolExecResult:
        return ToolExecResult(
            status=ToolStatus.SUCCESS,
            tool_name=self.name,
            output={
                "query": query,
                "candidates": [
                    {"path": p, "score": 0.5, "note": "placeholder — CLIP not yet integrated"}
                    for p in candidate_paths
                ],
            },
            warning="CLIP model not integrated yet — all scores are dummy 0.5",
        )
