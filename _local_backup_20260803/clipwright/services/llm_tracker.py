"""LLM 成本追踪 — 记录每次 LLM 调用到 MongoDB。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from clipwright.config import TIME_ZONE, logger
from clipwright.context import mongo
from clipwright.models.pipeline_model import LLMCallModel

# 各模型的每千 token 价格（USD），用于成本估算
MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6": {"input": 0.003, "output": 0.015, "cached_input": 0.0003},
    "claude-opus-4-6": {"input": 0.015, "output": 0.075, "cached_input": 0.0015},
    "claude-haiku-4-5": {"input": 0.00025, "output": 0.00125, "cached_input": 0.00003},
    "gpt-4o": {"input": 0.0025, "output": 0.01, "cached_input": 0.00125},
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006, "cached_input": 0.000075},
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int, cached_input: int = 0) -> float:
    """估算 LLM 调用成本（USD）。"""
    pricing = MODEL_PRICING.get(model, {"input": 0.003, "output": 0.015, "cached_input": 0.0003})
    cost = (input_tokens / 1000) * pricing["input"]
    cost += (output_tokens / 1000) * pricing["output"]
    cost += (cached_input / 1000) * (pricing.get("cached_input", pricing["input"] * 0.1))
    return round(cost, 6)


async def record_llm_call(
    pipeline_id: str,
    agent_name: str,
    model: str,
    provider: str,
    input_tokens: int,
    output_tokens: int,
    duration_ms: float,
    prompt_summary: str = "",
    status: str = "success",
    error: str | None = None,
    cached_input_tokens: int = 0,
) -> str | None:
    """记录一次 LLM 调用到 MongoDB。

    Returns:
        record_id or None（MongoDB 不可用时）
    """
    try:
        if not mongo.is_connected:
            return None

        cost = estimate_cost(model, input_tokens, output_tokens, cached_input_tokens)
        call_id = f"llm_{uuid.uuid4().hex[:12]}"

        model_inst = LLMCallModel(
            _id=call_id,
            pipeline_id=pipeline_id,
            agent_name=agent_name,
            model=model,
            provider=provider,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
            duration_ms=duration_ms,
            prompt_summary=prompt_summary[:100],
            status=status,
            error=error,
            estimated_cost=cost,
        )
        model_inst.insert()
        return call_id
    except Exception as e:
        logger.warning("LLM 调用记录失败: %s", e)
        return None


async def get_llm_usage_stats(
    pipeline_id: str = "",
    from_date: str = "",
    to_date: str = "",
) -> dict:
    """获取 LLM 使用统计。"""
    try:
        if not mongo.is_connected:
            return {"error": "MongoDB not connected"}

        filters: dict = {}
        if pipeline_id:
            filters["pipeline_id"] = pipeline_id

        # 使用聚合管道统计
        pipeline = [
            {"$match": filters},
            {
                "$group": {
                    "_id": "$model",
                    "call_count": {"$sum": 1},
                    "total_input_tokens": {"$sum": "$input_tokens"},
                    "total_output_tokens": {"$sum": "$output_tokens"},
                    "total_cost": {"$sum": "$estimated_cost"},
                    "avg_duration_ms": {"$avg": "$duration_ms"},
                    "error_count": {"$sum": {"$cond": [{"$eq": ["$status", "error"]}, 1, 0]}},
                }
            },
            {"$sort": {"total_cost": -1}},
        ]
        results = list(LLMCallModel.aggregate(pipeline))

        total_input = sum(r.get("total_input_tokens", 0) for r in results)
        total_output = sum(r.get("total_output_tokens", 0) for r in results)
        total_cost = sum(r.get("total_cost", 0) for r in results)

        return {
            "total_calls": sum(r.get("call_count", 0) for r in results),
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_cost_usd": round(total_cost, 4),
            "by_model": [
                {
                    "model": r["_id"],
                    "calls": r.get("call_count", 0),
                    "input_tokens": r.get("total_input_tokens", 0),
                    "output_tokens": r.get("total_output_tokens", 0),
                    "cost_usd": round(r.get("total_cost", 0), 4),
                    "avg_duration_ms": round(r.get("avg_duration_ms", 0), 0),
                    "errors": r.get("error_count", 0),
                }
                for r in results
            ],
        }
    except Exception as e:
        logger.warning("LLM 统计查询失败: %s", e)
        return {"error": str(e)}
