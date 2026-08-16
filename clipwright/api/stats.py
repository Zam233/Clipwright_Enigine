"""用量/费用报表（P5）— 用户可读；jwt 模式按 owner 过滤，off/token 模式返回全局。"""

from __future__ import annotations

import asyncio
from datetime import datetime

from fastapi import APIRouter, Request

from clipwright.api.pipeline import _pipeline_owners
from clipwright.api.render import _render_queue
from clipwright.authz import current_user_id, filter_by_owner, is_admin
from clipwright.config import TIME_ZONE, logger
from clipwright.context import mongo

router = APIRouter(prefix="/api/stats", tags=["stats"])


def _visible_pipeline_ids(request: Request) -> list[str]:
    uid = current_user_id(request)
    if uid is None or is_admin(request):
        return list(_pipeline_owners.keys())
    return [pid for pid, owner in _pipeline_owners.items() if owner == uid]


def _sum_tokens_sync(flt: dict) -> int:
    pipeline = [
        {"$match": flt},
        {"$group": {"_id": None, "total": {"$sum": {"$add": ["$input_tokens", "$output_tokens"]}}}},
    ]
    rows = list(mongo.db["llm_calls"].aggregate(pipeline))
    return int(rows[0]["total"]) if rows and rows[0].get("total") else 0


@router.get("/usage")
async def usage_stats(request: Request) -> dict:
    """用户可读用量：管线数 / 渲染任务数 / LLM tokens（本月与总计）。"""
    ids = _visible_pipeline_ids(request)

    month_tokens = 0
    total_tokens = 0
    if mongo.is_connected and ids:
        flt = {"pipeline_id": {"$in": ids}}
        try:
            now = datetime.now(tz=TIME_ZONE)
            month_start = datetime(now.year, now.month, 1, tzinfo=TIME_ZONE)
            from datetime import timezone as _tz
            start_naive = month_start.astimezone(_tz.utc).replace(tzinfo=None)

            total_tokens = await asyncio.to_thread(_sum_tokens_sync, flt)
            month_tokens = await asyncio.to_thread(
                _sum_tokens_sync, {**flt, "created_time": {"$gte": start_naive}}
            )
        except Exception as e:
            logger.warning("用量统计聚合失败: %s", e)

    renders = len(filter_by_owner(request, list(_render_queue.values())))

    return {
        "pipelines": len(ids),
        "renders": renders,
        "llm_tokens_month": month_tokens,
        "llm_tokens_total": total_tokens,
    }
