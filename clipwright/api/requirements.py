"""需求工作台 API — 对话式创作方案 + 规划书 + SSE 流式。"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from clipwright.config import logger, settings
from clipwright.services.requirements_service import RequirementsService

router = APIRouter(prefix="/api/requirements", tags=["requirements"])

_service = RequirementsService()


# ── 请求模型 ──────────────────────────────────

# 需求会话归属表（对齐管线 _pipeline_owners 机制）
_session_owners: dict[str, str] = {}

# P3-13: proceed 幂等统一到共享存储（与 /pipeline/run-async 共用键空间）
from clipwright.services import idempotency as _idem


def _proceed_idem_hit(key: str, running: dict, tasks: dict) -> Optional[str]:
    """轮70（D10）：proceed 幂等命中——目标仍在运行/排队则返回既有 id。

    `agent_mode=on` 时目标可能是**任务**（`task_*`）而不是管线：任务只登记在
    `api.task._RUNNING` 里，不进 `_running_pipelines`/`_pipeline_tasks`。旧实现只查后两张表，
    于是双击 proceed 会各建一个 Task、双倍扣费（审计 P1-1）。这里补查任务表。
    """
    pid = _idem.lookup(key)
    if not pid:
        return None
    if pid in running or pid in tasks:
        return pid
    if pid.startswith("task_"):
        try:
            from clipwright.api.task import _RUNNING as _TASK_RUNNING

            if pid in _TASK_RUNNING:
                return pid
        except Exception:  # noqa: BLE001 — 任务表不可用不影响老路径
            return None
    return None


def _require_session_owner(request: Request, session_id: str) -> None:
    """jwt 模式下校验会话所有权；无主记录放行（兼容 off 模式与历史会话）。"""
    from clipwright.authz import current_user_id, is_admin

    uid = current_user_id(request)
    if uid is None or is_admin(request):
        return
    if _session_owners.get(session_id) not in (uid, None):
        raise HTTPException(status_code=403, detail=f"无权访问会话 {session_id}")


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
    # 区域级返工 — 编辑范围（秒）；提供时仅返回/改写该时间窗内的片段
    region_start_sec: float | None = None
    region_end_sec: float | None = None


class IntentRequest(BaseModel):
    """R6：自然语言指令 → 意图 → 落位预览（**不修改时间线**）。"""
    message: str
    timeline: dict[str, Any] = {}
    selected_clip_ids: list[str] = []
    region_start_sec: float | None = None
    region_end_sec: float | None = None
    project_id: str = ""


class IntentApplyRequest(IntentRequest):
    """R7：意图 → 补丁 → dry-run（可选执行）。继承 `/intent` 的入参，多三项。"""
    apply: bool = False
    answers: dict[str, str] = {}
    intent_raw: dict[str, Any] = {}


class ProceedRequest(BaseModel):
    session_id: str
    persona_id: str = ""
    category_plugin_id: str = ""
    extra_params: dict[str, Any] = {}
    # 修复：管线完成后把 final_timeline 保存到该项目（防止内存结果 60s 清理后丢失）
    project_id: str = ""
    # beat-sync 开关与 BPM——EditAgent 读取 cut_on_beat/bpm（卡点剪辑），
    # 但此前全仓无任何写入方，功能永久禁用
    cut_on_beat: bool = False
    beat_bpm: float = Field(default=0, ge=0, le=300)


# ── API 端点 ─────────────────────────────────

@router.post("/init")
async def init_session(req: InitRequest, request: Request) -> dict:
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
    # 登记会话归属（jwt 模式下其余端点据此校验）
    from clipwright.authz import current_user_id
    _session_owners[session.get("session_id", "")] = current_user_id(request) or ""
    # 轮72：归属表上限——会话本身有 TTL，此处防长进程无限增长
    # （被淘汰的会话在 _require_session_owner 中与未知会话同语义：放行）
    if len(_session_owners) > 500:
        for _k in list(_session_owners)[: len(_session_owners) - 500]:
            _session_owners.pop(_k, None)
    return session


@router.post("/edit")
async def edit_timeline(req: EditRequest, request: Request) -> dict:
    _require_session_owner(request, req.session_id)
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


@router.post("/{session_id}/intent")
async def preview_intent(session_id: str, req: IntentRequest, request: Request) -> dict:
    """R6：把一句自然语言指令解析成**剪辑意图**并落位到具体片段（预览，不修改时间线）。

    返回：`intent`（人话总结）、`resolution`（影响的片段 + 预览清单）、
    `questions`（必须由用户拍板的问题，带具体选项）、`problems`（校验错误/警告）、
    `blocked`（有硬错误时为 True，此时不落位——幻觉操作在这里被拦住）。

    前端"意图确认卡"直接渲染本响应；确认后的执行在 R7 的 apply 通路。
    """
    _require_session_owner(request, session_id)
    from clipwright.schema.timeline import Timeline
    from clipwright.services.intent_resolver import preview_intent as _preview

    try:
        timeline = Timeline.model_validate(req.timeline) if req.timeline else Timeline(id="empty")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"时间线数据非法: {str(exc)[:120]}")
    region = ((req.region_start_sec, req.region_end_sec)
              if req.region_start_sec is not None and req.region_end_sec is not None else None)
    try:
        result = await asyncio.wait_for(
            _preview(
                req.message, timeline=timeline, session_id=session_id,
                selected_clip_ids=req.selected_clip_ids, region=region,
                project_id=req.project_id or None,
                llm=getattr(_service, "_llm", None),
            ),
            timeout=120,
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="意图解析超时，请简化指令后重试")
    except Exception as exc:
        logger.exception("意图预览失败: %s", exc)
        raise HTTPException(status_code=500, detail="意图解析失败")
    return {"session_id": session_id, **result}


@router.post("/{session_id}/intent/apply")
async def apply_intent(session_id: str, req: IntentApplyRequest, request: Request) -> dict:
    """R7：把意图**编译成时间线补丁**，dry-run 校验后（可选）真正执行。

    与 `/intent` 的关系：`/intent` 负责"说清要干什么"（只读预览），本端点负责"干"。
    `apply=false`（默认）时返回补丁 + dry-run 报告，**不修改时间线**；
    `apply=true` 时只有 dry-run 无硬错误才执行，并在响应里回传新的 `timeline`。

    `answers` 是用户在确认卡上做的选择（`{问题码: 选项值}`），会回填进意图；
    `intent_raw` 可传入 `/intent` 已解析过的意图，保证"执行的就是用户确认的那份"，
    避免同一句话被解析两次得出不同结果。

    响应：`patch`（补丁明细）、`report`（dry-run：预览文案 + 前后片段数/时长 + 问题）、
    `applied`（是否真的落了）、`blocked`（有硬错误或仍有未答问题）、`timeline`（仅 applied 时）。
    """
    _require_session_owner(request, session_id)
    from clipwright.schema.timeline import Timeline
    from clipwright.services.intent_apply import plan_validate_apply

    try:
        timeline = Timeline.model_validate(req.timeline) if req.timeline else Timeline(id="empty")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"时间线数据非法: {str(exc)[:120]}")
    region = ((req.region_start_sec, req.region_end_sec)
              if req.region_start_sec is not None and req.region_end_sec is not None else None)
    try:
        result = await asyncio.wait_for(
            plan_validate_apply(
                req.message, timeline=timeline, session_id=session_id,
                selected_clip_ids=req.selected_clip_ids, region=region,
                project_id=req.project_id or None,
                answers=req.answers, intent_raw=req.intent_raw or None,
                llm=getattr(_service, "_llm", None), apply=req.apply,
            ),
            timeout=120,
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="意图执行超时，请简化指令后重试")
    except Exception as exc:
        logger.exception("意图执行失败: %s", exc)
        raise HTTPException(status_code=500, detail="意图执行失败")
    return {"session_id": session_id, **result}


@router.post("/chat")
async def chat_message(req: ChatRequest, request: Request) -> dict:
    _require_session_owner(request, req.session_id)
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
async def chat_stream(session_id: str, message: str = Form(...), request: Request = None):
    _require_session_owner(request, session_id)
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
    request: Request,
    session_id: str,
    file: UploadFile = File(...),
):
    """上传参考文件（图片/文档等）。"""
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id required")
    _require_session_owner(request, session_id)

    import tempfile
    import os
    suffix = os.path.splitext(file.filename or "file")[1] or ".bin"
    # 类型与大小限制（multipart 豁免全局 20MB 上限 → 此前完全无界）
    if suffix.lower() not in (".txt", ".md", ".pdf", ".docx", ".png", ".jpg",
                              ".jpeg", ".webp", ".gif", ".srt"):
        raise HTTPException(status_code=415, detail=f"不支持的文件类型: {suffix}")
    content = await file.read()
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="文件过大（上限 20MB）")
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
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
async def get_session(session_id: str, request: Request) -> dict:
    _require_session_owner(request, session_id)
    """获取会话完整状态。"""
    session = await asyncio.to_thread(_service.get_session, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
    return session


@router.get("/plan/{session_id}")
async def get_plan(session_id: str, request: Request) -> dict:
    _require_session_owner(request, session_id)
    """获取规划书。"""
    plan = await asyncio.to_thread(_service.get_plan, session_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found or not yet generated")
    return plan


def _opt_bool(value) -> bool | None:
    """user_inputs 里的开关可能是 bool / "true" / "1"，统一归一化（None 表示沿用工作区现值）。"""
    if value is None or isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if not text:
        return None
    return text in {"1", "true", "yes", "on", "是", "开"}


async def _proceed_as_task(*, session: dict, req: "ProceedRequest", request: Request,
                           user_inputs: dict, plan_data: dict | None,
                           pipeline_req, pipeline_id: str) -> dict:
    """agent_mode=on 的分支：需求确认 → 主 Agent 任务（PRD §6.1）。

    - 先确保工作区存在（后面的 material 步骤直接从工作区索引检索）；
    - Task 的 extra 带上 workspace_root / session_id / production_plan，执行器
      （执行器 → 老管线）就能拿到完整上下文；
    - 返回体保留 ``pipeline_id``（值 = task id），前端老代码不会立刻炸。
    """
    from clipwright.api.task import _register_owner, _schedule
    from clipwright.schema.task import TaskRequest
    from clipwright.services.agent_kernel import AgentKernel
    from clipwright.services.workspace import ensure_workspace

    ws_project_id = str(getattr(req, "project_id", "") or req.session_id)
    ws_name = str(user_inputs.get("workspace_name") or ws_project_id)
    ws, _ws_meta = ensure_workspace(
        ws_project_id,
        name=ws_name,
        auto_index=_opt_bool(user_inputs.get("workspace_auto_index")),
        vision=_opt_bool(user_inputs.get("workspace_vision")),
    )

    # 需求文档落盘（PRD §17.4 / §18.3）：brief.md + plan.md 是主 agent 每一步都要读的东西。
    # 这是**唯一**触发点（requirements 不是 delegate 阶段）——放在这里而不是阶段里。
    _docs: dict[str, str] = {}
    try:
        _docs = ws.write_docs(session.get("creative_brief"), plan_data)
    except Exception as _doc_err:  # noqa: BLE001 — 文档写失败不阻断开工，但要在日志里可见
        logger.warning("需求文档落盘失败（继续）: %s", _doc_err)

    # dry-run 预览（PRD closeout B2）：用户勾了"只预览"就必须只预览。
    # 之前它只落在 `pipeline_req.dry_run` 这个随老管线一起失效的字段上，
    # Task 路径完全看不到 → 勾了预览照样真跑真扣钱。
    _dry_run = bool(
        getattr(pipeline_req, "dry_run", False)
        or (pipeline_req.extra_params or {}).get("dry_run")
        or (getattr(req, "extra_params", None) or {}).get("dry_run")
    )
    extra = dict(pipeline_req.extra_params)
    extra.update({
        "workspace_root": str(ws.dirs().root),
        "workspace_project_id": ws.project_id,
        "session_id": req.session_id,
        "production_plan": plan_data,
        "creative_brief": session.get("creative_brief"),
        "docs": _docs,
        # Task 执行侧唯一读的口径（见 agent_kernel._run_tool_loop）
        "dry_run": _dry_run,
    })
    task_req = TaskRequest(
        topic=str(pipeline_req.topic or req.session_id),
        persona_id=pipeline_req.persona_id,
        category_plugin_id=pipeline_req.category_plugin_id,
        duration_sec=float(user_inputs.get("audio_duration_sec") or 0),
        plan_only=False,
        extra=extra,
    )
    kernel = AgentKernel()
    task = kernel.create(task_req)
    _register_owner(task.id, request)
    await kernel.plan(task)
    _schedule(task.id)
    await asyncio.to_thread(
        _service._persist,
        req.session_id, "pipeline_running",
        session.get("messages", []),
        session.get("creative_brief"),
        session.get("production_plan"),
        session.get("user_inputs", {}),
    )
    logger.info("proceed 双轨：session=%s → task=%s（workspace=%s）",
                req.session_id, task.id, ws.dirs().root)
    # 幂等键重新绑定到 **task.id**：进入本函数前绑的是 `pl_xxx`，但 task 路径返回
    # `task_yyy`——不重绑的话双击 proceed 会各建一个 Task、双倍扣费（审计 P1-1）。
    try:
        _idem_key = request.headers.get("Idempotency-Key", "") or f"sess:{req.session_id}"
        _idem.bind(_idem_key, task.id)
    except Exception as _idem_err:  # noqa: BLE001 — 幂等登记失败不影响本次创建
        logger.warning("proceed 幂等键重绑失败（task=%s）: %s", task.id, _idem_err)
    return {
        "session_id": req.session_id,
        # 兼容字段：老前端读 pipeline_id
        "pipeline_id": task.id,
        "task_id": task.id,
        "status": "pipeline_started",
        "mode": "task",
        "workspace_root": str(ws.dirs().root),
        "plan": task.plan.model_dump(mode="json") if task.plan else None,
    }


@router.post("/proceed")
async def proceed_to_pipeline(req: ProceedRequest, request: Request) -> dict:
    _require_session_owner(request, req.session_id)
    """确认规划书 → 启动管线，返回 pipeline_id 供前端追踪（SSE + result）。"""
    session = await asyncio.to_thread(_service.get_session, req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    plan_data = session.get("production_plan")
    if not plan_data:
        raise HTTPException(status_code=400, detail="Plan not ready")

    import uuid
    from clipwright.authz import current_user_id
    from clipwright.schema.pipeline import PipelineRequest
    from clipwright.services.trace import create_trace
    from clipwright.api.pipeline import (
        _pipeline_tasks, _running_pipelines, pipeline_timeout_sec,
    )

    # proceed 与 /run-async 同权——预算熔断检查（原路径完全绕过预算）
    from clipwright.services.budget import check_budget
    allowed, used = await check_budget()
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"本月 LLM token 预算已耗尽（已用 {used}），无法启动管线",
        )

    uid = current_user_id(request)

    # 轮70（D10）：proceed 幂等——双击/网络重试命中在跑的管线时返回同一 pipeline_id，
    # 不再起第二条管线（旧实现每次调用都新建 pipeline_id）
    _idem_key = request.headers.get("Idempotency-Key", "") or f"sess:{req.session_id}"
    _existing = _proceed_idem_hit(_idem_key, _running_pipelines, _pipeline_tasks)
    if _existing:
        logger.info("proceed 幂等命中: %s → %s", _idem_key, _existing)
        return {
            "session_id": req.session_id, "pipeline_id": _existing,
            "status": "pipeline_started", "idempotent": True,
        }

    # 预先生成 pipeline_id 并建立 trace，使前端可立即订阅 SSE / 轮询 result
    pipeline_id = f"pl_{uuid.uuid4().hex[:12]}"
    _idem.bind(_idem_key, pipeline_id)
    create_trace(pipeline_id)

    user_inputs = session.get("user_inputs", {})
    # 长视频管线耗时随配音时长增长（逐场景素材处理），动态调高超时避免误超时。
    # 前后端共用统一公式（audio×6 / scene×360 / min 1800），与前端 ReviewPanel 对齐。
    _audio_dur = float(user_inputs.get("audio_duration_sec", 0) or 0)
    _scene_count = int((plan_data or {}).get("scene_count", 0) or 0)
    # 规划书未给出场景数时按音频时长估算（约每 15 秒一个场景）兜底，保证场景余量不失效。
    if _scene_count <= 0 and _audio_dur > 0:
        _scene_count = int(_audio_dur / 15)
    _pipeline_timeout = pipeline_timeout_sec(_audio_dur, _scene_count)
    # 接通 animation_intents 通道——需求会话简报中的动画意图此前从未
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
            # subtitle_enabled 不再硬编码 True（客户端/会话可关）
            "subtitle_enabled": bool(user_inputs.get("subtitle_enabled", True)),
            "voice_id": user_inputs.get("voice_id", ""),
            "dub_segments": user_inputs.get("dub_segments", []),
            # 用户选择的素材源透传（旧实现遗漏 → 永远检索全部源/占位）
            "material_source_ids": user_inputs.get("material_source_ids", []),
            # animation_intents 通道补上缺失的插入行（C2 修复的遗留缺口）
            "animation_intents": animation_intents,
            "creative_brief": session.get("creative_brief"),
            "production_plan": session.get("production_plan"),
            # V58：管线带上会话 id，结构阶段的规划偏离才能写回本会话的规划书
            "session_id": req.session_id,
            "pipeline_timeout_sec": _pipeline_timeout,
            # beat-sync 透传（用户 extra_params 显式值优先）
            **({"cut_on_beat": True, "bpm": float(req.beat_bpm)}
               if req.cut_on_beat and req.beat_bpm > 0 else {}),
            **req.extra_params,
        },
        # dry-run 预览模式（仅生成粗剪时间线，跳过动画/音频/质检）
        dry_run=bool(req.extra_params.get("dry_run", False)),
        use_v2=True,
    )
    # ── 单轨（PRD master-agent-prd-2026-10-26 §4.1 P2）──
    # 创作只有一条路：主 Agent（Task）。老 6 阶段管线的执行引擎已在 M3 删除。
    # M3（modern-agent PRD §4.1/§5.4）：创作只有一条路——主 Agent（Task）。
    # 老 6 阶段管线的执行引擎已物理删除，所以这里既没有"agent_mode=off 就走老管线"的
    # 分支，也不会因为 agent_mode 忘记配而给出一个"当前没有可用创作通道"的 409：
    # Task 恒可用，缺的只是模型凭据（那条会在 Kernel 里如实失败，而不是在这里假装）。
    return await _proceed_as_task(
        session=session, req=req, request=request,
        user_inputs=user_inputs, plan_data=plan_data,
        pipeline_req=pipeline_req, pipeline_id=pipeline_id,
    )
