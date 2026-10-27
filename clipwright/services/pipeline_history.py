"""管线历史（只读）与取消标记（modern-agent PRD §4.1：从 `pipeline_v2` 迁出的中立模块）。

为什么要单独一份：`GET /api/pipeline/runs`、`/diagnostics`、`/cancel` 这些**历史只读**
能力与"能不能执行老管线"无关。它们原来长在 `PipelineOrchestratorV2` 旁边，于是
"删编排器"必然牵连"删历史查询"——这正是老管线一直删不干净的原因。

本模块只做三件事，且**不 import 任何执行器**：

1. 取消标记（协作式，供取消端点与历史快照使用）；
2. 运行记录（内存有界注册表 + Mongo 持久化历史的只读合并）；
3. 错误归类与用量汇总（纯函数，便于单测）。

执行能力的入口不在这里，也不该在这里。
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Optional

from clipwright.config import logger
from clipwright.schema.pipeline import PipelineStatus, PipelineStep

#: 协作式取消标记（进程内）
_CANCELLED: set[str] = set()

#: 内存运行注册表（有界，保持新→旧）
_run_registry: list[dict[str, Any]] = []
_run_registry_lock = threading.Lock()
_MAX_RUNS = 200


# ── 取消 ─────────────────────────────────────────


def mark_cancelled(pipeline_id: str) -> None:
    """标记一次运行被取消（协作式）。"""
    _CANCELLED.add(pipeline_id)


def is_cancelled(pipeline_id: str) -> bool:
    return pipeline_id in _CANCELLED


def clear_cancel(pipeline_id: str | None = None) -> None:
    """清除取消标记（测试/重置用）。"""
    if pipeline_id is None:
        _CANCELLED.clear()
    else:
        _CANCELLED.discard(pipeline_id)


# ── 运行记录 ─────────────────────────────────────


def _as_same_tz(dt: datetime, ref: datetime) -> datetime:
    """把 dt 规整到与 ref 相同的时区，容忍 naive/aware 混用。

    naive 值按「本地时间」惯例解释（外部构造/测试的 naive 通常表示本地时间），
    再转换到 ref 时区，避免跨时区偏差。
    """
    if dt.tzinfo is None:
        if ref.tzinfo is not None:
            local_tz = datetime.now(timezone.utc).astimezone().tzinfo
            return dt.replace(tzinfo=local_tz).astimezone(ref.tzinfo)
        return dt
    return dt.astimezone(ref.tzinfo) if ref.tzinfo is not None else dt


def _registry_snapshot() -> list[dict]:
    """深拷贝当前内存注册表（剔除内部时间戳字段）。"""
    with _run_registry_lock:
        return [{k: v for k, v in r.items() if k != "_start_dt"} for r in _run_registry]


def record_run_start(pipeline_id: str, topic: str) -> None:
    """记录一次运行开始（status=running，agents 空）。"""
    now = datetime.now(timezone.utc)
    with _run_registry_lock:
        _run_registry.append({
            "id": pipeline_id,
            "topic": topic or "",
            "status": "running",
            "duration_ms": 0,
            "started_at": now.isoformat(timespec="seconds"),
            "_start_dt": now,
            "agents": [],
        })
        del _run_registry[:-_MAX_RUNS]


def usage_totals_from_steps(steps: list[PipelineStep]) -> dict[str, Any]:
    """把各 Agent 的 `llm_usage` 汇总成一次运行的总账（纯函数）。

    给出 `{input_tokens, output_tokens, total_tokens, calls, by_agent, render_sec}`；
    渲染耗时取名为 render 的步骤（没有就为 0，不猜测）。
    """
    totals: dict[str, Any] = {
        "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
        "calls": 0, "by_agent": {},
    }
    render_ms = 0
    for step in steps or []:
        result = getattr(step, "result", None) or {}
        if not isinstance(result, dict):
            continue
        usage = result.get("llm_usage") or {}
        if isinstance(usage, dict) and usage:
            in_tokens = int(usage.get("input_tokens") or 0)
            out_tokens = int(usage.get("output_tokens") or 0)
            calls = int(usage.get("calls") or (1 if (in_tokens or out_tokens) else 0))
            totals["input_tokens"] += max(0, in_tokens)
            totals["output_tokens"] += max(0, out_tokens)
            totals["calls"] += max(0, calls)
            totals["by_agent"][getattr(step, "agent_name", "") or "unknown"] = {
                "input_tokens": max(0, in_tokens),
                "output_tokens": max(0, out_tokens),
            }
        if "render" in str(getattr(step, "agent_name", "") or "").lower():
            render_ms = max(render_ms, int(getattr(step, "duration_ms", 0) or 0))
    totals["total_tokens"] = totals["input_tokens"] + totals["output_tokens"]
    totals["render_sec"] = round(render_ms / 1000.0, 2) if render_ms else 0.0
    return totals


def record_run_complete(pipeline_id: str, status: str,
                        steps: list[PipelineStep]) -> None:
    """更新运行结束（成功与失败分支共用）。

    把 PipelineStep 列表转换成前端期望的 agent 跨度
    `[{agent, start, dur, status}]`，并附上用量总账 `usage`。
    """
    spans: list[dict] = []
    usage = usage_totals_from_steps(steps)
    with _run_registry_lock:
        for run in _run_registry:
            if run["id"] != pipeline_id:
                continue
            start_dt: Optional[datetime] = run.get("_start_dt")
            for s in steps or []:
                start_ms = 0
                if start_dt is not None and s.started_at is not None:
                    s_start = _as_same_tz(s.started_at, start_dt)
                    start_ms = max(0, int((s_start - start_dt).total_seconds() * 1000))
                span_status = "ok"
                if s.status == PipelineStatus.FAILED:
                    span_status = "fail"
                elif s.retry_count > 0:
                    span_status = "retry"
                spans.append({
                    "agent": s.agent_name,
                    "start": start_ms,
                    "dur": int(s.duration_ms or 0),
                    "status": span_status,
                })
            now = datetime.now(timezone.utc)
            run["status"] = status
            run["duration_ms"] = int((now - start_dt).total_seconds() * 1000) if start_dt else 0
            run["agents"] = spans
            run["usage"] = usage
            run.pop("_start_dt", None)
            return
        # 开始记录缺失（如直接调用）→ 补一条完成的记录
        _run_registry.append({
            "id": pipeline_id, "topic": "", "status": status, "duration_ms": 0,
            "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "agents": spans, "usage": usage,
        })


def clear_run_records() -> None:
    """清空内存注册表（测试用）。"""
    with _run_registry_lock:
        _run_registry.clear()


def _mongo_record_to_run(m: Any) -> Optional[dict]:
    """把 Mongo PipelineModel 文档映射为前端期望的 run 形状（防御式）。"""
    try:
        request = getattr(m, "request", None) or {}
        topic = request.get("topic", "") if isinstance(request, dict) else ""
        created = getattr(m, "created_time", None)
        updated = getattr(m, "updated_time", None)
        if not isinstance(created, datetime) or not isinstance(updated, datetime):
            return None
        duration_ms = max(0, int((_as_same_tz(updated, created) - created).total_seconds() * 1000))
        spans: list[dict] = []
        for s in (getattr(m, "steps", None) or []):
            if not isinstance(s, dict):
                continue
            start_ms = 0
            try:
                s_start = s.get("started_at")
                if s_start:
                    start_ms = max(0, int(
                        (_as_same_tz(datetime.fromisoformat(str(s_start)), created) - created
                         ).total_seconds() * 1000))
            except (ValueError, TypeError):
                start_ms = 0
            span_status = "ok"
            if s.get("status") == "failed":
                span_status = "fail"
            elif s.get("retry_count", 0) > 0:
                span_status = "retry"
            spans.append({
                "agent": s.get("agent_name", ""),
                "start": start_ms,
                "dur": int(s.get("duration_ms") or 0),
                "status": span_status,
            })
        return {
            "id": str(m.id),
            "topic": topic,
            "status": getattr(m, "status", "completed") or "completed",
            "duration_ms": duration_ms,
            "started_at": created.isoformat(timespec="seconds"),
            "agents": spans,
        }
    except Exception as e:  # noqa: BLE001 — 单条映射失败不影响整体
        logger.debug("Mongo run 记录映射失败: %s", e)
        return None


def get_run_records(limit: int = 50) -> list[dict]:
    """获取运行记录（新→旧）。

    内存注册表始终返回（当前进程真实执行历史）；Mongo 可用时再合并持久化历史
    （去重，内存优先）。
    """
    records = _registry_snapshot()
    try:
        from clipwright.context import mongo as mongo_ctx

        if mongo_ctx.is_connected:
            from clipwright.models.pipeline_model import PipelineModel

            memory_ids = {r["id"] for r in records}
            docs = PipelineModel.find_many({}, sort=[("created_time", -1)], limit=limit)
            for m in docs:
                if str(m.id) in memory_ids:
                    continue
                rec = _mongo_record_to_run(m)
                if rec:
                    records.append(rec)
    except Exception as e:  # noqa: BLE001 — Mongo 不可用不影响内存记录
        logger.debug("GET /runs Mongo 读取失败，仅返回内存记录: %s", e)
    return records[:limit]


# ── 错误归类（纯函数）───────────────────────────


def categorize_error(error: str) -> str:
    """把错误文本归成 `transient` / `fatal` / `permanent`（诊断端点用）。

    分类顺序：transient → fatal → permanent。测不出特征时归 permanent：
    「不可重试」比「可重试」安全（不会让主 agent 在同一坑里反复花钱）。
    """
    lowered = str(error or "").lower()
    transient_patterns = ["timeout", "超时", "rate limit", "too many", "暂时", "retry",
                          "connection", "reset", "timed out"]
    fatal_patterns = ["memoryerror", "out of memory", "oom", "mongo", "not connected",
                      "disk full", "磁盘", "database"]
    for p in transient_patterns:
        if p in lowered:
            return "transient"
    for p in fatal_patterns:
        if p in lowered:
            return "fatal"
    return "permanent"


def truncate_shared_data(shared: dict[str, Any], max_len: int = 5000) -> dict[str, Any]:
    """递归截断 shared_data 中的字符串字段，保留 dict/list 结构。"""
    def _truncate(v: Any) -> Any:
        if isinstance(v, str):
            if len(v) > max_len:
                return v[:max_len] + f"...[截断, 原长{len(v)}]"
            return v
        if isinstance(v, dict):
            return {k: _truncate(val) for k, val in v.items()}
        if isinstance(v, list):
            return [_truncate(item) for item in v]
        return v

    return {k: _truncate(v) for k, v in shared.items()}


__all__ = ["categorize_error", "clear_cancel", "clear_run_records", "get_run_records",
           "is_cancelled", "mark_cancelled", "record_run_complete", "record_run_start",
           "truncate_shared_data", "usage_totals_from_steps"]
