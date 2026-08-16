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
from clipwright.services.pipeline import PipelineOrchestrator
from clipwright.services.pipeline_v2 import PipelineOrchestratorV2
from clipwright.services.trace import get_events, get_all_events, create_trace, add_event
from clipwright.services.async_util import spawn_background
from clipwright.config import logger

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])
_orchestrator = PipelineOrchestrator()


# 后台运行的任务 + 结果缓存
_running_pipelines: dict[str, asyncio.Task] = {}
_pipeline_results: dict[str, dict] = {}
# P3-3B: 管线归属（jwt 模式）；"" = 无主（off/token 模式/遗留）
_pipeline_owners: dict[str, str] = {}
# P5-B8: 幂等键（Idempotency-Key → pipeline_id；随结果缓存一并清理）
_idempotency_pipeline: dict[str, str] = {}


def _enforce_pipeline_owner(request: Request, pipeline_id: str) -> None:
    """jwt 模式下校验管线所有权（无主记录 403，安全优先）。"""
    from clipwright.authz import current_user_id, is_admin

    uid = current_user_id(request)
    if uid is None or is_admin(request):
        return
    owner = _pipeline_owners.get(pipeline_id)
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


@router.get("/runs")
async def list_pipeline_runs(request: Request, limit: int = 50) -> list[dict]:
    """获取管线运行记录（真实执行历史，供 PipelineAdminPage 展示）。

    返回形状: {id, topic, status, duration_ms, started_at,
               agents: [{agent, start, dur, status}]}
    数据源：内存注册表（当前进程真实运行）优先，Mongo PipelineModel 持久化历史兜底。
    P3-3B: jwt 模式下按 owner 过滤（无主记录隐藏）。
    """
    from clipwright.authz import current_user_id, is_admin

    from clipwright.services.pipeline_v2 import get_run_records
    # P0-6: find_many 走 _io() → 线程内执行，避免事件循环中协程误用导致 Mongo 历史丢失
    records = await asyncio.to_thread(get_run_records, max(1, min(limit, 500)))
    uid = current_user_id(request)
    if uid is None or is_admin(request):
        return records
    return [r for r in records if _pipeline_owners.get(r.get("id")) == uid]


