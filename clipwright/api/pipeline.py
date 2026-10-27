"""Pipeline API — 全流程执行、单 Agent 执行、实时追踪。"""

from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from clipwright.schema.pipeline import PipelineRequest, PipelineState
from clipwright.services.predictor import ScriptAnalyzer, MaterialAnalyzer
from clipwright.services.trace import get_events, get_all_events, create_trace, add_event
from clipwright.services.async_util import spawn_background
from clipwright.config import logger, settings

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])


# 后台运行的任务 + 结果缓存
_running_pipelines: dict[str, asyncio.Task] = {}
_pipeline_results: dict[str, dict] = {}
# 管线归属（jwt 模式）；"" = 无主（off/token 模式/遗留）
_pipeline_owners: dict[str, str] = {}
# P3-13: 幂等键统一到共享存储（与 /requirements/proceed 共用键空间）
from clipwright.services import idempotency as _idem
# pipeline_id → TaskQueue task_id（供取消/查询映射）
_pipeline_tasks: dict[str, str] = {}
# 用户主动取消的管线（用于区分用户取消与队列超时强杀——两者都会以
# CancelledError 形式进入 _run_background，但终态语义不同）
_user_cancel_requested: set[str] = set()


# ── 生产加固 1.2：运行态映射落 Mongo（重启可恢复），不可用时静默降级纯内存 ──

def _mongo_runtime_col():
    try:
        from clipwright.context import mongo
        if mongo.is_connected:
            return mongo.db["pipeline_runtime"]
    except Exception:
        pass
    return None


def _persist_pipeline_runtime(pipeline_id: str, **fields) -> None:
    """upsert 管线运行态元数据（owner/幂等键/队列映射），供进程重启后重建。"""
    col = _mongo_runtime_col()
    if col is None:
        return
    try:
        import time as _t
        payload = {k: v for k, v in fields.items() if v}
        payload["updated_at"] = _t.time()
        col.update_one({"pipeline_id": pipeline_id}, {"$set": payload}, upsert=True)
    except Exception as e:
        logger.warning("pipeline runtime 持久化失败: %s", e)


async def _load_result_from_mongo(pipeline_id: str) -> dict | None:
    """内存缺失时从 pipelines 集合重建结果字典（V2 全程落库的 state）。

    本函数在事件循环中被调用——Model 的 ``_io`` 帮手在运行中的 loop 里会
    返回未执行的协程（历史 bug：恢复路径永远失败于 'coroutine' object has
    no attribute items），故整个读取必须 offload 到线程。
    """
    import asyncio

    def _read() -> dict | None:
        from clipwright.models.pipeline_model import PipelineModel
        model = PipelineModel.find_by_id(pipeline_id)
        if model is None:
            return None
        doc = model.to_dict()
        if not isinstance(doc, dict) or not doc.get("status"):
            return None
        return {
            "pipeline_id": pipeline_id,
            "status": doc.get("status", "unknown"),
            "steps": doc.get("steps") or [],
            "request": doc.get("request") or {},
            "shared_data": doc.get("shared_data") or {},
            "error": doc.get("error") or "",
            "output_path": doc.get("output_path") or "",
            "recovered_from_mongo": True,
        }

    try:
        return await asyncio.to_thread(_read)
    except Exception as e:
        logger.warning("pipeline 结果 Mongo 回退失败: %s", e)
        return None


def recover_pipeline_runtime() -> dict:
    """启动恢复（main.py lifespan 调用）：

    1. pipelines 集合中遗留 status=running 的记录标记 interrupted（进程已死）；
    2. 从 pipeline_runtime 集合重建 owner/幂等映射（24h 内）。
    """
    stats = {"interrupted": 0, "owners": 0, "idem": 0}
    try:
        from clipwright.models.pipeline_model import PipelineModel
        # 修复：pending 也可能是被中断的运行（checkpoint 曾把 running 覆写为
        # pending），一并标记 interrupted
        stale = PipelineModel.find_many(
            {"status": {"$in": ["running", "pending"]}}, limit=500)
        for model in stale or []:
            try:
                model.status = "interrupted"
                model.error = (model.error or "") + " [进程重启中断]"
                model.update()
                stats["interrupted"] += 1
            except Exception:
                # 轮 81：留痕——此前 pass 会让重启后僵尸 running 状态永残留且无迹可查
                logger.exception("启动恢复: 标记中断管线失败 pipeline=%s", model.id)
    except Exception as e:
        logger.warning("启动恢复: 标记中断管线失败: %s", e)
    col = _mongo_runtime_col()
    if col is not None:
        try:
            import time as _t
            cutoff = _t.time() - 24 * 3600
            for doc in col.find({"updated_at": {"$gte": cutoff}}, limit=2000):
                pid = doc.get("pipeline_id")
                if not pid:
                    continue
                if doc.get("owner_id"):
                    _pipeline_owners.setdefault(pid, doc["owner_id"])
                    stats["owners"] += 1
                if doc.get("idem_key"):
                    _idem.bind_if_absent(doc["idem_key"], pid)
                    stats["idem"] += 1
                if doc.get("task_id"):
                    _pipeline_tasks.setdefault(pid, doc["task_id"])
        except Exception as e:
            logger.warning("启动恢复: 重建运行态映射失败: %s", e)
    if any(stats.values()):
        logger.info("管线运行态启动恢复: %s", stats)
    return stats


