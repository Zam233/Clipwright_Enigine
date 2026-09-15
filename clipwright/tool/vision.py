"""视觉分析工具 — FFmpeg 场景检测 + CLIP 语义匹配。

R9 起 `semantic_match` 接的是**真 CLIP 视觉向量**（此前是返回假分 0.5 的占位实现）。
"""

from __future__ import annotations

import asyncio
import re
import subprocess
from typing import Any

from clipwright.config import settings
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
    """语义匹配工具（CLIP 视觉向量）。

    R9 之前这里是**占位实现**：所有候选都返回假分 `0.5`（并如实标成
    `DEPENDENCY_MISSING`）。现在接真 CLIP：

    - 查询文本经 `gloss_query()` 做中→英提示词映射（CLIP 文本塔不认中文）；
    - 候选图/视频帧算 CLIP 图像向量，取余弦相似度；
    - CLIP 不可用时**如实失败**（`DEPENDENCY_MISSING` + 空分数），
      绝不退回假分——假分比没分数更糟，它会让上层以为语义匹配已经生效。
    """

    name = "semantic_match"
    description = "用 CLIP 视觉向量做文本-画面语义匹配（中英文查询均可，中文走提示词改写）"

    async def execute(
        self,
        query: str,
        candidate_paths: list[str],
        **kwargs: Any,
    ) -> ToolExecResult:
        from clipwright.services import content_semantic as sem

        if not sem._clip_loaded():
            return ToolExecResult(
                status=ToolStatus.DEPENDENCY_MISSING,
                tool_name=self.name,
                output={"query": query, "candidates": []},
                error=(f"CLIP 模型不可用（{settings.content_index_clip_model}）——"
                       "语义匹配无法执行；请确认模型已下载或改用关键词检索"),
            )
        paths = [str(p) for p in candidate_paths if str(p)]
        if not paths:
            return ToolExecResult(
                status=ToolStatus.SUCCESS,
                tool_name=self.name,
                output={"query": query, "candidates": [], "query_gloss": sem.gloss_query(query)},
            )
        glossed = sem.gloss_query(query)
        vectors = await asyncio.to_thread(sem.embed_images, paths)
        text_vec = await asyncio.to_thread(sem.embed_texts, [glossed])
        if not vectors or not text_vec:
            return ToolExecResult(
                status=ToolStatus.ERROR,
                tool_name=self.name,
                output={"query": query, "query_gloss": glossed, "candidates": []},
                error="CLIP 前向失败（图片读取失败或显存不足），未产生任何分数",
            )
        qvec = text_vec[0]
        scored = [
            {"path": path, "score": round(sem.cosine(qvec, vec), 4)}
            for path, vec in zip(paths, vectors)
        ]
        scored.sort(key=lambda item: -item["score"])
        return ToolExecResult(
            status=ToolStatus.SUCCESS,
            tool_name=self.name,
            output={
                "query": query,
                "query_gloss": glossed,
                "candidates": scored,
                # 如实说明这次分数是什么：CLIP 余弦相似度，**不是**"相关概率"，
                # 也不要拿 0.5 当"一半相关"来读（不同查询的分布不可比）。
                "score_kind": "clip_cosine_similarity",
                "model": settings.content_index_clip_model,
            },
        )