@router.post("/run-v2")
async def run_pipeline_v2(request: PipelineRequest) -> dict:
    """运行 PipelineV2（动态路由 + 自愈循环）。

    Deprecated (B13): V1 同步端点，前端已改用 /run-async + SSE 追踪；保留兼容不删除。
    """
    import uuid
    logger.warning("DEPRECATED: POST /api/pipeline/run-v2 被调用 (persona=%s)", request.persona_id)
    pipeline_id = f"pl_v2_{uuid.uuid4().hex[:12]}"
    create_trace(pipeline_id)
    add_event(pipeline_id, "system", "info", f"PipelineV2 启动: {request.persona_id} / {request.category_plugin_id}")

    orch_v2 = PipelineOrchestratorV2()
    try:
        state = await orch_v2.run(request, pipeline_id=pipeline_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Pipeline 执行失败: {e}")
    return {
        "pipeline_id": pipeline_id,
        "status": state.status.value,
        "steps": [{"agent": s.agent_name, "status": s.status.value} for s in state.steps],
        "error": state.error,
        "deprecated": True,
    }


@router.post("/run", response_model=PipelineState)
async def run_pipeline(request: PipelineRequest) -> PipelineState:
    """全流程执行，返回完整时间线。

    Deprecated (B13): V1 同步端点，前端零调用；保留兼容不删除。
    """
    logger.warning("DEPRECATED: POST /api/pipeline/run 被调用 (persona=%s)", request.persona_id)
    state = await _orchestrator.run(request)
    state.shared_data["execution_trace"] = get_all_events(state.pipeline_id)
    if state.status == "failed":
        raise HTTPException(status_code=400, detail=state.error)
    # 附加 deprecated 标记（响应模型为 PipelineState，注入 shared_data 供调用方探测）
    state.shared_data["deprecated"] = True
    return state


@router.post("/run-async")
async def run_pipeline_async(request: PipelineRequest, req: Request) -> dict:
    """异步启动管线，立即返回 pipeline_id，进度通过 SSE trace 推送。"""
    import uuid

    from clipwright import audit
    from clipwright.authz import current_user_id

    uid = current_user_id(req)
    # P5-B8: 幂等键去重（双击/网络重试不重复扣费）
    idem_key = req.headers.get("Idempotency-Key", "")
    if idem_key:
        existing = _idempotency_pipeline.get(idem_key)
        if existing and (existing in _pipeline_results or existing in _running_pipelines):
            return {"pipeline_id": existing, "status": "deduplicated"}

    pipeline_id = f"pl_{uuid.uuid4().hex[:12]}"
    # P5-B3: 成本预算熔断（超月预算拒绝新管线）
    from clipwright.services.budget import check_budget
    allowed, used = await check_budget()
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"本月 LLM token 预算已耗尽（已用 {used} / {settings.llm_monthly_token_budget}）",
        )
    # P3-3B: 记录归属
    _pipeline_owners[pipeline_id] = uid or ""
    if idem_key:
        _idempotency_pipeline[idem_key] = pipeline_id
    audit.record("pipeline_run", uid, {"pipeline_id": pipeline_id,
                                       "persona_id": request.persona_id,
                                       "category_plugin_id": request.category_plugin_id})
    create_trace(pipeline_id)
    add_event(pipeline_id, "system", "info",
              f"管线异步启动: {request.persona_id} / {request.category_plugin_id} (v2={'是' if request.use_v2 else '否'})")

    async def _run_background():
        try:
            orch = PipelineOrchestratorV2() if request.use_v2 else _orchestrator
            state = await orch.run(request, pipeline_id=pipeline_id)
            state.shared_data["execution_trace"] = get_all_events(pipeline_id)
            result_dict = state.model_dump(mode="json")
            _pipeline_results[pipeline_id] = result_dict
            add_event(pipeline_id, "system", "done", f"管线完成: {state.status}")
            # P8: webhook 事件接线 — 管线完成/失败通知
            ok = getattr(state, "status", None) is not None and str(getattr(state, "status", "")).lower() in ("completed", "pass")
            try:
                from clipwright.api.webhook import dispatch_event
                await dispatch_event(
                    "pipeline.completed" if ok else "pipeline.failed",
                    {
                        "pipeline_id": pipeline_id,
                        "topic": request.topic,
                        "persona_id": request.persona_id,
                        "status": str(getattr(state, "status", "")),
                    },
                )
            except Exception as we:
                logger.warning("pipeline webhook dispatch failed: %s", we)
        except asyncio.CancelledError:
            # C9: 取消即时性 — 任务被外部 cancel，写入终态并标记已取消（不重新抛出）
            logger.info("管线任务已取消: %s", pipeline_id)
            _pipeline_results[pipeline_id] = {
                "status": "cancelled", "pipeline_id": pipeline_id,
                "error": "管线已取消（任务中断）",
            }
            add_event(pipeline_id, "system", "cancelled", "管线已取消（任务中断）")
        except Exception as e:
            logger.exception("pipeline._run_background failed: %s", e)
            add_event(pipeline_id, "system", "error", f"管线失败: {e}")
            # 即使异常也写一个结果，让前端能查到错误
            _pipeline_results[pipeline_id] = {"status": "failed", "error": str(e), "pipeline_id": pipeline_id}
            try:
                from clipwright.api.webhook import dispatch_event
                await dispatch_event("pipeline.failed", {
                    "pipeline_id": pipeline_id,
                    "topic": request.topic,
                    "error": str(e)[:300],
                })
            except Exception as we:
                logger.warning("pipeline webhook dispatch failed: %s", we)
        finally:
            # 60秒后清理内存，给前端足够时间轮询
            async def _cleanup():
                await asyncio.sleep(60)
                _pipeline_results.pop(pipeline_id, None)
                _running_pipelines.pop(pipeline_id, None)
                _pipeline_owners.pop(pipeline_id, None)
                for k, v in list(_idempotency_pipeline.items()):
                    if v == pipeline_id:
                        _idempotency_pipeline.pop(k, None)
                from clipwright.services.trace import clear
                clear(pipeline_id)
            spawn_background(_cleanup(), name=f"pipeline-cleanup-{pipeline_id}")

    task = asyncio.create_task(_run_background())
    _running_pipelines[pipeline_id] = task
    return {"pipeline_id": pipeline_id, "status": "started"}


