"""Pipeline API — 全流程执行、单 Agent 执行、局部重执行。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from clipwright.schema.pipeline import PipelineRequest, PipelineState
from clipwright.services.pipeline import PipelineOrchestrator

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])
_orchestrator = PipelineOrchestrator()


@router.post("/run", response_model=PipelineState)
async def run_pipeline(request: PipelineRequest) -> PipelineState:
    """全流程执行，返回完整时间线。"""
    state = await _orchestrator.run(request)
    if state.status == "failed":
        raise HTTPException(status_code=400, detail=state.error)
    return state


@router.get("/status/{pipeline_id}", response_model=PipelineState)
async def get_pipeline_status(pipeline_id: str) -> PipelineState:
    """查询管线执行状态。"""
    # Phase 1 占位：返回空状态
    # 后续接入持久化存储
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
