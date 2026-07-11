"""Pipeline API — 全流程执行、单 Agent 执行、实时追踪。"""

from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from clipwright.schema.pipeline import PipelineRequest, PipelineState
from clipwright.services.pipeline import PipelineOrchestrator
from clipwright.services.trace import get_events

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])
_orchestrator = PipelineOrchestrator()


@router.post("/run", response_model=PipelineState)
async def run_pipeline(request: PipelineRequest) -> PipelineState:
    """全流程执行，返回完整时间线。"""
    state = await _orchestrator.run(request)
    # 将追踪事件附加到返回数据
    from clipwright.services.trace import get_all_events
    state.shared_data["execution_trace"] = get_all_events(state.pipeline_id)
    if state.status == "failed":
        raise HTTPException(status_code=400, detail=state.error)
    return state


@router.get("/trace/{pipeline_id}")
async def stream_pipeline_trace(pipeline_id: str):
    """SSE 流：实时推送管线执行追踪事件（LLM、Tool、Skill、Plugin 调用）。"""
    async def event_stream():
        last_time = time.time()
        # 先发送已存在的事件
        for event in get_events(pipeline_id):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            last_time = max(last_time, event["time"])

        # 然后持续轮询新事件（最多 60 秒）
        for _ in range(120):  # 120 * 0.5s = 60s 超时
            await asyncio.sleep(0.5)
            events = get_events(pipeline_id, since=last_time)
            if events:
                for event in events:
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    last_time = max(last_time, event["time"])
            # 当 pipeline 完成时（最后一条是 agent_end 或 error），再多发 2 秒后停止
            last_events = get_events(pipeline_id)
            if last_events:
                last_type = last_events[-1]["type"]
                if last_type in ("agent_end", "error") and _ > 4:
                    break

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