@router.get("/trace/{pipeline_id}")
async def get_pipeline_trace(pipeline_id: str):
    """获取管线追踪事件（返回 JSON 数组或 SSE 流）。"""
    # 简单检测：如果 Accept 不是 text/event-stream，返回 JSON
    from fastapi import Request
    from starlette.requests import Request as StarletteRequest
    # 直接返回 JSON
    return get_all_events(pipeline_id)


@router.get("/trace/stream/{pipeline_id}")
async def stream_pipeline_trace(pipeline_id: str, request: Request):
    """SSE 流：实时推送管线执行追踪事件（LLM、Tool、Skill、Plugin 调用）。"""
    async def event_stream():
        last_time = time.time()
        # 先发送已存在的事件
        for event in get_events(pipeline_id):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            last_time = max(last_time, event["time"])

        # 持续轮询新事件直到管线终态（done/error）或达到长上限。
        # 管线动画阶段可能长达 40-60 分钟，固定 600s 会让前端 SSE 中途断流而收不到终态。
        # B7：max_wall 动态化——从结果缓存中取该管线的 pipeline_timeout_sec（proceed 时写入
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
        start_wall = time.time()
        while time.time() - start_wall < max_wall:
            if await request.is_disconnected():
                return
            await asyncio.sleep(0.5)
            events = get_events(pipeline_id, since=last_time)
            if events:
                for event in events:
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    last_time = max(last_time, event["time"])
                    # 收到 done/error 事件后延 2 秒关闭
                    if event["type"] in ("done", "error"):
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
async def get_pipeline_result(pipeline_id: str) -> dict:
    """获取异步管线执行结果。如果管线仍在运行，最多轮询 5 分钟等待。"""
    import asyncio

    # 先返回已有结果
    result = _pipeline_results.get(pipeline_id)
    if result is not None:
        return result
    # 没有结果 → 检查是否还在运行
    task = _running_pipelines.get(pipeline_id)
    if task is None:
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
    result = _pipeline_results.get(pipeline_id)
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
    from clipwright.services.pipeline_v2 import PipelineOrchestratorV2

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

    category = PipelineOrchestratorV2._categorize_error(error or (failed_step or {}).get("error") or "")
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
    """重试指定 Agent（从失败的管线中恢复，B3）。

    改用 PipelineOrchestratorV2.run_from_agent：从 prior_state 重放目标 agent 之前的
    成功结果，只重跑目标 agent + 下游联动 + 统一自愈质检循环（不再全量重跑）。
    目标 agent 无可用前置结果/无记录时明确 400。
    """
    _enforce_pipeline_owner(request, pipeline_id)
    from clipwright.services.trace import get_all_events
    events = get_all_events(pipeline_id)
    if not events:
        raise HTTPException(status_code=404, detail=f"Pipeline {pipeline_id} not found")

    # 重建上下文，只从失败 agent 开始重新执行
    state = _pipeline_results.get(pipeline_id)
    if not state:
        raise HTTPException(status_code=400, detail="Pipeline result not available for retry")

    request_data = state.get("request")
    if not isinstance(request_data, dict):
        raise HTTPException(status_code=400, detail="Pipeline result missing request data")
    request = PipelineRequest(**request_data)

    # 前置校验：目标 agent 必须存在于 steps 且之前有成功结果，否则同步 400
    steps = state.get("steps") or []
    target_index = None
    for i, s in enumerate(steps):
        if isinstance(s, dict) and s.get("agent_name") == agent_name:
            target_index = i
            break
    if target_index is None:
        raise HTTPException(
            status_code=400,
            detail=f"Pipeline {pipeline_id} 中未找到 Agent {agent_name}",
        )
    has_preceding = any(
        isinstance(s, dict) and s.get("status") == "completed" and s.get("result")
        for s in steps[:target_index]
    )
    if not has_preceding:
        raise HTTPException(
            status_code=400,
            detail=f"Pipeline {pipeline_id} 无可用前置成功结果，无法从 {agent_name} 恢复",
        )

    add_event(pipeline_id, "system", "info", f"重试 Agent: {agent_name}")

    async def _retry():
        try:
            orch_v2 = PipelineOrchestratorV2()
            new_state = await orch_v2.run_from_agent(
                pipeline_id, request, agent_name, prior_state=state,
            )
            result_dict = new_state.model_dump(mode="json")
            _pipeline_results[pipeline_id] = result_dict
            add_event(pipeline_id, "system", "done", f"重试完成: {new_state.status}")
        except Exception as e:
            add_event(pipeline_id, "system", "error", f"重试失败: {e}")

    task = asyncio.create_task(_retry())
    _running_pipelines[pipeline_id] = task
    return {"pipeline_id": pipeline_id, "status": "retrying", "agent": agent_name}


