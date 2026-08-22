"""AgentBus — 管线内 Agent 间通信总线。

提供:
  ・artifact 存储 (timeline, persona_config, plugin 等共享产物)
  ・事件发布 (agent 执行结果、场景信息、clip 列表)
  ・需求收集 (agent 间协作需求)
"""

from __future__ import annotations

from typing import Any

from clipwright.config import logger


class AgentBus:
    """管线级 Agent 通信总线 — 轻量内存实现。"""

    # 生产加固 1.8: 背压上限 — 长管线（40-60min）事件不再无限增长，
    # 超额淘汰最旧事件（get_events 为诊断用途，容忍截断）。
    _MAX_EVENTS = 2000
    _MAX_DEMANDS = 200

    def __init__(self, pipeline_id: str) -> None:
        self.pipeline_id = pipeline_id
        self._artifacts: dict[str, Any] = {}
        self._events: list[dict[str, Any]] = []
        self._demands: list[dict[str, Any]] = []

    # ── Artifact 存储 ──────────────────────────────

    def set_artifact(self, key: str, value: Any) -> None:
        """存储共享产物 (timeline, persona_config, plugin 等)。"""
        self._artifacts[key] = value

    def get_artifact(self, key: str) -> Any | None:
        """获取共享产物，不存在返回 None。"""
        return self._artifacts.get(key)

    # ── 事件发布 ───────────────────────────────────

    def publish(self, source: str, event_type: str, data: Any) -> None:
        """发布事件到总线（超额淘汰最旧，背压保护）。"""
        event = {"source": source, "type": event_type, "data": data}
        self._events.append(event)
        if len(self._events) > self._MAX_EVENTS:
            del self._events[: len(self._events) - self._MAX_EVENTS]
        logger.debug("AgentBus[%s] %s/%s: %s", self.pipeline_id, source, event_type, data)

    def get_events(self, source: str = "", event_type: str = "") -> list[dict[str, Any]]:
        """查询事件，可按 source/type 过滤。"""
        result = self._events
        if source:
            result = [e for e in result if e["source"] == source]
        if event_type:
            result = [e for e in result if e["type"] == event_type]
        return result

    # ── 需求收集 ───────────────────────────────────

    def add_demand(self, source: str, demand: dict[str, Any]) -> None:
        """Agent 发布协作需求（超额淘汰最旧）。"""
        self._demands.append({"source": source, **demand})
        if len(self._demands) > self._MAX_DEMANDS:
            del self._demands[: len(self._demands) - self._MAX_DEMANDS]

    def get_demands(self) -> list[dict[str, Any]]:
        """获取所有未处理的协作需求。"""
        return list(self._demands)
