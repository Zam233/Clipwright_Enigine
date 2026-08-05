"""全链路追踪服务 — Span 模型 + MongoDB 持久化。

Layer 2: 每个 Agent/Tool/LLM 调用记录为 Span，构建调用树。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from clipwright.config import TIME_ZONE, logger
from clipwright.context import mongo
from clipwright.models.pipeline_model import TraceSpanModel


class SpanTracer:
    """Span 追踪器 — 创建和管理追踪 span。

    用法:
        tracer = SpanTracer(pipeline_id)
        span = tracer.start_span("agent", "structure", "结构分析")
        # ... do work ...
        tracer.end_span(span, status="ok", output_summary="生成 5 个场景")
    """

    def __init__(self, pipeline_id: str) -> None:
        self.pipeline_id = pipeline_id
        self._active_spans: dict[str, dict] = {}  # span_id → span_data

    def start_span(
        self,
        span_type: str,
        agent_name: str,
        span_name: str,
        parent_span_id: str = "",
        input_summary: str = "",
        metadata: dict | None = None,
    ) -> str:
        """开始一个新的追踪 span。

        Args:
            span_type: "agent" / "llm" / "tool" / "render"
            agent_name: 所属 agent
            span_name: 描述
            parent_span_id: 父 span ID（为空则挂到 pipeline 根）
            input_summary: 输入摘要
            metadata: 扩展数据

        Returns:
            span_id
        """
        span_id = f"span_{uuid.uuid4().hex[:12]}"
        now = datetime.now(tz=TIME_ZONE) if TIME_ZONE else datetime.now(timezone.utc)

        self._active_spans[span_id] = {
            "span_id": span_id,
            "pipeline_id": self.pipeline_id,
            "parent_span_id": parent_span_id,
            "span_type": span_type,
            "agent_name": agent_name,
            "span_name": span_name,
            "started_at": now,
            "completed_at": None,
            "duration_ms": 0,
            "status": "running",
            "input_summary": input_summary[:200],
            "output_summary": "",
            "error": None,
            "metadata": metadata or {},
        }
        return span_id

    def end_span(
        self,
        span_id: str,
        status: str = "ok",
        output_summary: str = "",
        error: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        """结束一个 span。"""
        span = self._active_spans.get(span_id)
        if not span:
            logger.warning("SpanTracer: span %s not found", span_id)
            return

        now = datetime.now(tz=TIME_ZONE) if TIME_ZONE else datetime.now(timezone.utc)
        span["completed_at"] = now
        if span["started_at"]:
            span["duration_ms"] = int((now - span["started_at"]).total_seconds() * 1000)
        span["status"] = status
        span["output_summary"] = output_summary[:200] if output_summary else ""
        span["error"] = error
        if metadata:
            span["metadata"].update(metadata)

        # 持久化到 MongoDB
        self._persist_span(span)

    def _persist_span(self, span: dict) -> None:
        """持久化 span 到 MongoDB。"""
        try:
            if not mongo.is_connected:
                return
            model = TraceSpanModel(
                _id=span["span_id"],
                pipeline_id=span["pipeline_id"],
                parent_span_id=span["parent_span_id"],
                span_type=span["span_type"],
                agent_name=span["agent_name"],
                span_name=span["span_name"],
                started_at=span["started_at"],
                completed_at=span["completed_at"],
                duration_ms=span["duration_ms"],
                status=span["status"],
                input_summary=span["input_summary"],
                output_summary=span["output_summary"],
                error=span.get("error"),
                metadata=span.get("metadata", {}),
            )
            model.insert()
        except Exception as e:
            logger.warning("Span 持久化失败: %s", e)

    def get_span_tree(self) -> list[dict]:
        """获取当前 pipeline 的 span 树。"""
        try:
            if mongo.is_connected:
                spans = TraceSpanModel.find_many(
                    {"pipeline_id": self.pipeline_id},
                    sort=[("started_at", 1)],
                )
                return [s.to_session_dict() if hasattr(s, 'to_session_dict') else s.to_dict() for s in spans]
        except Exception:
            pass
        return list(self._active_spans.values())

    def get_pipeline_timeline(self) -> list[dict]:
        """获取时间线格式的 span 数据（用于前端甘特图）。"""
        spans = self.get_span_tree()
        timeline = []
        for s in spans:
            if s.get("status") == "running":
                continue
            timeline.append({
                "id": s.get("span_id", ""),
                "name": s.get("span_name", ""),
                "agent": s.get("agent_name", ""),
                "type": s.get("span_type", ""),
                "start": s.get("started_at", ""),
                "duration_ms": s.get("duration_ms", 0),
                "status": s.get("status", ""),
                "parent_id": s.get("parent_span_id", ""),
            })
        return timeline

    def cleanup(self) -> None:
        """清理未完成的 span。"""
        now = datetime.now(tz=TIME_ZONE) if TIME_ZONE else datetime.now(timezone.utc)
        for span_id, span in list(self._active_spans.items()):
            if span.get("status") == "running":
                span["completed_at"] = now
                if span["started_at"]:
                    span["duration_ms"] = int((now - span["started_at"]).total_seconds() * 1000)
                span["status"] = "interrupted"
                span["error"] = "pipeline ended before span completed"
                self._persist_span(span)
        self._active_spans.clear()
