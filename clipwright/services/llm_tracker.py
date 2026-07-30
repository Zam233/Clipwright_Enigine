"""LLM Token 用量追踪。

记录每次 LLM 调用的 token 消耗，供后续分析和成本核算。
"""

from __future__ import annotations

from clipwright.config import logger

# 内存存储（后续可持久化到 MongoDB），限制最大记录数防止内存泄漏
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
    """记录一次 LLM 调用。"""
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
    }
    _llm_calls.append(record)
    if len(_llm_calls) > _MAX_CALLS:
        del _llm_calls[:-((_MAX_CALLS * 3) // 4)]
    logger.info(
        "LLM call: pipeline=%s agent=%s model=%s tokens=%d/%d (%.0fms)",
        pipeline_id, agent_name, model, input_tokens, output_tokens, duration_ms,
    )


def get_llm_calls(pipeline_id: str = "") -> list[dict]:
    """查询 LLM 调用记录。"""
    if pipeline_id:
        return [r for r in _llm_calls if r["pipeline_id"] == pipeline_id]
    return list(_llm_calls)
