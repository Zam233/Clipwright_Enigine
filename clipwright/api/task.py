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
        # 成片时间线回写发起项目（E2E 2026-10-29）：proceed 带了 project_id，
        # 但旧实现只把它当工作区 id 用——任务跑完黑板里有 final_timeline，项目里仍是
        # 0 轨，导出页按钮恒禁用。这一步失败只记日志，不改任务结论。
        try:
            from clipwright.services.task_project_sync import sync_timeline_to_project

            sync_timeline_to_project(task_id, kernel.get(task_id) or task)
        except Exception:  # noqa: BLE001
            pass
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


@router.get("/{task_id}/tool-calls")
async def task_tool_calls(
    task_id: str,
    request: Request,
    stage: str = Query(default="", description="阶段名（edit/animation/...）；留空取最近一个"),
    attempt: int = Query(default=0, ge=0, description="第几次尝试；0 = 最新"),
    limit: int = Query(default=2000, ge=1, le=20000),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """读回某阶段的**全量**工具轨迹（PRD modern-agent-review M3）。

    `AgentResult.tool_calls` 只带 12 条采样 + `tool_calls_path`；真正的完整
    transcript 落在 `<workspace>/reports/tool_calls/<stage>-<n>.json`。前端 run 详情
    需要"点开看全部"，所以这里提供一个带分页的读接口（路径永远来自任务工作区，
    不接受客户端传路径）。
    """
    _enforce_task_owner(request, task_id)
    from clipwright.services.workspace import Workspace

    def _empty(stage_name: str, names: list[str]) -> dict:
        # 结构恒定：前端不用为"没有轨迹"写两套取值。
        return {"task_id": task_id, "stage": stage_name, "attempt": 0, "path": "",
                "total": 0, "offset": offset, "limit": limit, "count": 0,
                "internal": 0, "failed": 0, "items": [], "available": names}

    def _root_of() -> Any:
        """解析阶段写轨迹时用的那个工作区根。

        阶段侧 `_workspace_root_of` 是：`request.extra.workspace_root` 优先，其次任务工作区。
        从项目 `/proceed` 起来的任务，轨迹落在**项目**工作区（`reports/tool_calls/`），
        只查任务工作区会读成"没有轨迹"（E2E 2026-10-30 实测）。
        """
        extra: dict[str, Any] = {}
        try:
            task = _kernel().get(task_id)
            extra = dict(getattr(getattr(task, "request", None), "extra", None) or {})
        except Exception:  # noqa: BLE001 — kernel 不可用就按下面的兜底走
            extra = {}
        root = str(extra.get("workspace_root") or "")
        if root and (root_path := __import__("pathlib").Path(root)).is_dir():
            return root_path
        project_id = str(extra.get("proceed_project_id") or "")
        if project_id:
            candidate = Workspace(project_id).dirs().root
            if (candidate / "reports" / "tool_calls").is_dir():
                return candidate
        # 工作区目录本来就是 `<workspace_dir>/<slug>-<尾6>`；目录不在时 is_dir() 挡掉
        return Workspace(str(task_id)).dirs().root

    base = _root_of() / "reports" / "tool_calls"
    if not base.is_dir():
        return _empty(stage, [])

    available = sorted(p.stem for p in base.glob("*.json"))
    # stage 只参与文件名前缀匹配：剥掉路径分隔符与 ".."，避免穿越到别处。
    stage = (stage or "").replace("\\", "/").split("/")[-1].strip()
    if stage in (".", ".."):
        return _empty("", available)
    if stage:
        candidates = sorted(
            (p for p in base.glob(f"{stage}-*.json") if p.is_file()),
            key=lambda p: int("".join(ch for ch in p.stem.split("-")[-1] if ch.isdigit()) or 0),
        )
        if not candidates:
            return _empty(stage, available)
        path = candidates[-1] if attempt <= 0 else next(
            (p for p in candidates if p.stem.endswith(f"-{attempt}")), candidates[-1])
    else:
        latest = sorted((p for p in base.glob("*.json") if p.is_file()),
                        key=lambda p: p.stat().st_mtime)
        if not latest:
            return _empty("", available)
        path = latest[-1]

    try:
        import json as _json

        data = _json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — 读失败如实回报
        raise HTTPException(status_code=500, detail=f"读取轨迹失败: {exc}") from exc

    calls = data.get("calls") or []
    window = calls[offset: offset + limit]
    return {
        "task_id": task_id,
        "stage": str(data.get("stage") or stage),
        "attempt": int(data.get("attempt") or 0),
        "path": str(path),
        "total": len(calls),
        "offset": offset,
        "limit": limit,
        "count": len(window),
        "internal": sum(1 for c in calls if isinstance(c, dict) and c.get("source") == "internal"),
        "failed": sum(1 for c in calls if isinstance(c, dict)
                      and str(c.get("status") or "").lower() not in ("success", "ok", "")),
        "items": window,
        "available": available,
    }


@router.get("/{task_id}/runs")
async def task_runs(task_id: str, request: Request) -> dict:
    """列出该任务派过的全部子 agent run（PRD modern-agent-review M2）。

    前端 run 详情靠它做"可寻址子会话"：每个 run 都有 run_id，可以继续
    `GET /runs/{run_id}` 看全量、`POST /runs/{run_id}/message` 追问、
    `POST /runs/{run_id}/replay` 重放。
    """
    from clipwright.services import agent_run

    _enforce_task_owner(request, task_id)
    items = []
    for run in agent_run.list_runs(task_id):
        items.append({
            "run_id": run.get("run_id", ""),
            "kind": run.get("kind", ""),
            "stage": run.get("stage", ""),
            "role": run.get("role", ""),
            "status": run.get("status", ""),
            "attempt": run.get("attempt", 1),
            "duration_ms": run.get("duration_ms", 0),
            "cost_usd": run.get("cost_usd"),
            "degradations": len(run.get("degradations") or []),
            "tool_calls": len(run.get("tool_calls") or []),
            "tool_calls_path": run.get("tool_calls_path", ""),
            "report_path": (run.get("outputs") or {}).get("report_path", ""),
            "summary": str(run.get("summary") or "")[:400],
            "replayed": bool(run.get("replayed")),
        })
    items.sort(key=lambda r: (r["stage"], r["attempt"], r["run_id"]))
    return {"task_id": task_id, "count": len(items), "items": items}


@router.get("/{task_id}/runs/{run_id}")
async def task_run_detail(task_id: str, run_id: str, request: Request) -> dict:
    """单个 run 的全量结果（含降级、工具轨迹、失败原因）。"""
    from clipwright.services import agent_run

    _enforce_task_owner(request, task_id)
    run = agent_run.get_run(run_id) or agent_run.inspect(run_id=run_id, task_id=task_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"run 不存在: {run_id}")
    return {"task_id": task_id, "run": run}


class RunMessageBody(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


@router.post("/{task_id}/runs/{run_id}/message")
async def task_run_message(task_id: str, run_id: str, body: RunMessageBody,
                           request: Request) -> dict:
    """给某个 run 所在的阶段子会话留一条补充要求（下一次派发时并入 brief）。

    为什么要按 run 记而不是直接改任务：用户是对着"那一次子 agent 的产出"提要求，
    语义上属于该阶段的子会话；下一次派发同一阶段时它会被消费并上报事件。
    """
    from clipwright.services import agent_run

    _enforce_task_owner(request, task_id)
    run = agent_run.get_run(run_id) or agent_run.inspect(run_id=run_id, task_id=task_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"run 不存在: {run_id}")
    stage = str(run.get("stage") or run.get("role") or "")
    if not stage:
        raise HTTPException(status_code=400, detail="该 run 没有阶段名，无法投递补充要求")
    pending = agent_run.add_pending_note(task_id, stage, body.text)
    from clipwright.services.task_events import emit

    emit(task_id, "inject", f"追加要求 → {stage}（排队第 {pending} 条）",
         {"stage": stage, "run_id": run_id, "text": body.text[:500]}, span="subagent")
    return {"task_id": task_id, "run_id": run_id, "stage": stage, "pending": pending}


@router.post("/{task_id}/runs/{run_id}/replay")
async def task_run_replay(task_id: str, run_id: str, request: Request) -> dict:
    """重放一个 run：用同样的阶段与 brief **真重跑**（跳过幂等回放）。"""
    from clipwright.services import agent_run
    from clipwright.tool import surface

    _enforce_task_owner(request, task_id)
    run = agent_run.get_run(run_id) or agent_run.inspect(run_id=run_id, task_id=task_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"run 不存在: {run_id}")
    stage = str(run.get("stage") or "")
    if not stage:
        raise HTTPException(status_code=400, detail="该 run 没有阶段名，无法重放")
    task = _kernel().get(task_id)
    if task is None:
        raise HTTPException(status_code=409,
                            detail="任务不在运行态内存里（已结束或服务重启），无法重放该 run")
    result = await surface.execute(
        "delegate_agent",
        {"stage": stage, "brief": str(run.get("brief") or ""), "refresh": True},
        pipeline_id=task_id, task=task,
    )
    return {"task_id": task_id, "stage": stage, "status": result.get("status"),
            "replayed": bool(result.get("replayed")),
            "run": (result.get("output") or {}) if isinstance(result.get("output"), dict) else {}}


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