def _enforce_pipeline_owner(request: Request, pipeline_id: str) -> None:
    """jwt 模式下校验管线所有权（无主记录 403，安全优先）。"""
    from clipwright.authz import current_user_id, is_admin

    uid = current_user_id(request)
    if uid is None or is_admin(request):
        return
    owner = _pipeline_owners.get(pipeline_id)
    if owner is None:
        # 路线 C：任务 id 也会走这条校验（子代理树/执行日志用 task id 当 pipeline id），
        # 任务归属登记在 api/task.py 的 `_TASK_OWNERS` 里。
        try:
            from clipwright.api.task import _TASK_OWNERS

            owner = _TASK_OWNERS.get(pipeline_id)
        except Exception:  # noqa: BLE001 — 归属表不可用不影响老路径
            owner = None
    if owner is None:
        # 批A(D2)：内存归属 60s 清理后回退 Mongo（runtime 持久化含 owner_id）
        try:
            col = _mongo_runtime_col()
            if col is not None:
                doc = col.find_one({"pipeline_id": pipeline_id})
                if doc and doc.get("owner_id"):
                    _pipeline_owners[pipeline_id] = doc["owner_id"]
                    owner = doc["owner_id"]
        except Exception:
            pass
    if owner == uid:
        return
    raise HTTPException(status_code=403, detail=f"无权访问管线 {pipeline_id}")


def pipeline_timeout_sec(audio_dur: float, scene_count: int) -> int:
    """管线超时公式（前后端对齐，B7）。

    动画阶段逐片段 LLM MG 生成（每个 2-4+ 分钟）是主要耗时来源，另有 critique 修复重试；
    按音频时长 ×6 与场景数 ×360 叠加余量，最低 1800s。
    前端 ReviewPanel 与后端 proceed_to_pipeline 必须使用同一公式。
    """
    return int(max(1800, float(audio_dur or 0) * 6, int(scene_count or 0) * 360))


def _extract_quality_summary(result_dict: dict) -> dict | None:
    """C7: 从 steps 提取质检结果摘要——issues 此前只存在于 step.result 内部，
    前端（/result 与 SSE done 事件）完全看不到质检发现了什么。"""
    for s in reversed(result_dict.get("steps") or []):
        if isinstance(s, dict) and s.get("agent_name") == "quality":
            r = s.get("result") or {}
            issues = r.get("issues") or []
            if not isinstance(issues, list):
                return None
            items = []
            for i in issues:
                if isinstance(i, dict):
                    items.append({
                        "severity": i.get("severity", ""),
                        "category": i.get("category", ""),
                        "message": str(i.get("message", ""))[:200],
                        "location": str(i.get("location", "") or "")[:80],
                    })
            errors = sum(1 for x in items if x["severity"] == "error")
            warnings = sum(1 for x in items if x["severity"] == "warning")
            return {
                "passed": r.get("passed", True),
                "error_count": errors,
                "warning_count": warnings,
                "issues": items[:20],
                "fix_suggestions": [str(x)[:200] for x in (r.get("fix_suggestions") or [])][:10],
                "redo_agent": r.get("redo_agent", ""),
            }
    return None


def _aggregate_agent_notes(result_dict: dict) -> dict:
    """C9: 聚合各 Agent 的 notes/warnings 到 /result 顶层（只写字段一次性暴露）。"""
    notes: dict[str, list[str]] = {}
    for s in result_dict.get("steps") or []:
        if not isinstance(s, dict):
            continue
        name = s.get("agent_name") or ""
        r = s.get("result") or {}
        if not isinstance(r, dict):
            continue
        out: list[str] = []
        for key in ("edit_notes", "audio_notes", "material_notes"):
            v = r.get(key)
            if isinstance(v, list):
                out.extend(str(x) for x in v)
        sk = r.get("script_skeleton")
        if isinstance(sk, dict) and isinstance(sk.get("_warnings"), list):
            out.extend(str(x) for x in sk["_warnings"])
        if out:
            notes[name] = out[:20]
    return notes


