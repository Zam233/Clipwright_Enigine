"""`vision_llm_batch` —— 一批候选一次视觉评分。

Engine 侧负责并发、缓存与逐项失败隔离；实际视觉实现仍走 `vision_score` 能力
（`vision_llm` 或插件接管者）。单批最多 50 个候选。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import OrderedDict
from typing import Any

from clipwright.config import logger
from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.tool.base import BaseTool
from clipwright.tool.capabilities import resolve

_CACHE: OrderedDict[str, dict[str, Any]] = OrderedDict()
_CACHE_MAX = 1024


def clear_vision_batch_cache() -> None:
    _CACHE.clear()


def _cache_key(asset: dict[str, Any], scene_context: dict[str, Any], frame_count: int) -> str:
    raw = json.dumps([
        asset.get("asset_id") or asset.get("url") or asset.get("local_path") or "",
        asset.get("url") or asset.get("local_path") or "",
        int(frame_count or 3),
        scene_context.get("title") or "",
        scene_context.get("keywords") or [],
        scene_context.get("description") or "",
        scene_context.get("narration_text") or "",
        scene_context.get("material_intent") or "",
    ], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class VisionBatchTool(BaseTool):
    """批量视觉评分工具；单项失败不会中断整批。"""

    name = "vision_llm_batch"
    capability = "vision_score_batch"
    capability_priority = 10
    permission_tier = "costly"
    agent_callable = True
    dry_run_capable = False
    dependencies: list[str] = []
    description = (
        "批量分析候选素材画面与场景的语义匹配分：assets[] + scene_context + frame_count，"
        "逐项返回 score/tags/description；单批最多 50 个。"
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "assets": {
                "type": "array",
                "maxItems": 50,
                "items": {"type": "object"},
                "description": "候选素材 [{asset_id,url,local_path,...}]",
            },
            "scene_context": {
                "type": "object",
                "description": "场景上下文 {title,keywords,description,narration_text,material_intent}",
            },
            "frame_count": {"type": "integer", "default": 3, "minimum": 1, "maximum": 6},
            "concurrency": {"type": "integer", "default": 4, "minimum": 1, "maximum": 8},
        },
        "required": ["assets"],
    }

    def is_available(self) -> bool:
        return resolve("vision_score") is not None

    async def execute(
        self,
        *,
        assets: list[dict[str, Any]] | None = None,
        scene_context: dict[str, Any] | None = None,
        frame_count: int = 3,
        concurrency: int = 4,
        **kwargs: Any,
    ) -> ToolExecResult:
        rows = [x for x in (assets or []) if isinstance(x, dict)]
        if not rows:
            return ToolExecResult(
                status=ToolStatus.ERROR, tool_name=self.name,
                error="assets 不能为空",
            )
        if len(rows) > 50:
            return ToolExecResult(
                status=ToolStatus.ERROR, tool_name=self.name,
                error=f"assets 最多 50 项（收到 {len(rows)}）",
            )
        scorer = resolve("vision_score")
        if scorer is None:
            return ToolExecResult(
                status=ToolStatus.NOT_FOUND, tool_name=self.name,
                error="vision_score 能力当前无可用实现",
            )
        ctx = dict(scene_context or {})
        frames = max(1, min(6, int(frame_count or 3)))
        sem = asyncio.Semaphore(max(1, min(8, int(concurrency or 4))))

        async def _one(index: int, asset: dict[str, Any]) -> dict[str, Any]:
            key = _cache_key(asset, ctx, frames)
            cached = _CACHE.get(key)
            if cached is not None:
                _CACHE.move_to_end(key)
                return {"index": index, "cached": True, **cached}
            async with sem:
                try:
                    res = await scorer.execute(
                        asset=asset, scene_context=ctx, frame_count=frames,
                    )
                    out = dict(getattr(res, "output", None) or {})
                    if str(getattr(res, "status", "")) not in ("success", "ok"):
                        return {"index": index, "status": "failed",
                                "error": str(getattr(res, "error", "") or "vision_score failed")}
                    result = {
                        "status": "ok",
                        "score": out.get("score", 0.5),
                        "tags": out.get("tags") or [],
                        "description": out.get("description") or "",
                        "frames_analyzed": out.get("frames_analyzed", 0),
                        "extraction_method": out.get("extraction_method", ""),
                    }
                    _CACHE[key] = result
                    _CACHE.move_to_end(key)
                    while len(_CACHE) > _CACHE_MAX:
                        _CACHE.popitem(last=False)
                    return {"index": index, "cached": False, **result}
                except Exception as exc:  # noqa: BLE001 — 单候选失败
                    logger.warning("vision_llm_batch item %s failed: %s", index, exc)
                    return {"index": index, "status": "failed",
                            "error": f"{type(exc).__name__}: {exc}"}

        items = await asyncio.gather(*[_one(i, a) for i, a in enumerate(rows)])
        ok = sum(1 for x in items if x.get("status") == "ok")
        return ToolExecResult(
            status=ToolStatus.SUCCESS if ok else ToolStatus.ERROR,
            tool_name=self.name,
            error="" if ok else "所有候选视觉评分失败",
            output={
                "items": items,
                "summary": {"total": len(items), "ok": ok, "failed": len(items) - ok},
            },
        )
