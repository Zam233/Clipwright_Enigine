"""Pipeline API — 全流程执行、单 Agent 执行、实时追踪。"""

from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

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


@router.post("/run-v2")
async def run_pipeline_v2(request: PipelineRequest) -> dict:
    """运行 PipelineV2（动态路由 + 自愈循环）。"""
    import uuid
    pipeline_id = f"pl_v2_{uuid.uuid4().hex[:12]}"
    create_trace(pipeline_id)
    add_event(pipeline_id, "system", "info", f"PipelineV2 启动: {request.persona_id} / {request.category_plugin_id}")

    orch_v2 = PipelineOrchestratorV2()
    try:
        state = await orch_v2.run(request, pipeline_id=pipeline_id)
    except Exception as e:
        return {"pipeline_id": pipeline_id, "status": "failed", "error": str(e)}
    return {
        "pipeline_id": pipeline_id,
        "status": state.status.value,
        "steps": [{"agent": s.agent_name, "status": s.status.value} for s in state.steps],
        "error": state.error,
    }


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
        except Exception as e:
            logger.exception("pipeline._run_background failed: %s", e)
            add_event(pipeline_id, "system", "error", f"管线失败: {e}")
            # 即使异常也写一个结果，让前端能查到错误
            _pipeline_results[pipeline_id] = {"status": "failed", "error": str(e), "pipeline_id": pipeline_id}
        finally:
            # 60秒后清理内存，给前端足够时间轮询
            async def _cleanup():
                await asyncio.sleep(60)
                _pipeline_results.pop(pipeline_id, None)
                _running_pipelines.pop(pipeline_id, None)
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

        # 然后持续轮询新事件（最多 600 秒；客户端断开即退出）
        for _ in range(1200):  # 1200 * 0.5s = 600s 超时
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
async def get_pipeline_status(pipeline_id: str) -> dict:
    """查询管线执行状态。"""
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


@router.post("/retry/{pipeline_id}/{agent_name}")
async def retry_agent(pipeline_id: str, agent_name: str) -> dict:
    """重试指定 Agent（从失败的管线中恢复）。"""
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

    add_event(pipeline_id, "system", "info", f"重试 Agent: {agent_name}")

    async def _retry():
        try:
            new_state = await _orchestrator.run(request, pipeline_id=pipeline_id)
            result_dict = new_state.model_dump(mode="json")
            _pipeline_results[pipeline_id] = result_dict
            add_event(pipeline_id, "system", "done", f"重试完成: {new_state.status}")
        except Exception as e:
            add_event(pipeline_id, "system", "error", f"重试失败: {e}")

    task = asyncio.create_task(_retry())
    _running_pipelines[pipeline_id] = task
    return {"pipeline_id": pipeline_id, "status": "retrying", "agent": agent_name}


@router.post("/regenerate-scene/{pipeline_id}/{scene_index}")
async def regenerate_scene(pipeline_id: str, scene_index: int) -> dict:
    """重新生成指定场景（局部编辑）。"""
    from clipwright.services.pipeline import PipelineOrchestrator
    orch = PipelineOrchestrator()

    state = _pipeline_results.get(pipeline_id)
    if not state:
        raise HTTPException(status_code=404, detail="Pipeline result not available")

    timeline_data = state.get("shared_data", {}).get("final_timeline")
    if not timeline_data:
        raise HTTPException(status_code=400, detail="No final timeline in pipeline result")

    # 局部重生成：重新跑 structure + material + edit
    import uuid as _uuid
    new_pid = f"pl_scene_{_uuid.uuid4().hex[:12]}"
    create_trace(new_pid)
    add_event(new_pid, "system", "info", f"局部重生成场景[{scene_index}]")

    request_data = state.get("request")
    if not isinstance(request_data, dict):
        raise HTTPException(status_code=400, detail="Pipeline result missing request data")
    request = PipelineRequest(**request_data)

    async def _run():
        try:
            new_state = await orch.run(request, pipeline_id=new_pid)
            new_ft = new_state.shared_data.get("final_timeline")
            if new_ft:
                # 仅替换场景[scene_index]的 clip
                if scene_index < len(timeline_data.get("tracks", [])):
                    for track_idx, track in enumerate(timeline_data.get("tracks", [])):
                        old_clips = track.get("clips", [])
                        new_tracks = new_ft.get("tracks", [])
                        if track_idx < len(new_tracks):
                            new_clips = new_tracks[track_idx].get("clips", [])
                            if scene_index < len(new_clips):
                                old_clips[scene_index] = new_clips[scene_index]
                state["shared_data"]["final_timeline"] = timeline_data
            _pipeline_results[new_pid] = state
            add_event(new_pid, "system", "done", f"场景[{scene_index}] 重生成完成")
        except Exception as e:
            add_event(new_pid, "system", "error", f"场景重生成失败: {e}")

    _running_pipelines[new_pid] = spawn_background(_run(), name=f"scene-regen-{new_pid}")
    return {"pipeline_id": new_pid, "scene_index": scene_index, "status": "regenerating"}


@router.post("/predict-script")
async def predict_script(script_text: str) -> dict:
    """智能预判：分析文稿并推荐 Persona/类型/时长。"""
    result = await ScriptAnalyzer.analyze(script_text)
    return result


@router.post("/predict-material")
async def predict_material(file_path: str, file_size: int = 0) -> dict:
    """智能预判：分析素材并推荐使用方式。"""
    result = await MaterialAnalyzer.analyze(file_path, file_size)
    return result


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