@router.get("/runs")
async def list_pipeline_runs(request: Request, limit: int = 50) -> list[dict]:
    """获取管线运行记录（真实执行历史，供 PipelineAdminPage 展示）。

    返回形状: {id, topic, status, duration_ms, started_at,
               agents: [{agent, start, dur, status}],
               usage: {input_tokens, output_tokens, total_tokens, calls,
                       by_agent: {agent: {input_tokens, output_tokens}}, render_sec}}
    ``usage`` 是 V38 的用量总账：token 按 Agent 汇总，渲染耗时取 render 步骤。
    数据源：内存注册表（当前进程真实运行）优先，Mongo PipelineModel 持久化历史兜底。
    P3-3B: jwt 模式下按 owner 过滤（无主记录隐藏）。
    """
    from clipwright.authz import current_user_id, is_admin

    from clipwright.services.pipeline_history import get_run_records
    # find_many 走 _io() → 线程内执行，避免事件循环中协程误用导致 Mongo 历史丢失
    records = await asyncio.to_thread(get_run_records, max(1, min(limit, 500)))
    uid = current_user_id(request)
    if uid is None or is_admin(request):
        return records
    return [r for r in records if _pipeline_owners.get(r.get("id")) == uid]


@router.get("/tasks")
async def list_pipeline_tasks(request: Request, limit: int = 50) -> dict:
    """A10: 队列任务列表（含重启恢复项 recovered/interrupted）。

    返回: {tasks: [...], recovered: [...], running: n, pending: n}
    """
    from clipwright.services.task_queue import get_task_queue

    tq = get_task_queue()
    tasks = tq.list_tasks(task_type="pipeline", limit=max(1, min(limit, 200)))
    recovered = tq.recover_stale()
    return {
        "tasks": tasks,
        "recovered": recovered,
        "running": tq.running_count,
        "pending": tq.pending_count,
    }


# 批A(R6)：废弃同步端点 /run 与 /run-v2 已移除——无 owner/预算/队列治理，
# 且 /result 的 owner 校验使 jwt 模式下调用者根本读不到结果。
# 统一使用 /run-async + SSE。


async def _start_as_task(request: PipelineRequest, req: Request, uid: str = "") -> dict:
    """把老 `run-async` 请求转成一条 Task（双轨兼容层）。

    兼容承诺：返回体仍带 `pipeline_id`（=task id），老客户端拿它去轮询
    `/status`、`/result`、`/cancel` 时能正常工作（那几个端点已加 Task 回退）。
    """
    from clipwright.schema.task import TaskRequest
    from clipwright.services.agent_kernel import AgentKernel
    from clipwright.services.task_events import emit

    # 插件 hook —— 管线前置（M3 后 Task 是唯一创作入口，所以 hook 挂在这里：
    # 插件契约不变，只是宿主从后台编排循环换成了 Task 创建）。
    try:
        from clipwright.plugins.hooks import HookPoint
        from clipwright.plugins.hooks_async import execute_async

        await execute_async(HookPoint.PRE_PIPELINE, {
            "pipeline_id": pipeline_id, "topic": request.topic,
            "persona_id": request.persona_id,
        })
    except Exception as e:  # noqa: BLE001 — hook 失败不能拦住开工
        logger.warning("pre_pipeline hook 执行失败: %s", e)

    task_request = TaskRequest(
        topic=request.topic,
        persona_id=request.persona_id,
        category_plugin_id=request.category_plugin_id,
        duration_sec=float((request.extra_params or {}).get("duration_sec") or 0),
        plan_only=False,          # 老入口的语义就是"直接开跑"
        extra={"pipeline_request": request.model_dump(mode="json")},
    )
    kernel = AgentKernel()
    task = kernel.create(task_request, mode="on")
    # 归属登记：老端点的 owner 校验靠 `_pipeline_owners`，task 也得进这张表
    if uid:
        _pipeline_owners[task.id] = uid
    emit(task.id, "task_start", "由 /run-async 转入 agent 任务",
         {"compat": True, "legacy_topic": request.topic[:80]})
    from clipwright.api.task import _schedule

    _schedule(task.id)
    return {
        "pipeline_id": task.id,
        "task_id": task.id,
        "status": "started",
        "mode": "agent",
    }


def _task_of(pipeline_id: str):
    """若该 id 是 agent 任务则返回 Task，否则 None（兼容层用）。"""
    from clipwright.services.agent_kernel import AgentKernel

    if not pipeline_id.startswith("task_"):
        return None
    return AgentKernel().get(pipeline_id)


_STATUS_MAP = {
    "pending": "running", "planning": "running", "running": "running",
    "paused": "paused", "done": "completed", "failed": "failed",
    "cancelled": "cancelled",
}


