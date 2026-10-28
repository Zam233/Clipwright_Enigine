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

#: 任务归属（task_id → user_id）。与 pipeline 的 `_pipeline_owners` 同思路：
#: off/token 模式下 uid 为空，不产生任何限制；jwt 模式下才是真的隔离。
_TASK_OWNERS: dict[str, str] = {}


def _enforce_task_owner(request: Request, task_id: str) -> None:
    """jwt 模式下校验任务归属（无主记录 403，安全优先）。

    为什么要显式做：task id 会出现在 URL 里，没有这条检查时，任何登录用户
    拿到别人的 id 就能读事件流、暂停、取消、甚至往别人任务里插话。
    """
    from clipwright.authz import current_user_id, is_admin

    uid = current_user_id(request)
    if uid is None or is_admin(request):
        return
    owner = _TASK_OWNERS.get(task_id)
    if owner is None:
        # 无主任务：只有"老入口转过来且登记过 owner"或单机模式才可能发生。
        # jwt 模式下宁可按无主处理（403），也不放行。
        raise HTTPException(status_code=403, detail=f"无权访问任务 {task_id}")
    if owner != uid:
        raise HTTPException(status_code=403, detail=f"无权访问任务 {task_id}")


def _register_owner(task_id: str, request: Request) -> None:
    from clipwright.authz import current_user_id

    uid = current_user_id(request)
    if uid:
        _TASK_OWNERS[task_id] = uid


def clear_owners() -> None:
    """测试用。"""
    _TASK_OWNERS.clear()


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
    _register_owner(task.id, request)   # 归属登记：后面的每个端点都靠它隔离
    await kernel.plan(task)
    if not payload.plan_only:
        _schedule(task.id)
    return {"task": task.to_public(), "planned": True, "started": not payload.plan_only}


@router.get("/{task_id}")
async def get_task(task_id: str, request: Request) -> dict:
    from clipwright.services.task_events import events

    _enforce_task_owner(request, task_id)
    task = _kernel().get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    return {"task": task.to_public(), "events": len(events(task_id))}


@router.get("/{task_id}/events")
async def get_task_events(task_id: str, request: Request,
                          since_seq: int = Query(default=0, ge=0)) -> dict:
    from clipwright.services.task_events import events_since

    _enforce_task_owner(request, task_id)
    items = [e.to_public() for e in events_since(task_id, since_seq)]
    return {"task_id": task_id, "since_seq": since_seq, "count": len(items), "items": items}


@router.get("/{task_id}/events/stream")
async def stream_task_events(task_id: str, request: Request) -> StreamingResponse:
    """SSE：与 trace 流同构（seq 游标 + data 帧）。"""
    from clipwright.services.task_events import events_since

    _enforce_task_owner(request, task_id)

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
    """继续一个暂停中的任务。

    PRD B11（2026-10-29 / G8）：**终态任务不允许 resume**。旧实现没有这个守卫，
    对已 done/failed/cancelled 的任务照样 `_schedule` 并返回 `status=running`——
    任务会被重新调度（重复花预算、重复写事件），而调用方只看到"已继续"。
    现在返回 409 + 结构化原因，前端据此禁用按钮并给出可读说明。
    """
    _enforce_task_owner(request, task_id)
    kernel = _kernel()
    task = kernel.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    if task.terminal:
        raise HTTPException(
            status_code=409,
            detail={
                "reason": "task_terminal",
                "status": task.status,
                "message": f"任务已结束（{task.status}），无法继续；如需重做请新建任务",
            },
        )
    _schedule(task_id)
    return {"task_id": task_id, "status": "running", "resumed": True}


@router.post("/{task_id}/pause")
async def pause_task(task_id: str, request: Request) -> dict:
    from clipwright.services.agent_kernel import KernelError

    _enforce_task_owner(request, task_id)
    try:
        task = _kernel().pause(task_id)
    except KernelError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"task_id": task_id, "status": task.status}


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: str, request: Request) -> dict:
    from clipwright.services.agent_kernel import KernelError

    _enforce_task_owner(request, task_id)
    try:
        task = _kernel().cancel(task_id)
    except KernelError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"task_id": task_id, "status": task.status}


