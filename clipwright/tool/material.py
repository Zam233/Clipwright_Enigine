"""素材筛选与帧验证工具 — 可被 Agent 独立调用。"""

from __future__ import annotations

import json
import os
import asyncio
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

