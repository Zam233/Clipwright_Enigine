"""SpanTracer — 管线执行追踪（轻量内存实现）。

为 pipeline / agent 执行创建 span，记录耗时和状态。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from clipwright.config import logger


class SpanTracer:
    """管线级 Span 追踪器。"""

    def __init__(self, pipeline_id: str) -> None:
        self.pipeline_id = pipeline_id
        self._spans: dict[str, dict[str, Any]] = {}

    def start_span(
        self,
        span_type: str,
        name: str,
        description: str = "",
        input_summary: str = "",
    ) -> str:
        """创建一个 span，返回 span_id。"""
        span_id = f"span_{uuid.uuid4().hex[:10]}"
        self._spans[span_id] = {
            "span_id": span_id,
            "type": span_type,
            "name": name,
            "description": description,
            "input_summary": input_summary,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "ended_at": None,
            "status": "running",
            "error": "",
            "metadata": {},
            "output_summary": "",
        }
        logger.debug("SpanTracer[%s] start %s/%s: %s", self.pipeline_id, span_type, name, description)
        return span_id

    def end_span(
        self,
        span_id: str,
        status: str = "ok",
        error: str = "",
        metadata: dict[str, Any] | None = None,
        output_summary: str = "",
    ) -> None:
        """结束一个 span。"""
        span = self._spans.get(span_id)
        if not span:
            return
        span["ended_at"] = datetime.now(timezone.utc).isoformat()
        span["status"] = status
        span["error"] = error
        span["metadata"] = metadata or {}
        span["output_summary"] = output_summary
        logger.debug("SpanTracer[%s] end %s → %s", self.pipeline_id, span_id, status)

    def get_spans(self) -> list[dict[str, Any]]:
        """获取所有 span。"""
        return list(self._spans.values())

    def cleanup(self) -> None:
        """清理资源。"""
        self._spans.clear()