@router.post("/{task_id}/inject")
async def inject_task(task_id: str, payload: InjectBody, request: Request) -> dict:
    from clipwright.services.agent_kernel import KernelError

    _enforce_task_owner(request, task_id)
    try:
        _kernel().inject(task_id, payload.text)
    except KernelError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"task_id": task_id, "injected": True}


@router.post("/{task_id}/plan")
async def replan_task(task_id: str, request: Request) -> dict:
    """重新生成计划（人工改主意 / 计划不满意时）。"""
    from clipwright.services.agent_kernel import KernelError

    _enforce_task_owner(request, task_id)
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


class ToProjectBody(BaseModel):
    """把任务产物放进一个新项目（供编辑器继续剪）。"""

    name: str = Field(default="", description="项目名；留空用任务标题")


@router.post("/{task_id}/to-project")
async def task_to_project(task_id: str, request: Request,
                          payload: ToProjectBody | None = None) -> dict:
    """把任务产出的 timeline 落成一个项目，返回 project_id（前端据此跳编辑器）。

    为什么需要这一步：任务是"出片"，编辑器是"继续剪"。两者之间必须有一步
    显式的"落成项目"，否则用户拿到一个 timeline 却没有地方打开它。
    """
    from clipwright.api.pipeline import _enforce_pipeline_owner  # noqa: F401 — 保持同一套鉴权语义
    from clipwright.api.project import _manager
    from clipwright.authz import current_user_id
    from clipwright.services.task_results import last_result

    _enforce_task_owner(request, task_id)

    result = last_result(task_id)
    timeline = None
    if isinstance(result, dict):
        timeline = result.get("timeline") or (result.get("shared_data") or {}).get("timeline")
    if not timeline:
        raise HTTPException(
            status_code=409,
            detail="这条任务还没有可编辑的 timeline（管线未产出，或任务未执行到出片步骤）",
        )

    task = _kernel().get(task_id)
    name = (payload.name if payload else "") or (task.request.topic if task else "") or "未命名任务"
    data = _manager.create(
        name=name[:80],
        timeline=timeline,
        persona_id=(task.request.persona_id if task else "") or None,
        plugin_id=(task.request.category_plugin_id if task else "") or None,
        owner_id=current_user_id(request) or "",
    )
    from clipwright import audit

    audit.record("project_create_from_task", current_user_id(request),
                 {"project_id": data["id"], "task_id": task_id})
    return {"task_id": task_id, "project_id": data["id"], "name": data.get("name", name)}


@router.get("/{task_id}/result")
async def task_result(task_id: str, request: Request) -> dict:
    """任务产物快照（timeline 概览 + 是否有可编辑时间线）。"""
    from clipwright.services.task_results import last_result

    _enforce_task_owner(request, task_id)
    result = last_result(task_id)
    timeline = None
    if isinstance(result, dict):
        timeline = result.get("timeline") or (result.get("shared_data") or {}).get("timeline")
    tracks = (timeline or {}).get("tracks") or []
    return {
        "task_id": task_id,
        "has_timeline": bool(timeline),
        "tracks": len(tracks),
        "clips": sum(len(t.get("clips") or []) for t in tracks),
        "duration_sec": float((timeline or {}).get("duration_sec") or 0.0),
        "output_path": (result or {}).get("output_path") or "",
    }


@router.get("")
async def list_tasks(request: Request, limit: int = Query(default=20, ge=1, le=100)) -> dict:
    """任务列表（C6 管理页用；内存 + 检查点目录）。"""
    from clipwright.authz import current_user_id, is_admin
    from clipwright.services.agent_kernel import _TASKS  # noqa: PLC2701 — 只读快照

    uid = current_user_id(request)
    admin = is_admin(request)
    visible = [
        t for t in _TASKS.values()
        if admin or uid is None or _TASK_OWNERS.get(t.id) in (None, uid)
    ]
    items = sorted(visible, key=lambda t: t.updated_at, reverse=True)[:limit]
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
