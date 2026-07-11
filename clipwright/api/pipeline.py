"""Pipeline API — 全流程执行、单 Agent 执行、实时追踪。"""

from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from clipwright.schema.pipeline import PipelineRequest, PipelineState
from clipwright.services.pipeline import PipelineOrchestrator
from clipwright.services.trace import get_events, get_all_events, create_trace, add_event

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])
_orchestrator = PipelineOrchestrator()


# 后台运行的任务缓存
_running_pipelines: dict[str, asyncio.Task] = {}


@router.post("/run", response_model=PipelineState)
async def run_pipeline(request: PipelineRequest) -> PipelineState:
    """全流程执行，返回完整时间线。"""
    state = await _orchestrator.run(request)
    state.shared_data["execution_trace"] = get_all_events(state.pipeline_id)
    if state.status == "failed":
        raise HTTPException(status_code=400, detail=state.error)
    return state


@router.post("/run-async")
async def run_pipeline_async(request: PipelineRequest) -> dict:
    """异步启动管线，立即返回 pipeline_id，进度通过 SSE trace 推送。"""
    import uuid
    pipeline_id = f"pl_{uuid.uuid4().hex[:12]}"
    create_trace(pipeline_id)
    add_event(pipeline_id, "system", "info", f"管线异步启动: {request.persona_id} / {request.category_plugin_id}")

    async def _run_background():
        try:
            state = await _orchestrator.run(request, pipeline_id=pipeline_id)
            state.shared_data["execution_trace"] = get_all_events(pipeline_id)
            add_event(pipeline_id, "system", "done", f"管线完成: {state.status}")
        except Exception as e:
            add_event(pipeline_id, "system", "error", f"管线失败: {e}")
            import traceback
            add_event(pipeline_id, "system", "error_detail", traceback.format_exc())

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
async def stream_pipeline_trace(pipeline_id: str):
    """SSE 流：实时推送管线执行追踪事件（LLM、Tool、Skill、Plugin 调用）。"""
    async def event_stream():
        last_time = time.time()
        # 先发送已存在的事件
        for event in get_events(pipeline_id):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            last_time = max(last_time, event["time"])

        # 然后持续轮询新事件（最多 120 秒）
        for _ in range(240):  # 240 * 0.5s = 120s 超时
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


@router.get("/status/{pipeline_id}", response_model=PipelineState)
async def get_pipeline_status(pipeline_id: str) -> PipelineState:
    """查询管线执行状态。"""
    raise HTTPException(status_code=404, detail=f"Pipeline {pipeline_id} not found")


@router.post("/step/{agent_name}")
async def run_single_agent(agent_name: str, request: PipelineRequest) -> dict:
    """单 Agent 执行。"""
    state = await _orchestrator.run(request)
    step = state.get_step(agent_name)
    if step is None:
        raise HTTPException(status_code=404, detail=f"Agent {agent_name} not executed")
    return {
        "agent_name": agent_name,
        "status": step.status,
        "result": step.result,
        "error": step.error,
    }
