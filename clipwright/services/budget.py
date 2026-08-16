"""P5-B3: LLM 成本预算熔断（全局月 token 预算，默认关闭）。

预算检查结果内存缓存 60s，避免每个管线请求都打 Mongo 聚合。
多实例部署时缓存各自独立（最终一致性，可接受）。
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime

from clipwright.config import TIME_ZONE, logger, settings
from clipwright.context import mongo

_budget_cache: dict[str, tuple[float, int]] = {}
_CACHE_TTL_SEC = 60.0


async def _sum_month_tokens() -> int:
    """聚合本月（Asia/Shanghai 月起点）已消耗 tokens。"""
    from datetime import timezone as _tz

    if not mongo.is_connected:
        return 0
    now = datetime.now(tz=TIME_ZONE)
    month_start = datetime(now.year, now.month, 1, tzinfo=TIME_ZONE)
    start_ts = month_start.astimezone(_tz.utc).replace(tzinfo=None)

    def _query() -> int:
        pipeline = [
            {"$match": {"created_time": {"$gte": start_ts}}},
            {"$group": {"_id": None, "total": {"$sum": {"$add": ["$input_tokens", "$output_tokens"]}}}},
        ]
        rows = list(mongo.db["llm_calls"].aggregate(pipeline))
        return int(rows[0]["total"]) if rows and rows[0].get("total") else 0

    try:
        return await asyncio.to_thread(_query)
    except Exception as e:
        logger.warning("成本预算聚合失败（放行）: %s", e)
        return 0


async def check_budget() -> tuple[bool, int]:
    """返回 (是否放行, 本月已用 tokens)。预算 ≤0 恒放行。"""
    budget = settings.llm_monthly_token_budget
    if budget <= 0:
        return True, 0
    now = time.time()
    cached = _budget_cache.get("month")
    if cached and now - cached[0] < _CACHE_TTL_SEC:
        return cached[1] < budget, cached[1]
    used = await _sum_month_tokens()
    _budget_cache["month"] = (now, used)
    return used < budget, used


def reset_cache() -> None:
    _budget_cache.clear()