@router.post("/cancel/{pipeline_id}")
async def cancel_pipeline(pipeline_id: str, request: Request) -> dict:
    """取消管线（C9：即时取消）。

    1. 先取消后台 asyncio 任务（中断 in-flight agent/LLM 调用）；
    2. 同时标记协作式取消标志（任务已结束/不存在时仍能阻止后续 agent）；
    3. 终态写入 CANCELLED / cancelled 并写 trace 事件。
    若管线已结束/不存在，返回 404。
    """
    from clipwright.services.pipeline_v2 import mark_cancelled
    from clipwright.services.trace import get_all_events as _ga

    _enforce_pipeline_owner(request, pipeline_id)
    if not _ga(pipeline_id):
        raise HTTPException(status_code=404, detail=f"Pipeline {pipeline_id} not found")

    # C9: 即时取消 — 中断运行中的后台任务（CancelledError 由 _run_background 捕获写终态）
    task = _running_pipelines.get(pipeline_id)
    if task and not task.done():
        task.cancel()
        logger.info("取消请求: %s (即时取消后台任务)", pipeline_id)
    else:
        logger.info("取消请求: %s (任务已不在运行，仅标记协作式取消)", pipeline_id)

    mark_cancelled(pipeline_id)
    add_event(pipeline_id, "system", "cancelled", "已请求取消管线")
    return {"pipeline_id": pipeline_id, "status": "cancelling"}


@router.get("/breaker-status")
async def pipeline_breaker_status(request: Request) -> dict:
    """C8: 熔断健康探测 — 返回各 Agent 熔断计数与状态（运维/前端监控用）。"""
    from datetime import datetime, timezone
    from clipwright.services.pipeline_v2 import PipelineOrchestratorV2

    now = datetime.now(timezone.utc)
    breakers = PipelineOrchestratorV2._circuit_breakers
    threshold = PipelineOrchestratorV2._circuit_breaker_threshold
    recovery = PipelineOrchestratorV2._circuit_breaker_recovery_sec
    agents: list[dict] = []
    tripped = 0
    for name, cb in sorted(breakers.items()):
        count = int(cb.get("fail_count", 0))
        last = cb.get("last_fail_at")
        elapsed = (now - last).total_seconds() if last else None
        is_open = count >= threshold and (elapsed is None or elapsed <= recovery)
        if is_open:
            tripped += 1
        agents.append({
            "agent": name,
            "fail_count": count,
            "threshold": threshold,
            "open": is_open,
            "recovery_sec": recovery,
            "seconds_since_fail": round(elapsed, 1) if elapsed is not None else None,
        })
    return {
        "status": "degraded" if tripped > 0 else "ok",
        "tripped_agents": tripped,
        "agents": agents,
    }


class PredictScriptRequest(BaseModel):
    script_text: str = Field(..., max_length=50000, description="文稿内容")

class PredictMaterialRequest(BaseModel):
    file_path: str = Field(..., description="素材文件路径")
    file_size: int = Field(default=0, ge=0, description="文件大小(bytes)")


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


@router.post("/step/{agent_name}")
async def run_single_agent(agent_name: str, request: PipelineRequest) -> dict:
    """执行完整 Pipeline 并返回指定 Agent 的结果。

    Deprecated (B5): 语义为「运行完整管线并返回指定 agent 步骤结果（非隔离执行）」，
    前端零调用；保留兼容不删除，但调用方不应依赖其"单步"语义。
    """
    logger.warning("DEPRECATED: POST /api/pipeline/step/%s 被调用", agent_name)
    state = await _orchestrator.run(request)
    step = state.get_step(agent_name)
    if step is None:
        raise HTTPException(status_code=404, detail=f"Agent {agent_name} not executed")
    return {
        "agent_name": agent_name,
        "status": step.status,
        "result": step.result,
        "error": step.error,
        "deprecated": True,
    }
