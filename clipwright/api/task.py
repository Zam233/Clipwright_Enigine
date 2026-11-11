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
from typing import Any, Optional

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


class AnswerBody(BaseModel):
    """SP15.5：回答 ask_user 的问题。option_id 与 text 至少给一个。"""

    question_id: str = Field(min_length=1, max_length=120)
    option_id: Optional[str] = Field(default=None, max_length=80)
    text: Optional[str] = Field(default=None, max_length=2000)


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

    def _emit_failed(message: str, error: str, reason: str) -> None:
        """BE-03（审计 PRD §5E P2）：失败/异常路径的 task_end 也必须带
        completed/unfinished —— 前端按契约读这两个键，缺了就只能用空数组
        覆盖后端状态，终态展示会卡在异常分支。"""
        from clipwright.services.task_events import emit
        from clipwright.schema.task import TaskFinish, TerminalReason

        cur = kernel.get(task_id) or task
        try:
            cur.status = "failed"
            cur.terminal_reason = TerminalReason.model_error
            prev = cur.finish
            cur.finish = TaskFinish(
                conclusion=message,
                completed=list(getattr(prev, "completed", []) or []),
                unfinished=(list(getattr(prev, "unfinished", []) or [])
                            or [f"task_error:{reason}"]),
                reason=reason,
                terminal_reason=TerminalReason.model_error,
            )
            kernel._checkpoint(cur)  # noqa: SLF001 — 与正常收尾同一份真相
        except Exception:  # noqa: BLE001 — 状态补齐失败不阻断事件
            pass
        emit(task_id, "task_end", message, {
            "error": error,
            "reason": reason,
            "completed": list(getattr(getattr(cur, "finish", None), "completed", []) or []),
            "unfinished": list(getattr(getattr(cur, "finish", None), "unfinished", []) or []),
            "terminal_reason": "model_error",
        })

    try:
        await kernel.run(task)
    except KernelError as e:
        _emit_failed(f"任务执行失败: {e}", str(e), "failed")
    except Exception as e:  # noqa: BLE001 — 后台任务异常必须留痕，不能静默
        from clipwright.config import logger

        logger.exception("任务执行异常 %s", task_id)
        _emit_failed(f"任务异常: {type(e).__name__}", str(e)[:500], "exception")
    finally:
        # 成片时间线回写发起项目（E2E 2026-10-29）：proceed 带了 project_id，
        # 但旧实现只把它当工作区 id 用——任务跑完黑板里有 final_timeline，项目里仍是
        # 0 轨，导出页按钮恒禁用。
        # BE-04（审计 PRD §5E P2）：旧实现 `except Exception: pass` 把回写失败**静默
        # 吞掉**，用户看到"任务成功"但项目时间线是空的、导出按钮不可用，且无任何日志。
        # 现在至少记 error，并把失败写进任务结论（不谎称完整成功）。
        try:
            from clipwright.services.task_project_sync import sync_timeline_to_project

            sync_timeline_to_project(task_id, kernel.get(task_id) or task)
        except Exception as exc:  # noqa: BLE001 — 回写失败要可见
            from clipwright.config import logger
            from clipwright.services.task_events import emit

            logger.error("成片时间线回写项目失败 %s: %s", task_id, exc, exc_info=True)
            try:
                emit(task_id, "warning", f"成片未写入项目：{type(exc).__name__}", {
                    "scope": "timeline_sync", "error": str(exc)[:300],
                })
            except Exception:  # noqa: BLE001 — 连事件都发不出时只保留日志
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
async def get_task_events(
    task_id: str,
    request: Request,
    since_seq: int = Query(default=0, ge=0, description="只返回 seq 大于它的事件（续传）"),
    before_seq: int = Query(default=0, ge=0,
                            description="只返回 seq 小于它的事件（向前翻历史）"),
    limit: int = Query(default=0, ge=0, le=5000,
                       description="最多返回条数；0 = 不限（默认沿用旧行为）"),
) -> dict:
    """任务事件（支持续传 + 向前分页）。

    三种用法：
    - `since_seq=N`：拿 N 之后的新事件（SSE 断线补拉）；
    - `before_seq=N&limit=200`：拿 N 之前的历史（前端"往上翻"）；
    - 都不传：全量（旧行为，兼容既有调用方）。

    `has_more` / `oldest_available_seq` / `latest_seq` 是**恢复元数据**：
    前端据此知道"还能不能往上翻""我这份是不是全部"，缺口也能显式提示，
    而不是把"没有更多"和"服务端丢了"混为一谈。
    """
    from clipwright.services.task_events import events, events_since, spilled_max_seq

    _enforce_task_owner(request, task_id)
    if since_seq > 0:
        merged = events_since(task_id, since_seq)
    else:
        merged = events(task_id, include_spilled=True)
    if before_seq > 0:
        merged = [e for e in merged if e.seq < before_seq]
    total_after_filter = len(merged)
    has_more = False
    if limit > 0 and total_after_filter > limit:
        # `since_seq` 是"往后追"，取最早的 limit 条（保证不丢中间段）；
        # `before_seq` 是"往前翻"，取最晚的 limit 条（紧邻当前窗口）。
        merged = merged[:limit] if before_seq <= 0 else merged[-limit:]
        has_more = True
    items = [e.to_public() for e in merged]
    seqs = [e.seq for e in merged]
    latest = seqs[-1] if seqs else int(spilled_max_seq(task_id) or 0)
    return {
        "task_id": task_id,
        "since_seq": since_seq,
        "before_seq": before_seq,
        "limit": limit,
        "count": len(items),
        "items": items,
        "has_more": has_more or (total_after_filter > len(merged)),
        "oldest_available_seq": seqs[0] if seqs else 0,
        "latest_seq": latest,
        "spilled_max_seq": int(spilled_max_seq(task_id) or 0),
    }


