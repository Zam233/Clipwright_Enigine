"""需求工作台 API — 对话式创作方案 + 规划书 + SSE 流式。"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

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


class EditRequest(BaseModel):
    session_id: str
    message: str
    timeline: dict[str, Any] = {}
    selected_clip_ids: list[str] = []
    # W12: 区域级返工 — 编辑范围（秒）；提供时仅返回/改写该时间窗内的片段
    region_start_sec: float | None = None
    region_end_sec: float | None = None


class ProceedRequest(BaseModel):
    session_id: str
    persona_id: str = ""
    category_plugin_id: str = ""
    extra_params: dict[str, Any] = {}
    # E2E 修复：管线完成后把 final_timeline 保存到该项目（防止内存结果 60s 清理后丢失）
    project_id: str = ""
    # C5: beat-sync 开关与 BPM——EditAgent 读取 cut_on_beat/bpm（卡点剪辑），
    # 但此前全仓无任何写入方，功能永久禁用
    cut_on_beat: bool = False
    beat_bpm: float = Field(default=0, ge=0, le=300)


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


@router.post("/edit")
async def edit_timeline(req: EditRequest) -> dict:
    """时间线编辑：按选中素材 + 自然语言指令，三路分发（换素材 / 重做动画 / 数值调整）。

    整体用 wait_for 兜底：即使底层 Agent/LLM 意外卡死，也保证在时限内返回可重试错误。
    """
    EDIT_HARD_TIMEOUT = 660
    try:
        result = await asyncio.wait_for(
            _service.edit_timeline(
                req.session_id, req.message, req.timeline, req.selected_clip_ids,
                region_start_sec=req.region_start_sec,
                region_end_sec=req.region_end_sec,
            ),
            timeout=EDIT_HARD_TIMEOUT,
        )
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return result
    except asyncio.TimeoutError:
        logger.error(
            "时间线编辑处理超时（>%ss），会话 %s 可能卡死", EDIT_HARD_TIMEOUT, req.session_id
        )
        raise HTTPException(status_code=504, detail="处理超时，请稍后重试")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Timeline edit error: %s", e)
        raise HTTPException(status_code=500, detail="时间线编辑失败，请稍后重试")


@router.post("/chat")
async def chat_message(req: ChatRequest) -> dict:
    """发送对话消息（非流式）。

    整体用 wait_for 兜底：即使底层 LLM/线程意外卡死（asyncio.to_thread 的线程
    无法被取消），也保证在时限内返回一个可重试的错误响应，而不是让请求永久挂起。
    """
    CHAT_HARD_TIMEOUT = 660  # 秒：简报 180s + 规划书 240s + 翻译/重试余量
    try:
        result = await asyncio.wait_for(
            _service.chat(req.session_id, req.message),
            timeout=CHAT_HARD_TIMEOUT,
        )
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return result
    except asyncio.TimeoutError:
        logger.error("需求对话处理超时（>%ss），会话 %s 可能卡死，返回可重试错误", CHAT_HARD_TIMEOUT, req.session_id)
        raise HTTPException(status_code=504, detail="处理超时，请稍后重试")
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
async def proceed_to_pipeline(req: ProceedRequest, request: Request) -> dict:
    """确认规划书 → 启动管线，返回 pipeline_id 供前端追踪（SSE + result）。"""
    session = await asyncio.to_thread(_service.get_session, req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    plan_data = session.get("production_plan")
    if not plan_data:
        raise HTTPException(status_code=400, detail="Plan not ready")

    import uuid
    from clipwright import audit
    from clipwright.authz import current_user_id
    from clipwright.services.pipeline_v2 import PipelineOrchestratorV2
    from clipwright.schema.pipeline import PipelineRequest
    from clipwright.services.trace import create_trace, add_event, get_all_events
    from clipwright.services.async_util import spawn_background
    from clipwright.api.pipeline import (
        _pipeline_results, _running_pipelines, _pipeline_owners,
        _pipeline_tasks, _user_cancel_requested, _persist_pipeline_runtime,
        pipeline_timeout_sec, _extract_quality_summary, _aggregate_agent_notes,
    )

    # A5: proceed 与 /run-async 同权——预算熔断检查（原路径完全绕过预算）
    from clipwright.services.budget import check_budget
    allowed, used = await check_budget()
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"本月 LLM token 预算已耗尽（已用 {used}），无法启动管线",
        )

    uid = current_user_id(request)

    # 预先生成 pipeline_id 并建立 trace，使前端可立即订阅 SSE / 轮询 result
    pipeline_id = f"pl_{uuid.uuid4().hex[:12]}"
    create_trace(pipeline_id)

    user_inputs = session.get("user_inputs", {})
    # 长视频管线耗时随配音时长增长（逐场景素材处理），动态调高超时避免误超时。
    # B7：前后端共用统一公式（audio×6 / scene×360 / min 1800），与前端 ReviewPanel 对齐。
    _audio_dur = float(user_inputs.get("audio_duration_sec", 0) or 0)
    _scene_count = int((plan_data or {}).get("scene_count", 0) or 0)
    # 规划书未给出场景数时按音频时长估算（约每 15 秒一个场景）兜底，保证场景余量不失效。
    if _scene_count <= 0 and _audio_dur > 0:
        _scene_count = int(_audio_dur / 15)
    _pipeline_timeout = pipeline_timeout_sec(_audio_dur, _scene_count)
    # C2: 接通 animation_intents 通道——需求会话简报中的动画意图此前从未
    # 放入 extra_params，StructureAgent（extra_params.get("animation_intents")）
    # 永远读到空，聊天里确认的动画意图从不影响成片
    _cb = session.get("creative_brief") or {}
    _raw_intents = _cb.get("animation_intents") if isinstance(_cb, dict) else None
    if not isinstance(_raw_intents, list):
        _draft = _cb.get("brief_draft") if isinstance(_cb, dict) else None
        _raw_intents = _draft.get("animation_intents") if isinstance(_draft, dict) else None
    animation_intents = _raw_intents if isinstance(_raw_intents, list) else []
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
            "pipeline_timeout_sec": _pipeline_timeout,
            # C5: beat-sync 透传（用户 extra_params 显式值优先）
            **({"cut_on_beat": True, "bpm": float(req.beat_bpm)}
               if req.cut_on_beat and req.beat_bpm > 0 else {}),
            **req.extra_params,
        },
        # P8: dry-run 预览模式（仅生成粗剪时间线，跳过动画/音频/质检）
        dry_run=bool(req.extra_params.get("dry_run", False)),
        use_v2=True,
    )
    add_event(pipeline_id, "system", "info",
              f"由需求确认启动管线: {pipeline_req.persona_id} / {pipeline_req.category_plugin_id}")

    # A5: 归属注册 + 运行态持久化 + 审计（与 run-async 对齐；此前 jwt 模式下
    # proceed 发起的管线无 owner，本人 /status /cancel /diagnostics 全部 403）
    _pipeline_owners[pipeline_id] = uid or ""
    _persist_pipeline_runtime(pipeline_id, owner_id=uid or "")
    audit.record("pipeline_run", uid, {
        "pipeline_id": pipeline_id,
        "session_id": req.session_id,
        "persona_id": pipeline_req.persona_id,
        "category_plugin_id": pipeline_req.category_plugin_id,
        "source": "requirements_proceed",
    })

    async def _run(task_id: str = ""):
        try:
            # A5: 走 TaskQueue 执行（并发上限 + 任务级超时与管线超时公式对齐）
            from clipwright.services.task_queue import get_task_queue

            async def _queue_handler(inner_task_id: str = "") -> None:
                # A3 对齐: 注册运行中任务，使 /cancel 能即时中断
                _running_pipelines[pipeline_id] = asyncio.current_task()
                try:
                    orch = PipelineOrchestratorV2()
                    state = await orch.run(pipeline_req, pipeline_id=pipeline_id,
                                           task_id=inner_task_id or task_id)
                    state.shared_data["execution_trace"] = get_all_events(pipeline_id)
                    # C7/C9: 质检摘要 + Agent 备注（与 run-async 对齐）
                    _rd = state.model_dump(mode="json")
                    _q = _extract_quality_summary(_rd)
                    if _q:
                        _rd["quality_issues"] = _q
                    _notes = _aggregate_agent_notes(_rd)
                    if _notes:
                        _rd["agent_notes"] = _notes
                    _pipeline_results[pipeline_id] = _rd
                    add_event(pipeline_id, "system", "done", f"管线完成: {state.status}", _q)
                    # E2E 修复：管线完成后把 final_timeline 保存到关联项目（若有），
                    # 避免内存结果 60s 清理后 timeline 丢失（API 直连场景）。
                    try:
                        final_tl = state.shared_data.get("final_timeline")
                        if final_tl and req.project_id:
                            from clipwright.services.project_manager import ProjectManager
                            pm = ProjectManager()
                            pm.save(req.project_id, {"timeline": final_tl})
                            logger.info("管线时间线已保存到项目: %s", req.project_id)
                    except Exception as e:
                        logger.warning("管线时间线保存失败: %s", e)
                    # 更新会话状态
                    await asyncio.to_thread(
                        _service._persist,
                        req.session_id, "pipeline_done",
                        session.get("messages", []),
                        session.get("creative_brief"),
                        session.get("production_plan"),
                        session.get("user_inputs", {}),
                    )
                    logger.info("管线完成: pipeline_id=%s, status=%s", pipeline_id, state.status)
                except asyncio.CancelledError:
                    # A2 对齐: 用户取消写 cancelled 终态（原实现无 CancelledError
                    # 处理，取消后 /result 回落到 Mongo 的 running 快照）
                    if pipeline_id in _user_cancel_requested:
                        _user_cancel_requested.discard(pipeline_id)
                        _pipeline_results[pipeline_id] = {
                            "status": "cancelled", "pipeline_id": pipeline_id,
                            "error": "管线已取消（任务中断）",
                        }
                        add_event(pipeline_id, "system", "cancelled", "管线已取消（任务中断）")
                    else:
                        _pipeline_results[pipeline_id] = {
                            "status": "timeout", "pipeline_id": pipeline_id,
                            "error": f"管线执行超时（>{_pipeline_timeout}s），可重试",
                        }
                        add_event(pipeline_id, "system", "timeout",
                                  f"管线执行超时（>{_pipeline_timeout}s）")
                except Exception as e:
                    logger.exception("Pipeline failed: %s", e)
                    add_event(pipeline_id, "system", "error", f"管线失败: {e}")
                    _pipeline_results[pipeline_id] = {"status": "failed", "error": str(e), "pipeline_id": pipeline_id}
                finally:
                    if _running_pipelines.get(pipeline_id) is asyncio.current_task():
                        _running_pipelines.pop(pipeline_id, None)
                    async def _cleanup():
                        import asyncio as _a
                        await _a.sleep(60)
                        _pipeline_results.pop(pipeline_id, None)
                        _running_pipelines.pop(pipeline_id, None)
                        _pipeline_owners.pop(pipeline_id, None)
                        _pipeline_tasks.pop(pipeline_id, None)
                        _user_cancel_requested.discard(pipeline_id)
                        from clipwright.services.trace import clear
                        clear(pipeline_id)
                    spawn_background(_cleanup(), name=f"pipeline-cleanup-{pipeline_id}")

            _submit_timeout = float(_pipeline_timeout) + 120.0
            tq_task_id = await get_task_queue().submit(
                "pipeline", _queue_handler, timeout_sec=_submit_timeout,
            )
            _pipeline_tasks[pipeline_id] = tq_task_id
            _persist_pipeline_runtime(pipeline_id, task_id=tq_task_id)
        except Exception as e:
            # 队列提交失败：写失败终态（不静默吞掉，前端可感知并重试）
            logger.exception("TaskQueue 提交失败: %s", e)
            _pipeline_results[pipeline_id] = {
                "status": "failed", "error": f"管线任务提交失败: {e}",
                "pipeline_id": pipeline_id,
            }
            add_event(pipeline_id, "system", "error", f"管线任务提交失败: {e}")

    _running_pipelines[pipeline_id] = spawn_background(
        _run(), name=f"requirements-pipeline-{req.session_id}",
    )
    return {"session_id": req.session_id, "pipeline_id": pipeline_id, "status": "pipeline_started"}
