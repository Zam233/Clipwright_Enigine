"""Task API（路线 C / C2，PRD §8.4）。

设计取舍：**任务执行走后台协程**，HTTP 只负责建单/查询/控制。
因为一条任务可能跑十几分钟，同步 HTTP 会把连接占死、把超时留给我们最不想出错的地方。

事件读取与 SSE：与管线 trace 流同构（`seq` 游标 + `data:` 帧），前端可以复用
`connectSseStream`，C4 直接把订阅地址从 trace 换成 events 即可。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from clipwright.schema.task import TaskRequest

router = APIRouter(prefix="/api/task", tags=["task"])

#: 任务后台协程（用于"同一任务不并发跑两份"）
_RUNNING: dict[str, asyncio.Task] = {}


class CreateTaskBody(BaseModel):
    topic: str = Field(description="用户一句话需求")
    persona_id: str = ""
    category_plugin_id: str = ""
    duration_sec: float = 0.0
    plan_only: bool = True
    extra: dict[str, Any] = Field(default_factory=dict)


class InjectBody(BaseModel):
    text: str = Field(description="人工注入的指令或修正")


def _kernel():
    from clipwright.services.agent_kernel import AgentKernel

    return AgentKernel()


async def _run_background(task_id: str) -> None:
    from clipwright.services.agent_kernel import KernelError

    kernel = _kernel()
    task = kernel.get(task_id)
    if task is None:
        return
    try:
        await kernel.run(task)
    except KernelError as e:
        from clipwright.services.task_events import emit

        emit(task_id, "task_end", f"任务执行失败: {e}", {"error": str(e)})
    except Exception as e:  # noqa: BLE001 — 后台任务异常必须留痕，不能静默
        from clipwright.config import logger
        from clipwright.services.task_events import emit

        logger.exception("任务执行异常 %s", task_id)
        emit(task_id, "task_end", f"任务异常: {type(e).__name__}", {"error": str(e)[:500]})
    finally:
        _RUNNING.pop(task_id, None)


def _schedule(task_id: str) -> None:
    existing = _RUNNING.get(task_id)
    if existing is not None and not existing.done():
        return  # 幂等：同一任务不并发跑两份
    _RUNNING[task_id] = asyncio.create_task(_run_background(task_id))


@router.post("")
async def create_task(payload: CreateTaskBody, request: Request) -> dict:
    """创建任务。`plan_only=true`（默认）只出计划，不执行。"""
    request_value = TaskRequest(
        topic=payload.topic,
        persona_id=payload.persona_id,
        category_plugin_id=payload.category_plugin_id,
        duration_sec=payload.duration_sec,
        plan_only=payload.plan_only,
        extra=payload.extra,
    )
    kernel = _kernel()
    task = kernel.create(request_value)
    await kernel.plan(task)
    if not payload.plan_only:
        _schedule(task.id)
    return {"task": task.to_public(), "planned": True, "started": not payload.plan_only}


@router.get("/{task_id}")
async def get_task(task_id: str, request: Request) -> dict:
    from clipwright.services.task_events import events

    task = _kernel().get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    return {"task": task.to_public(), "events": len(events(task_id))}


@router.get("/{task_id}/events")
async def get_task_events(task_id: str, request: Request,
                          since_seq: int = Query(default=0, ge=0)) -> dict:
    from clipwright.services.task_events import events_since

    items = [e.to_public() for e in events_since(task_id, since_seq)]
    return {"task_id": task_id, "since_seq": since_seq, "count": len(items), "items": items}


@router.get("/{task_id}/events/stream")
async def stream_task_events(task_id: str, request: Request) -> StreamingResponse:
    """SSE：与 trace 流同构（seq 游标 + data 帧）。"""
    from clipwright.services.task_events import events_since

    async def generator():
        last_seq = 0
        while True:
            if await request.is_disconnected():
                return
            for event in events_since(task_id, last_seq):
                last_seq = event.seq
                yield f"data: {json.dumps(event.to_public(), ensure_ascii=False)}\n\n"
                if event.type == "task_end":
                    return
            await asyncio.sleep(0.5)

    return StreamingResponse(generator(), media_type="text/event-stream")


@router.post("/{task_id}/resume")
async def resume_task(task_id: str, request: Request) -> dict:
    kernel = _kernel()
    task = kernel.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    _schedule(task_id)
    return {"task_id": task_id, "status": "running", "resumed": True}


@router.post("/{task_id}/pause")
async def pause_task(task_id: str, request: Request) -> dict:
    from clipwright.services.agent_kernel import KernelError

    try:
        task = _kernel().pause(task_id)
    except KernelError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"task_id": task_id, "status": task.status}


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: str, request: Request) -> dict:
    from clipwright.services.agent_kernel import KernelError

    try:
        task = _kernel().cancel(task_id)
    except KernelError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"task_id": task_id, "status": task.status}


@router.post("/{task_id}/inject")
async def inject_task(task_id: str, payload: InjectBody, request: Request) -> dict:
    from clipwright.services.agent_kernel import KernelError

    try:
        _kernel().inject(task_id, payload.text)
    except KernelError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"task_id": task_id, "injected": True}


@router.post("/{task_id}/plan")
async def replan_task(task_id: str, request: Request) -> dict:
    """重新生成计划（人工改主意 / 计划不满意时）。"""
    from clipwright.services.agent_kernel import KernelError

    kernel = _kernel()
    task = kernel.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    if task.terminal:
        raise HTTPException(status_code=409, detail="任务已结束，不能重新规划")
    try:
        await kernel.plan(task)
    except KernelError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"task": task.to_public()}


@router.get("")
async def list_tasks(request: Request, limit: int = Query(default=20, ge=1, le=100)) -> dict:
    """任务列表（C6 管理页用；内存 + 检查点目录）。"""
    from clipwright.services.agent_kernel import _TASKS  # noqa: PLC2701 — 只读快照

    items = sorted(_TASKS.values(), key=lambda t: t.updated_at, reverse=True)[:limit]
    return {
        "count": len(items),
        "items": [
            {"id": t.id, "status": t.status, "topic": t.request.topic,
             "updated_at": t.updated_at.isoformat(),
             "steps": len(t.plan.steps) if t.plan else 0}
            for t in items
        ],
    }


def _new_id() -> str:  # pragma: no cover - 预留
    return f"task_{uuid.uuid4().hex[:10]}"