@router.get("/{task_id}/events/stream")
async def stream_task_events(
    task_id: str, request: Request,
    since_seq: int = Query(default=0, ge=0,
                          description="只推送 seq 大于它的事件；已水合过就不必全量回放"),
) -> StreamingResponse:
    """SSE：与 trace 流同构（seq 游标 + data 帧）。

    `since_seq` 是恢复语义的一部分：前端先 REST 水合，再从这里接着收。
    终态任务如果游标已经追平，直接结束流——否则 EventSource 重连会永远收到空流，
    前端也就永远触发重连（实测会形成“回放→EOF→重连”的死循环）。
    """
    from clipwright.services.task_events import events_since, last_seq

    _enforce_task_owner(request, task_id)
    # 直接调用路由函数（测试/内部）时拿到的是 Query 默认对象而不是 int；
    # 只在 FastAPI 注入路径上它才是数字。这里归一化，避免 `>` 直接 TypeError。
    start_seq = since_seq if isinstance(since_seq, int) else 0
    terminal = _kernel().get(task_id)
    if start_seq > 0 and terminal is not None and terminal.terminal \
            and last_seq(task_id) <= start_seq:
        return StreamingResponse(iter(()), media_type="text/event-stream")

    async def generator():
        last = start_seq
        while True:
            if await request.is_disconnected():
                return
            pending = events_since(task_id, last)
            for event in pending:
                last = event.seq
                yield f"data: {json.dumps(event.to_public(), ensure_ascii=False)}\n\n"
                if event.type == "task_end":
                    return
            task = _kernel().get(task_id)
            if not pending and task is not None and task.terminal:
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
    if task.status == "waiting_user":
        from clipwright.services import ask_user

        pend = ask_user.pending("task", task_id)
        if pend:
            raise HTTPException(
                status_code=409,
                detail={"reason": "waiting_answer",
                        "question_id": pend[0]["question_id"],
                        "message": "任务正在等待回答，请先回答上面的问题或取消任务"},
            )
        # 服务重启后：旧协程已不在，问题已判 expired → 回落到 paused 再放行
        task.status = "paused"
        try:
            from clipwright.services.task_events import emit

            emit(task_id, "narration", "原问题已失效，任务继续",
                 {"kind": "question_expired_on_restart"}, span="kernel")
        except Exception:  # noqa: BLE001 — 事件不可用不影响续跑
            pass
        if hasattr(task, "touch"):
            task.touch()
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


