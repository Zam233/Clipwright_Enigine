"""执行追踪 — 实时记录 Pipeline 中各 Agent 的 LLM 调用、Tool 执行、插件使用。"""

from __future__ import annotations

import json
import time
from typing import Any, Optional

# 全局追踪存储: pipeline_id → list[trace_event]
_traces: dict[str, list[dict[str, Any]]] = {}


def create_trace(pipeline_id: str) -> None:
    """创建新的追踪记录。"""
    _traces[pipeline_id] = []


def add_event(
    pipeline_id: str,
    agent: str,
    event_type: str,
    summary: str,
    detail: Any = None,
) -> None:
    """添加一条追踪事件。

    Args:
        pipeline_id: 管线 ID
        agent: Agent 名称
        event_type: llm / tool / skill / plugin / agent_start / agent_end / info
        summary: 简短描述
        detail: 详细数据（可选）
    """
    if pipeline_id not in _traces:
        _traces[pipeline_id] = []
    _traces[pipeline_id].append({
        "time": time.time(),
        "agent": agent,
        "type": event_type,
        "summary": summary,
        "detail": detail,
    })


def get_events(pipeline_id: str, since: float = 0) -> list[dict[str, Any]]:
    """获取指定管线自 since 以来的追踪事件。"""
    events = _traces.get(pipeline_id, [])
    return [e for e in events if e["time"] >= since]


def get_all_events(pipeline_id: str) -> list[dict[str, Any]]:
    return _traces.get(pipeline_id, [])


def clear(pipeline_id: str) -> None:
    _traces.pop(pipeline_id, None)


def format_tool_call(tool_name: str, params: dict) -> str:
    params_str = ", ".join(f"{k}={v}" for k, v in list(params.items())[:3])
    return f"{tool_name}({params_str})"


def format_llm_call(model: str, prompt_preview: str) -> str:
    return f"LLM({model}) → {prompt_preview[:80]}"
