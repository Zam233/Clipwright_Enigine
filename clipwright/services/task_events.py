"""任务事件流（路线 C / C2，PRD F4）。

三条性质决定了它必须是 append-only：

1. **可回放**：前端重连、Kernel 续跑、事后排查都靠"从头放一遍"，不改历史。
2. **可推导**：任务/Step 的状态由事件推导，`Task.status` 只是缓存——所以事件不能改。
3. **可审计**：`tool_call`/`approval_*` 留在流里，回答"谁在什么时候让机器干了什么"。

存储：内存热层（按任务有界）+ Mongo 旁路（失败只告警，不阻断）。C2 只做这两层，
事件压缩/归档留到 C6（现在没有性能压力，过早优化会把语义搞乱）。
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, Optional

from clipwright.config import logger
from clipwright.schema.task import EVENT_TYPES, TaskEvent

_MAX_TASKS = 200
_MAX_EVENTS_PER_TASK = 2000

_STREAMS: dict[str, list[TaskEvent]] = defaultdict(list)


class EventError(RuntimeError):
    """事件写入失败（类型不合法等）。"""


def emit(
    task_id: str,
    event_type: str,
    message: str = "",
    detail: Optional[dict[str, Any]] = None,
    *,
    trace_id: str = "",
    run_id: str = "",
    span: str = "",
    causation_id: str = "",
) -> TaskEvent:
    """追加一条事件（同步；调用方不需要 await）。"""
    if event_type not in EVENT_TYPES:
        raise EventError(
            f"未知事件类型: {event_type}（可选 {EVENT_TYPES}）——新增类型必须同步前端与回放器"
        )
    stream = _STREAMS[task_id]
    event = TaskEvent(
        seq=(stream[-1].seq + 1) if stream else 1,
        type=event_type,
        message=message,
        detail=dict(detail or {}),
        trace_id=trace_id or task_id,
        run_id=run_id,
        span=span,
        causation_id=causation_id,
    )
    stream.append(event)
    if len(stream) > _MAX_EVENTS_PER_TASK:
        del stream[: len(stream) - _MAX_EVENTS_PER_TASK]
    if len(_STREAMS) > _MAX_TASKS:
        # 淘汰最旧的整条任务流（与 ledger 同策略：有界，避免长跑进程吃内存）
        oldest = next(iter(_STREAMS))
        if oldest != task_id:
            _STREAMS.pop(oldest, None)
    _persist(task_id, event)
    return event


async def aemit(
    task_id: str,
    event_type: str,
    message: str = "",
    detail: Optional[dict[str, Any]] = None,
    *,
    trace_id: str = "",
    run_id: str = "",
    span: str = "",
    causation_id: str = "",
) -> TaskEvent:
    """事件循环内的写法（Mongo 写入放线程池，不阻塞）。"""
    if _mongo_ready():
        event = _build(task_id, event_type, message, detail,
                       trace_id=trace_id, run_id=run_id, span=span,
                       causation_id=causation_id)
        await asyncio.to_thread(_persist, task_id, event)
        return event
    return emit(task_id, event_type, message, detail, trace_id=trace_id,
                run_id=run_id, span=span, causation_id=causation_id)


def _build(task_id: str, event_type: str, message: str,
           detail: Optional[dict[str, Any]], *, trace_id: str = "",
           run_id: str = "", span: str = "",
           causation_id: str = "") -> TaskEvent:
    stream = _STREAMS[task_id]
    event = TaskEvent(
        seq=(stream[-1].seq + 1) if stream else 1,
        type=event_type,
        message=message,
        detail=dict(detail or {}),
        trace_id=trace_id or task_id,
        run_id=run_id,
        span=span,
        causation_id=causation_id,
    )
    stream.append(event)
    if len(stream) > _MAX_EVENTS_PER_TASK:
        del stream[: len(stream) - _MAX_EVENTS_PER_TASK]
    return event


def events(task_id: str) -> list[TaskEvent]:
    return list(_STREAMS.get(task_id) or [])


def events_since(task_id: str, seq: int = 0) -> list[TaskEvent]:
    return [e for e in events(task_id) if e.seq > seq]


def last_seq(task_id: str) -> int:
    stream = _STREAMS.get(task_id)
    return stream[-1].seq if stream else 0


def clear(task_id: str = "") -> None:
    if task_id:
        _STREAMS.pop(task_id, None)
    else:
        _STREAMS.clear()


def _mongo_ready() -> bool:
    try:
        from clipwright.context import mongo

        return bool(getattr(mongo, "is_connected", False))
    except Exception:  # noqa: BLE001
        return False


def _persist(task_id: str, event: TaskEvent) -> None:
    """Mongo 旁路：写失败只告警（事件流不能因为落库失败而中断）。

    **不能在事件循环里同步写**：`emit()` 到处都是同步调用，若这里直接
    `insert_one`，Mongo 一通就会周期性卡住整个任务（长任务尤其明显）。
    所以：有运行中的循环就丢到线程池（fire-and-forget），没有循环（单测/脚本）
    才同步写。
    """
    if not _mongo_ready():
        return
    payload = {"task_id": task_id, **event.to_public()}

    def _write() -> None:
        try:
            from clipwright.context import mongo

            mongo.db["task_events"].insert_one(payload)
        except Exception as e:  # noqa: BLE001
            logger.warning("任务事件落库失败 %s: %s", task_id, e)

    try:
        import asyncio

        loop = asyncio.get_running_loop()
    except RuntimeError:
        _write()
        return
    # 线程池执行：不阻塞事件循环，也不占用默认执行器的顺序（异常已在 _write 内消化）
    loop.run_in_executor(None, _write)
