"""PipelineState MongoDB 持久化模型 — 管线状态、span 追踪、LLM 调用记录。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from clipwright.config import TIME_ZONE, logger
from clipwright.services.mongodb_service import MongoDbModel


class PipelineModel(MongoDbModel):
    """管线执行状态 MongoDB 持久化。

    Collection: pipelines

    Fields:
        _id: str (pipeline_id)
        status: str — PipelineStatus 枚举值
        request: dict — PipelineRequest 序列化
        steps: list[dict] — 每个 agent 的执行详情
        shared_data: dict — Agent 间共享数据
        final_timeline: dict | None
        output_path: str
        error: str | None
        error_category: str — 错误分类 (transient/permanent/fatal)
        duration_sec: float — 总耗时
        extra_params_summary: dict | None — 用户参数摘要 (便于查询)
    """

    collection_name = "pipelines"

    def __init__(self, **kwargs: Any):
        now = datetime.now(tz=TIME_ZONE) if TIME_ZONE else datetime.now(timezone.utc)
        super().__init__(
            created_time=kwargs.pop("created_time", now),
            updated_time=kwargs.pop("updated_time", now),
            **kwargs,
        )


class TraceSpanModel(MongoDbModel):
    """全链路追踪 Span — 记录每个 Agent/Tool/LLM 调用的耗时和输入输出。

    Collection: trace_spans

    Fields:
        _id: str (span_id)
        pipeline_id: str
        parent_span_id: str — 父 span ID，构建调用树
        span_type: str — "agent" / "llm" / "tool" / "render"
        agent_name: str — 所属 agent
        span_name: str — 描述
        started_at: datetime
        completed_at: datetime | None
        duration_ms: float
        status: str — "ok" / "error"
        input_summary: str — 输入摘要 (前 200 字符)
        output_summary: str — 输出摘要
        error: str | None
        metadata: dict — 扩展数据
    """

    collection_name = "trace_spans"

    def __init__(self, **kwargs: Any):
        now = datetime.now(tz=TIME_ZONE) if TIME_ZONE else datetime.now(timezone.utc)
        super().__init__(
            created_time=kwargs.pop("created_time", now),
            updated_time=kwargs.pop("updated_time", now),
            **kwargs,
        )


class LLMCallModel(MongoDbModel):
    """LLM 调用记录 — 成本追踪与分析。

    Collection: llm_calls

    Fields:
        _id: str
        pipeline_id: str
        agent_name: str
        model: str
        provider: str
        input_tokens: int
        output_tokens: int
        cached_input_tokens: int
        duration_ms: float
        prompt_summary: str — 前 100 字符
        status: str — "success" / "error"
        error: str | None
        estimated_cost: float — USD
    """

    collection_name = "llm_calls"

    def __init__(self, **kwargs: Any):
        now = datetime.now(tz=TIME_ZONE) if TIME_ZONE else datetime.now(timezone.utc)
        super().__init__(
            created_time=kwargs.pop("created_time", now),
            updated_time=kwargs.pop("updated_time", now),
            **kwargs,
        )
