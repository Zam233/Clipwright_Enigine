"""LLM Token 用量追踪。

记录每次 LLM 调用的 token 消耗，供后续分析和成本核算。
C2（2026-08）：内存保留（热查询）+ MongoDB 持久化（llm_calls 集合，供 /metrics 与成本预算），
事件循环线程用 to_thread 写入，失败仅告警不阻断管线。
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from clipwright.config import TIME_ZONE, logger
from clipwright.context import mongo

# 内存存储（热查询用），限制最大记录数防止内存泄漏
_llm_calls: list[dict] = []
_MAX_CALLS = 10000


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
) -> None:
    """记录一次 LLM 调用（内存 + Mongo 持久化）。"""
    record = {
        "pipeline_id": pipeline_id,
        "agent_name": agent_name,
        "model": model,
        "provider": provider,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "duration_ms": duration_ms,
        "prompt_summary": prompt_summary,
        "status": status,
        "created_time": datetime.now(tz=TIME_ZONE),
    }
    _llm_calls.append(record)
    if len(_llm_calls) > _MAX_CALLS:
        del _llm_calls[:-((_MAX_CALLS * 3) // 4)]
    logger.info(
        "LLM call: pipeline=%s agent=%s model=%s tokens=%d/%d (%.0fms)",
        pipeline_id, agent_name, model, input_tokens, output_tokens, duration_ms,
    )
    # C2: 持久化到 Mongo（事件循环线程 to_thread；失败仅告警）
    try:
        if mongo.is_connected:
            await asyncio.to_thread(mongo.db["llm_calls"].insert_one, dict(record))
    except Exception as e:
        logger.warning("LLM 调用持久化失败: %s", e)


def get_llm_calls(pipeline_id: str = "") -> list[dict]:
    """查询 LLM 调用记录。"""
    if pipeline_id:
        return [r for r in _llm_calls if r["pipeline_id"] == pipeline_id]
    return list(_llm_calls)