@router.post("/run-async")
async def run_pipeline_async(request: PipelineRequest, req: Request) -> dict:
    """老管线入口：只剩"转 Task"与 410 两条路。

    `agent_mode=on` → 转成主 Agent 任务（返回体仍带 `pipeline_id`，老客户端不受影响）；
    否则 410。**没有第三条路**——执行引擎（`PipelineOrchestratorV2`）已在 M3 物理删除，
    所以这里不再有后台编排代码可留。
    """
    from clipwright.authz import current_user_id

    if str(getattr(settings, "agent_mode", "off")).lower() == "on":
        return await _start_as_task(request, req, uid=current_user_id(req))

    raise HTTPException(
        status_code=410,
        detail=(
            "老 6 阶段管线已下线：创作请走主 Agent —— "
            "POST /api/requirements/proceed（agent_mode=on 时转 Task）或 POST /api/task"
        ),
    )


class BatchPipelineRequest(BaseModel):
    """批量管线启动（轮 86 出片闭环）。requests 每项即单条 PipelineRequest。"""
    requests: list[PipelineRequest] = Field(default_factory=list)


@router.post("/run-batch")
async def run_pipeline_batch(batch: BatchPipelineRequest, req: Request) -> dict:
    """批量启动管线（单批上限 10 条），逐条返回 pipeline_id。

    每条独立入队、独立计费/审计；单条失败不影响其余条目。
    """
    if not batch.requests:
        raise HTTPException(status_code=400, detail="requests 不能为空")
    if len(batch.requests) > 10:
        raise HTTPException(status_code=400, detail="单批最多 10 条管线")
    results: list[dict] = []
    for r in batch.requests:
        try:
            info = await run_pipeline_async(r, req)
            results.append({"ok": True, "topic": r.topic, **info})
        except HTTPException as e:
            results.append({"ok": False, "topic": r.topic,
                            "error": e.detail if isinstance(e.detail, str) else str(e.detail)})
        except Exception as e:
            logger.exception("批量管线单条启动失败: %s", r.topic)
            results.append({"ok": False, "topic": r.topic, "error": str(e)})
    return {
        "submitted": sum(1 for r in results if r["ok"]),
        "total": len(results),
        "results": results,
    }


@router.get("/trace/{pipeline_id}")
async def get_pipeline_trace(pipeline_id: str, request: Request):
    """获取追踪事件（JSON）。

    兼容层（P3）：`pipeline_id` 也可能是 agent 任务 id（`task_*`）——主 Agent 化之后
    前端仍用同一个订阅面板，这里把 task 事件映射成 trace 形状，避免前端为"两条链路"
    写两套订阅。批4：补 owner 校验——trace 含提示词/脚本/时间线快照。
    """
    # 先做归属校验（`_enforce_pipeline_owner` 已支持 task id：查 api/task 的归属表），
    # 再决定从 task_events 还是管线 trace 取数——task 分支不能绕过鉴权。
    _enforce_pipeline_owner(request, pipeline_id)
    if _task_of(pipeline_id) is not None:
        from clipwright.services.task_events import events as task_events

        return [
            {"seq": e.seq, "type": e.type, "message": e.message,
             "detail": e.detail, "ts": e.ts.isoformat(), "source": "task"}
            for e in task_events(pipeline_id)
        ]
    # 直接返回 JSON
    return get_all_events(pipeline_id)