@router.post("/{task_id}/answer")
async def answer_task_question(task_id: str, payload: AnswerBody,
                               request: Request) -> dict:
    """回答主 agent 提出的问题（PRD §15.5）。

    错误语义：404 问题不存在 / 409 已回答或任务已取消 / 410 已超时或服务重启失效 /
    400 选项非法或拒绝自由文本。前端据此禁用按钮并给出可读提示。
    """
    from clipwright.services import ask_user

    _enforce_task_owner(request, task_id)
    q = ask_user.get(payload.question_id)
    if q is not None and q.get("owner_type") == "task" and q.get("owner_id") != task_id:
        raise HTTPException(status_code=404,
                            detail=f"问题不属于该任务: {payload.question_id}")
    try:
        ans = ask_user.answer(payload.question_id, option_id=payload.option_id,
                             text=payload.text)
    except ask_user.AskUserError as exc:
        raise HTTPException(status_code=exc.http_status,
                            detail={"reason": exc.code, "message": exc.message,
                                    **exc.detail}) from exc
    task = _kernel().get(task_id)
    return {"task_id": task_id, "question_id": ans["question_id"],
            "status": "accepted",
            "task_status": getattr(task, "status", "") or "",
            "option_id": ans.get("option_id"), "source": ans.get("source")}


@router.get("/{task_id}/questions")
async def list_task_questions(task_id: str, request: Request,
                              status: str = Query(default="pending")) -> dict:
    """列出该任务的问题（默认只看待答）：刷新后恢复问题卡用（PRD §15.5）。"""
    from clipwright.services import ask_user

    _enforce_task_owner(request, task_id)
    if status == "pending":
        items = ask_user.pending("task", task_id)
    elif status == "all":
        items = ask_user.list_for_owner("task", task_id)
    else:
        items = [q for q in ask_user.list_for_owner("task", task_id)
                 if q.get("status") == status]
    return {"task_id": task_id, "status": status, "count": len(items),
            "items": items}


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


def _run_for_task(task_id: str, run_id: str) -> dict | None:
    """取 run，并强制校验它属于 URL 里的任务。

    任务 owner 校验只证明“你能看这个任务”，不能证明 run_id 属于它。
    详情/追问/重放/取消都必须共用这一处，否则 A 任务的 URL 就能操作 B 的子会话。
    """
    from clipwright.services import agent_run

    return (agent_run.get_run_for_task(task_id, run_id)
            or agent_run.inspect(run_id=run_id, task_id=task_id))


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
            # 真实阶段结果把报告路径写在顶层（AgentResult.report_path），
            # outputs 里通常没有这个键——旧实现让列表永远拿不到报告入口。
            "report_path": run.get("report_path", ""),
            "summary": str(run.get("summary") or "")[:400],
            "replayed": bool(run.get("replayed")),
            "live": bool(run.get("live")),
        })
    items.sort(key=lambda r: (r["stage"], r["attempt"], r["run_id"]))
    return {"task_id": task_id, "count": len(items), "items": items}


@router.get("/{task_id}/runs/{run_id}")
async def task_run_detail(task_id: str, run_id: str, request: Request) -> dict:
    """单个 run 的全量结果（含降级、工具轨迹、失败原因）。"""
    from clipwright.services import agent_run

    _enforce_task_owner(request, task_id)
    run = _run_for_task(task_id, run_id)
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
    run = _run_for_task(task_id, run_id)
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
    run = _run_for_task(task_id, run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"run 不存在: {run_id}")
    stage = str(run.get("stage") or "")
    if not stage:
        raise HTTPException(status_code=400, detail="该 run 没有阶段名，无法重放")
    task = _kernel().get(task_id)
    if task is None:
        raise HTTPException(status_code=409,
                            detail="任务不在运行态内存里（已结束或服务重启），无法重放该 run")
    if getattr(task, "terminal", False):
        # 终态之后重放会继续写 agent_run_*，破坏 task_end 的“事件已经收敛”语义，
        # 也会产生用户看不见的新成本；要重做请新建任务。
        raise HTTPException(status_code=409,
                            detail="任务已结束，不能再重放子 agent；如需重做请新建任务")
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
    # 任务发起时若带了项目，产物查询以该项目为范围（前端 artifact 列表用）
    project_id = ""
    try:
        from clipwright.services.agent_kernel import _TASKS  # noqa: PLC2701 — 只读快照

        task = _TASKS.get(task_id)
        if task is not None:
            project_id = str((task.request.extra or {}).get("proceed_project_id") or "")
    except Exception:  # noqa: BLE001 — 取不到就返回空，不影响旧字段
        project_id = ""
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
        "project_id": project_id,
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
