"""需求工作台 API — 对话式创作方案 + 规划书 + SSE 流式。"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from clipwright.config import logger
from clipwright.services.requirements_service import RequirementsService

router = APIRouter(prefix="/api/requirements", tags=["requirements"])

_service = RequirementsService()


# ── 请求模型 ──────────────────────────────────

class InitRequest(BaseModel):
    topic: str = ""
    persona_id: str = ""
    category_plugin_id: str = ""
    script_text: str = ""
    audio_duration_sec: float = 0
    extra: dict[str, Any] = {}


class ChatRequest(BaseModel):
    session_id: str
    message: str


class ProceedRequest(BaseModel):
    session_id: str
    persona_id: str = ""
    category_plugin_id: str = ""
    extra_params: dict[str, Any] = {}


# ── API 端点 ─────────────────────────────────

@router.post("/init")
async def init_session(req: InitRequest) -> dict:
    """初始化需求对话会话。"""
    user_inputs = {
        "topic": req.topic,
        "persona_id": req.persona_id,
        "category_plugin_id": req.category_plugin_id,
        "script_text": req.script_text,
        "audio_duration_sec": req.audio_duration_sec,
        **req.extra,
    }
    session = await asyncio.to_thread(_service.create_session, user_inputs)
    return session


@router.post("/chat")
async def chat_message(req: ChatRequest) -> dict:
    """发送对话消息（非流式）。"""
    try:
        result = await _service.chat(req.session_id, req.message)
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Chat error: %s", e)
        raise HTTPException(status_code=500, detail="会话处理失败，请稍后重试")


@router.post("/chat/stream/{session_id}")
async def chat_stream(session_id: str, message: str = Form(...)):
    """SSE 流式对话 — 逐块推送状态和结果。"""
    async def event_stream():
        try:
            async for chunk in _service.stream_chat(session_id, message):
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.exception("SSE stream error: %s", e)
            yield f"data: {json.dumps({'type': 'error', 'data': '对话处理失败，请稍后重试'}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/upload/{session_id}")
async def upload_file(
    session_id: str,
    file: UploadFile = File(...),
):
    """上传参考文件（图片/文档等）。"""
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id required")

    import tempfile
    import os
    suffix = os.path.splitext(file.filename or "file")[1] or ".bin"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        # process_upload is async — await it directly (asyncio.to_thread would only
        # create an unawaited coroutine and never execute it)
        result = await _service.process_upload(session_id, tmp_path, file.filename or "file")
        return result
    except Exception as e:
        logger.exception("Upload error: %s", e)
        raise HTTPException(status_code=500, detail="会话处理失败，请稍后重试")
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


@router.get("/session/{session_id}")
async def get_session(session_id: str) -> dict:
    """获取会话完整状态。"""
    session = await asyncio.to_thread(_service.get_session, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
    return session


@router.get("/plan/{session_id}")
async def get_plan(session_id: str) -> dict:
    """获取规划书。"""
    plan = await asyncio.to_thread(_service.get_plan, session_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found or not yet generated")
    return plan


@router.post("/proceed")
async def proceed_to_pipeline(req: ProceedRequest) -> dict:
    """确认规划书 → 启动管线，返回 pipeline_id 供前端追踪（SSE + result）。"""
    session = await asyncio.to_thread(_service.get_session, req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    plan_data = session.get("production_plan")
    if not plan_data:
        raise HTTPException(status_code=400, detail="Plan not ready")

    import uuid
    from clipwright.services.pipeline_v2 import PipelineOrchestratorV2
    from clipwright.schema.pipeline import PipelineRequest
    from clipwright.services.trace import create_trace, add_event, get_all_events
    from clipwright.services.async_util import spawn_background
    from clipwright.api.pipeline import _pipeline_results, _running_pipelines

    # 预先生成 pipeline_id 并建立 trace，使前端可立即订阅 SSE / 轮询 result
    pipeline_id = f"pl_{uuid.uuid4().hex[:12]}"
    create_trace(pipeline_id)

    user_inputs = session.get("user_inputs", {})
    pipeline_req = PipelineRequest(
        persona_id=req.persona_id or user_inputs.get("persona_id", "default"),
        category_plugin_id=req.category_plugin_id or user_inputs.get("category_plugin_id", "knowledge_longform"),
        topic=user_inputs.get("topic", ""),
        extra_params={
            "script_text": user_inputs.get("script_text", ""),
            "audio_duration_sec": user_inputs.get("audio_duration_sec", 0),
            "audio_path": user_inputs.get("audio_path", ""),
            "video_mode": user_inputs.get("video_mode", "voiceover"),
            "split_mode": user_inputs.get("split_mode", "period"),
            "auto_dub": user_inputs.get("auto_dub", True),
            "voice_id": user_inputs.get("voice_id", ""),
            "dub_segments": user_inputs.get("dub_segments", []),
            "creative_brief": session.get("creative_brief"),
            "production_plan": session.get("production_plan"),
            **req.extra_params,
        },
        use_v2=True,
    )
    add_event(pipeline_id, "system", "info",
              f"由需求确认启动管线: {pipeline_req.persona_id} / {pipeline_req.category_plugin_id}")

    async def _run():
        try:
            orch = PipelineOrchestratorV2()
            state = await orch.run(pipeline_req, pipeline_id=pipeline_id)
            state.shared_data["execution_trace"] = get_all_events(pipeline_id)
            _pipeline_results[pipeline_id] = state.model_dump(mode="json")
            add_event(pipeline_id, "system", "done", f"管线完成: {state.status}")
            # 更新会话状态
            await asyncio.to_thread(
                _service._persist,
                req.session_id, "pipeline_done",
                session.get("messages", []),
                session.get("creative_brief"),
                session.get("production_plan"),
                session.get("user_inputs", {}),
            )
            logger.info("管线完成: pipeline_id=%s, status=%s", state.pipeline_id, state.status)
        except Exception as e:
            logger.exception("Pipeline failed: %s", e)
            add_event(pipeline_id, "system", "error", f"管线失败: {e}")
            _pipeline_results[pipeline_id] = {"status": "failed", "error": str(e), "pipeline_id": pipeline_id}
        finally:
            async def _cleanup():
                import asyncio as _a
                await _a.sleep(60)
                _pipeline_results.pop(pipeline_id, None)
                _running_pipelines.pop(pipeline_id, None)
                from clipwright.services.trace import clear
                clear(pipeline_id)
            spawn_background(_cleanup(), name=f"pipeline-cleanup-{pipeline_id}")

    _running_pipelines[pipeline_id] = spawn_background(_run(), name=f"requirements-pipeline-{req.session_id}")
    return {"session_id": req.session_id, "pipeline_id": pipeline_id, "status": "pipeline_started"}