@router.get("/trace/stream/{pipeline_id}")
async def stream_pipeline_trace(pipeline_id: str, request: Request):
    """SSE 流：实时推送追踪事件（LLM、Tool、Skill、Plugin 调用）。

    兼容层（P3）：task id 走 `task_events` 的增量游标，形状与管线 trace 对齐。
    """
    # jwt 模式校验管线所有权（trace 含提示词/脚本/时间线快照，禁止跨用户订阅）
    _enforce_pipeline_owner(request, pipeline_id)

    if _task_of(pipeline_id) is not None:
        async def task_stream():
            from clipwright.services.task_events import events_since

            cursor = 0
            idle = 0
            while True:
                batch = events_since(pipeline_id, cursor)
                for e in batch:
                    cursor = max(cursor, int(e.seq))
                    payload = {"seq": e.seq, "type": e.type, "message": e.message,
                               "detail": e.detail, "ts": e.ts.isoformat(), "source": "task"}
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    idle = 0
                if not batch:
                    idle += 1
                    if idle % 10 == 0:
                        yield ": keep-alive\n\n"
                task_obj = _task_of(pipeline_id)
                if task_obj is not None and task_obj.terminal and not batch:
                    return
                await asyncio.sleep(1)

        return StreamingResponse(task_stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    async def event_stream():
        # 生产加固 1.8: 单调 seq 游标（替代浮点时间戳，时钟回拨不丢事件）
        from clipwright.services.trace import get_events_since_seq, wait_new_event
        # 终态集合含 cancelled/timeout——取消/超时后流必须关闭，前端才能停止订阅
        terminal_types = ("done", "error", "cancelled", "timeout")
        last_seq = 0
        # 先发送已存在的事件
        existing = get_events(pipeline_id)
        for event in existing:
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            last_seq = max(last_seq, int(event.get("seq", 0) or 0))
        # 重连时若历史事件已含终态（如完成后 60s 内重连），回放后直接关闭，
        # 不再空转到 max_wall
        if any(e.get("type") in terminal_types for e in existing):
            await asyncio.sleep(2)
            return
        if not existing:
            # trace 已清理（完成 60s 后重连）→ 从 Mongo 检查点回放终态，避免静默空转
            recovered = await _load_result_from_mongo(pipeline_id)
            _status = (recovered or {}).get("status", "")
            if _status and _status != "running":
                _type = ("done" if _status in ("completed", "pass")
                         else "cancelled" if _status in ("cancelled", "interrupted")
                         else "error")
                yield f"data: {json.dumps({'type': _type, 'agent': 'system', 'level': 'info', 'message': f'管线终态回放: {_status}', 'seq': 0}, ensure_ascii=False)}\n\n"
                return

        # 持续轮询新事件直到管线终态（done/error）或达到长上限。
        # 管线动画阶段可能长达 40-60 分钟，固定 600s 会让前端 SSE 中途断流而收不到终态。
        # max_wall 动态化——从结果缓存中取该管线的 pipeline_timeout_sec（proceed 时写入
        # request.extra_params），加 600s 余量；取不到时回退默认 7200+600。
        _max_wall = 7200.0 + 600.0
        _res = _pipeline_results.get(pipeline_id)
        try:
            if isinstance(_res, dict):
                _ep = (_res.get("request") or {}).get("extra_params") or {}
                _to = _ep.get("pipeline_timeout_sec")
                if isinstance(_to, (int, float)) and _to > 0:
                    _max_wall = float(_to) + 600.0
        except Exception:
            pass
        max_wall = _max_wall
        start_wall = time.monotonic()
        while time.monotonic() - start_wall < max_wall:
            if await request.is_disconnected():
                return
            # 生产加固 1.5: 事件驱动等待（替代 0.5s 盲轮询），1s 超时兼顾断连检查
            from clipwright.services.trace import wait_new_event
            await wait_new_event(pipeline_id, timeout=1.0)
            events = get_events_since_seq(pipeline_id, last_seq)
            if events:
                for event in events:
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    last_seq = max(last_seq, int(event.get("seq", 0) or 0))
                    # done/error/cancelled/timeout 均为终态，延 2 秒关闭
                    if event["type"] in terminal_types:
                        await asyncio.sleep(2)
                        return

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/result/{pipeline_id}")
async def get_pipeline_result(pipeline_id: str, request: Request) -> dict:
    """获取异步管线执行结果。如果管线仍在运行，最多轮询 5 分钟等待。

    批4：补 owner 校验——result 含完整时间线/脚本/质检，兄弟端点均已校验。
    """
    _enforce_pipeline_owner(request, pipeline_id)
    import asyncio

    # 双轨兼容：agent 任务的结果（有管线产物就返回管线结果，没有就返回任务快照）
    agent_task = _task_of(pipeline_id)
    if agent_task is not None:
        from clipwright.services.task_results import last_result

        pipeline_out = last_result(pipeline_id)
        if pipeline_out is not None:
            pipeline_out = dict(pipeline_out)
            pipeline_out.setdefault("task_id", pipeline_id)
            return pipeline_out
        return {
            "pipeline_id": pipeline_id,
            "task_id": pipeline_id,
            "status": _STATUS_MAP.get(agent_task.status, agent_task.status),
            "mode": "agent",
            "task": agent_task.to_public(),
        }

    # 先返回已有结果
    result = _pipeline_results.get(pipeline_id)
    if result is not None:
        return result
    # 没有结果 → 检查是否还在运行
    task = _running_pipelines.get(pipeline_id)
    if task is None:
        # 生产加固 1.2: 内存缺失（如进程重启）→ Mongo 回退
        recovered = await _load_result_from_mongo(pipeline_id)
        if recovered is not None:
            _pipeline_results[pipeline_id] = recovered
            return recovered
        # 既无结果也无运行中的任务
        from clipwright.services.trace import get_all_events as ga
        events = ga(pipeline_id)
        if events:
            return {"status": "running", "pipeline_id": pipeline_id,
                    "events": events[-5:], "note": "管线仍在运行"}
        raise HTTPException(status_code=404, detail=f"Pipeline {pipeline_id} not found")

    # 轮询等待（5s × 60 次 = 5 分钟）
    for _ in range(60):
        await asyncio.sleep(5)
        result = _pipeline_results.get(pipeline_id)
        if result is not None:
            return result
        # 任务已完成但没结果 → 异常
        if task.done():
            raise HTTPException(status_code=500,
                                detail=f"Pipeline {pipeline_id} 已完成但无结果数据")
        # 还在跑就继续等

    raise HTTPException(status_code=504,
                        detail=f"Pipeline {pipeline_id} 执行超时（>5分钟）")


@router.get("/status/{pipeline_id}")
async def get_pipeline_status(pipeline_id: str, request: Request) -> dict:
    """查询管线执行状态（P3-3B: 校验所有权）。"""
    _enforce_pipeline_owner(request, pipeline_id)

    # 双轨兼容：agent 模式下 id 是 task id（必须在 Mongo 回退之前判断）
    agent_task = _task_of(pipeline_id)
    if agent_task is not None:
        from clipwright.services.task_results import last_result

        return {
            "pipeline_id": pipeline_id,
            "status": _STATUS_MAP.get(agent_task.status, agent_task.status),
            "has_result": last_result(pipeline_id) is not None
            or agent_task.status == "done",
            "mode": "agent",
        }

    result = _pipeline_results.get(pipeline_id)
    if result is None:
        # 生产加固 1.2: Mongo 回退（async，旧实现漏了 await → 拿到 coroutine 就 .get 了）
        result = await _load_result_from_mongo(pipeline_id)
        if result is not None:
            _pipeline_results[pipeline_id] = result
    if result is not None:
        return {"pipeline_id": pipeline_id, "status": result.get("status", "completed"),
                "has_result": True}
    task = _running_pipelines.get(pipeline_id)
    if task is not None:
        return {"pipeline_id": pipeline_id,
                "status": "running" if not task.done() else "finished",
                "has_result": False}
    if get_all_events(pipeline_id):
        return {"pipeline_id": pipeline_id, "status": "unknown", "has_result": False}
    raise HTTPException(status_code=404, detail=f"Pipeline {pipeline_id} not found")


@router.get("/diagnostics/{pipeline_id}")
async def pipeline_diagnostics(pipeline_id: str, request: Request) -> dict:
    """P8: 失败诊断报告 — 失败 run 生成结构化诊断（阶段/原因/建议）。"""
    from clipwright.services.pipeline_history import categorize_error

    _enforce_pipeline_owner(request, pipeline_id)
    result = _pipeline_results.get(pipeline_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"Pipeline {pipeline_id} not found or still running")

    status = result.get("status", "")
    error = result.get("error") or ""
    steps = result.get("steps") or []

    # 定位失败步骤
    failed_step: dict | None = None
    for s in steps:
        if isinstance(s, dict) and s.get("status") == "failed":
            failed_step = s
            break

    category = categorize_error(error or (failed_step or {}).get("error") or "")
    suggestions: list[str] = []
    if category == "transient":
        suggestions.append("可重试：等待网络恢复后重跑，或对失败 Agent 单独重试（POST /retry/{id}/{agent}）")
    elif category == "fatal":
        suggestions.append("系统级故障：检查 MongoDB/磁盘/内存，修复后重跑")
    else:
        suggestions.append("参数/数据问题：检查 Persona、选题与素材路径配置")
    if failed_step:
        suggestions.append(f"失败阶段：{failed_step.get('agent_name', '?')}")

    return {
        "pipeline_id": pipeline_id,
        "status": status,
        "error": error[:500],
        "error_category": category,
        "failed_step": failed_step,
        "steps_summary": [
            {"agent_name": s.get("agent_name"), "status": s.get("status"),
             "error": (s.get("error") or "")[:200]}
            for s in steps if isinstance(s, dict)
        ],
        "suggestions": suggestions,
    }


@router.post("/retry/{pipeline_id}/{agent_name}")
async def retry_agent(pipeline_id: str, agent_name: str, request: Request) -> dict:
    """重试指定 Agent —— **已下线**（老管线执行引擎在 M3 删除）。

    主 Agent 侧的正确做法：`delegate_agent(stage=…)` 重派该阶段，
    连带下游由 `replan_closure` 计算。
    """
    return await start_agent_retry(pipeline_id, agent_name, request)


async def start_agent_retry(pipeline_id: str, agent_name: str, request: Request) -> dict:
    """「从某个 Agent 起重跑」——**已下线**。

    M3 之后没有"重跑某个 agent"这回事：主 Agent 用 `delegate_agent` 重新派该阶段，
    依赖闭包由 `replan_closure` 算。这里保留 410 与可执行指引，不做静默降级。
    """
    _enforce_pipeline_owner(request, pipeline_id)
    raise HTTPException(
        status_code=410,
        detail=(
            "老 6 阶段管线已下线，retry 不再可用：请用主 Agent 的 delegate_agent 重派该阶段"
            "（replan_closure 会算出需要连带重做的下游）"
        ),
    )


#: 模板名的合法字符（字母数字下划线/中文/连字符，1–64）
_PIPE_TEMPLATE_ID_RE = __import__("re").compile(r"^[\w\u4e00-\u9fff-]{1,64}$")
#: 管线模板登记表（name → {request, owner_id, updated_at}）。
#: 与执行引擎无关（模板只是"把一组请求参数存起来"），所以它留在这里。
_pipe_templates: dict[str, dict] = {}


class PipelineTemplateSaveRequest(BaseModel):
    name: str = Field(description="模板名（字母数字下划线连字符）")
    request: dict = Field(description="PipelineRequest 字段（persona_id/category_plugin_id/topic/extra_params/dry_run/use_v2）")


@router.get("/templates")
async def list_pipeline_templates(request: Request) -> list[dict]:
    """列出当前用户的管线配置模板（jwt 模式按 owner 过滤）。"""
    from clipwright.authz import current_user_id
    uid = current_user_id(request)
    items = []
    for name, t in _pipe_templates.items():
        if uid and t.get("owner_id") and t["owner_id"] != uid:
            continue
        items.append({"name": name, "updated_at": t.get("updated_at", "")})
    return sorted(items, key=lambda x: x["name"])


@router.post("/templates")
async def save_pipeline_template(req: PipelineTemplateSaveRequest, request: Request) -> dict:
    """保存（或覆盖）一个命名管线配置模板。"""
    from clipwright.authz import current_user_id
    if not _PIPE_TEMPLATE_ID_RE.match(req.name):
        raise HTTPException(status_code=400, detail="模板名限字母数字下划线连字符（1-64）")
    from datetime import datetime, timezone
    _pipe_templates[req.name] = {
        "request": req.request,
        "owner_id": current_user_id(request) or "",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    return {"name": req.name, "status": "saved"}


@router.get("/templates/{name}")
async def get_pipeline_template(name: str, request: Request) -> dict:
    """读取模板（返回 PipelineRequest 字段，供直接启动）。"""
    from clipwright.authz import current_user_id
    t = _pipe_templates.get(name)
    if t is None:
        raise HTTPException(status_code=404, detail=f"模板 {name} 不存在")
    uid = current_user_id(request)
    if uid and t.get("owner_id") and t["owner_id"] != uid:
        raise HTTPException(status_code=403, detail="无权访问该模板")
    return {"name": name, "request": t["request"], "updated_at": t.get("updated_at", "")}


@router.delete("/templates/{name}")
async def delete_pipeline_template(name: str, request: Request) -> dict:
    """删除模板。"""
    from clipwright.authz import current_user_id
    t = _pipe_templates.get(name)
    if t is None:
        raise HTTPException(status_code=404, detail=f"模板 {name} 不存在")
    uid = current_user_id(request)
    if uid and t.get("owner_id") and t["owner_id"] != uid:
        raise HTTPException(status_code=403, detail="无权删除该模板")
    del _pipe_templates[name]
    return {"name": name, "status": "deleted"}


@router.post("/cancel/{pipeline_id}")
async def cancel_pipeline(pipeline_id: str, request: Request) -> dict:
    """取消管线（C9：即时取消）。

    1. 先取消后台 asyncio 任务（中断 in-flight agent/LLM 调用）；
    2. 同时标记协作式取消标志（任务已结束/不存在时仍能阻止后续 agent）；
    3. 终态写入 CANCELLED / cancelled 并写 trace 事件。
    若管线已结束/不存在，返回 404。
    """
    from clipwright.services.pipeline_history import mark_cancelled
    from clipwright.services.trace import get_all_events as _ga

    _enforce_pipeline_owner(request, pipeline_id)

    # 双轨兼容：agent 任务的取消
    agent_task = _task_of(pipeline_id)
    if agent_task is not None:
        from clipwright.services.agent_kernel import AgentKernel, KernelError

        try:
            cancelled = AgentKernel().cancel(pipeline_id)
        except KernelError as e:
            raise HTTPException(status_code=404, detail=str(e))
        return {"pipeline_id": pipeline_id, "status": cancelled.status, "mode": "agent"}

    if not _ga(pipeline_id):
        raise HTTPException(status_code=404, detail=f"Pipeline {pipeline_id} not found")

    # 即时取消 — 中断运行中的后台任务（CancelledError 由 _run_background 捕获写终态）
    # 先登记用户取消意图——队列超时与用户取消都会以 CancelledError 进入
    # _run_background，靠该集合区分 cancelled / timeout 终态
    _user_cancel_requested.add(pipeline_id)
    task = _running_pipelines.get(pipeline_id)
    if task and not task.done():
        task.cancel()
        logger.info("取消请求: %s (即时取消后台任务)", pipeline_id)
    else:
        logger.info("取消请求: %s (任务已不在运行，仅标记协作式取消)", pipeline_id)

    # 队列 pending 任务直接从队列移除
    try:
        from clipwright.services.task_queue import get_task_queue
        tq_id = _pipeline_tasks.get(pipeline_id, "")
        if tq_id:
            get_task_queue().cancel(tq_id)
    except Exception:
        # 轮 81：留痕——取消失败会让任务继续跑完，用户以为已取消
        logger.exception("取消队列任务失败 pipeline=%s task=%s", pipeline_id, _pipeline_tasks.get(pipeline_id, ""))

    mark_cancelled(pipeline_id)
    add_event(pipeline_id, "system", "cancelled", "已请求取消管线")
    return {"pipeline_id": pipeline_id, "status": "cancelling"}


@router.get("/breaker-status")
async def pipeline_breaker_status(request: Request) -> dict:
    """C8: 熔断健康探测 — 返回各 Agent 熔断计数与状态（运维/前端监控用）。"""
    from clipwright.services import circuit_breaker

    return circuit_breaker.snapshot()


class PredictScriptRequest(BaseModel):
    script_text: str = Field(..., max_length=50000, description="文稿内容")

class PredictMaterialRequest(BaseModel):
    file_path: str = Field(..., description="素材文件路径")
    file_size: int = Field(default=0, ge=0, description="文件大小(bytes)")


class ScriptToolRequest(BaseModel):
    """P8: 脚本创作工具（改写/扩写/缩写）。"""
    mode: str = Field(..., description="rewrite | expand | summarize")
    script_text: str = Field(..., max_length=50000, description="原文稿")
    style: str = Field(default="", description="风格/口吻要求（可选）")
    max_length: int = Field(default=0, ge=0, le=20000, description="目标字数（可选，0=不限）")


class TopicSuggestRequest(BaseModel):
    """P8: 热点/选题发现。"""
    category: str = Field(default="", description="内容类别（可选）")
    count: int = Field(default=5, ge=1, le=10)
    use_web: bool = Field(default=False, description="是否尝试外部 trending 检索")


@router.post("/script-tools")
async def script_tools(body: ScriptToolRequest) -> dict:
    """P8: 脚本续写/改写/扩写 — LLM 工具化三种模式。"""
    from clipwright.services.llm import LLMService

    mode_map = {
        "rewrite": "改写",
        "expand": "扩写",
        "summarize": "缩写",
    }
    mode = mode_map.get(body.mode)
    if not mode:
        raise HTTPException(status_code=400, detail="mode 必须为 rewrite/expand/summarize")

    style_hint = f"风格要求：{body.style}。" if body.style else ""
    length_hint = f"目标约 {body.max_length} 字。" if body.max_length > 0 else ""
    prompt = (
        f"你是中文视频文案编辑。请对下面的文稿进行「{mode}」处理。\n"
        f"{style_hint}{length_hint}\n"
        f"只输出处理后的文稿正文，不要解释、不要 Markdown 标题。\n\n"
        f"原文稿：\n{body.script_text[:40000]}"
    )
    try:
        resp = await LLMService().generate(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=6000,
            timeout=180,
        )
        text = getattr(resp, "content", None) or ""
        out = str(text).strip()
        if not out:
            raise ValueError("空输出")
        return {"mode": body.mode, "result": out[:50000]}
    except Exception as e:
        # LLM 失败 → 启发式回退（扩写=原文+说明；缩写=截断；改写=原文）
        logger.warning("script-tools LLM 失败，回退启发式: %s", e)
        if body.mode == "summarize":
            target = body.max_length or min(500, len(body.script_text))
            return {"mode": body.mode, "result": body.script_text[:target], "fallback": True}
        if body.mode == "expand":
            return {"mode": body.mode, "result": body.script_text, "fallback": True,
                    "note": "LLM 不可用，未扩写"}
        return {"mode": body.mode, "result": body.script_text, "fallback": True}


@router.post("/predict-script")
async def predict_script(body: PredictScriptRequest) -> dict:
    """智能预判：分析文稿并推荐 Persona/类型/时长。"""
    result = await ScriptAnalyzer.analyze(body.script_text)
    return result


@router.post("/predict-material")
async def predict_material(body: PredictMaterialRequest) -> dict:
    """智能预判：分析素材并推荐使用方式。"""
    from clipwright.security import assert_allowed_path
    from pathlib import Path
    assert_allowed_path(Path(body.file_path))
    result = await MaterialAnalyzer.analyze(body.file_path, body.file_size)
    return result


@router.post("/topic-suggest")
async def suggest_topics(body: TopicSuggestRequest) -> dict:
    """P8: 热点/选题发现 — 返回选题建议列表（LLM + 可选 trending，启发式回退）。"""
    from clipwright.services.topic_discovery import suggest_topics as _suggest
    topics = await _suggest(category=body.category, count=body.count, use_web=body.use_web)
    return {"topics": topics}
