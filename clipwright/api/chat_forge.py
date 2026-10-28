"""ChatForge API — 对话式 Persona 构建端点。"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from clipwright.schema.persona import PersonaManifest
from clipwright.services.chat_forge import ChatForge, ChatForgeAttachmentError

router = APIRouter(prefix="/api/persona/forge/chat", tags=["chat-forge"])

_forge = ChatForge()


class ChatStartRequest(BaseModel):
    persona_id: str = ""
    #: 会话用途：persona（磨人设，默认）| mv_prompt（磨视频提示词，PRD §II.12）
    scope: str = "persona"
    #: 开屏上下文（歌曲/类型/已填提示词/素材摘要），随会话保存
    context: dict[str, Any] = Field(default_factory=dict)


class ChatAttachment(BaseModel):
    """一条附件（图片/文件）。图片用 data URL 传，服务端可选做视觉分析。"""
    type: str = Field(description="image / file")
    name: str = ""
    mime: str = ""
    data_url: str = Field(default="", description="data:<mime>;base64,<payload>")


class ChatMessageRequest(BaseModel):
    session_id: str
    message: str
    persona_id: str = ""
    attachments: list[ChatAttachment] = Field(default_factory=list)


class ChatKnowledgeRequest(BaseModel):
    session_id: str
    content: str
    source: str = "user_upload"


class ChatKnowledgeUpdateRequest(BaseModel):
    content: Optional[str] = None
    source: Optional[str] = None


class ChatCommitRequest(BaseModel):
    session_id: str
    persona_id: str = ""
    persona_name: str = ""


class PromptMessage(BaseModel):
    """蒸馏入参的一条消息（前端可直接传本地草稿）。"""

    role: str = "user"
    content: str = ""


class CommitPromptRequest(BaseModel):
    """把对话蒸馏成视频提示词（F-05 的 /commit-prompt）。"""

    messages: list[PromptMessage] = Field(default_factory=list)
    session_id: str = ""
    max_chars: int = 4000


def _owner_of(request: Request) -> str:
    """当前用户 id（jwt 模式）；off 模式返回空串。"""
    try:
        from clipwright.authz import current_user_id

        return current_user_id(request) or ""
    except Exception:
        return ""


@router.post("/start")
async def chat_start(req: ChatStartRequest) -> dict[str, Any]:
    """开始新的对话会话（``scope=mv_prompt`` 时用于磨视频提示词）。"""
    result = await _forge.start(req.persona_id)
    if isinstance(result, dict):
        result["scope"] = req.scope
        result["context"] = dict(req.context or {})
    return result


@router.post("/commit-prompt")
async def chat_commit_prompt(req: CommitPromptRequest) -> dict[str, Any]:
    """把对话蒸馏成视频提示词：``{prompt, avoid, rationale, degraded}``。

    有 LLM 走结构化蒸馏；失败或未配置时退规则蒸馏并标 ``degraded=True``——
    不返回空提示词，也不假装成功。
    """
    from clipwright.services.prompt_distill import MAX_PROMPT_CHARS, distill_with_llm

    messages = [message.model_dump() for message in req.messages]
    llm = None
    try:  # 延迟构造：无 LLM 配置的环境直接走规则蒸馏
        from clipwright.services.llm import LLMService

        llm = LLMService()
    except Exception:  # noqa: BLE001
        llm = None

    draft = await distill_with_llm(messages, llm=llm)
    limit = max(200, min(int(req.max_chars or MAX_PROMPT_CHARS), MAX_PROMPT_CHARS))
    return {
        "prompt": draft.prompt[:limit],
        "avoid": draft.avoid,
        "rationale": draft.rationale,
        "degraded": draft.degraded,
        "source_messages": draft.source_messages,
        "session_id": req.session_id,
    }


@router.get("/sessions")
async def chat_sessions(request: Request, scope: str = "mine") -> dict[str, Any]:
    """列出会话（历史欠账：原先只能按 id 查单条，没有列表）。

    `scope=mine`（默认）只返回当前用户的会话；`scope=all` 返回全部
    （off 鉴权模式下没有用户概念，两者等价）。
    """
    owner = _owner_of(request)
    sessions = _forge.list_sessions(owner_id=owner, include_all=(scope == "all" or not owner))
    return {"sessions": sessions, "total": len(sessions)}


@router.delete("/sessions/{session_id}")
async def chat_delete_session(session_id: str) -> dict[str, Any]:
    """删除会话（内存 + 落盘文件）。"""
    if not _forge.delete_session(session_id):
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
    return {"deleted": True, "session_id": session_id}


@router.post("/message")
async def chat_message(req: ChatMessageRequest) -> dict[str, Any]:
    """发送对话消息（可带图片/文件附件）。"""
    try:
        return await _forge.message(
            session_id=req.session_id,
            user_message=req.message,
            persona_id=req.persona_id,
            attachments=[a.model_dump() for a in req.attachments] or None,
        )
    except ChatForgeAttachmentError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Session not found: {req.session_id}")


@router.post("/message/stream")
async def chat_message_stream(req: ChatMessageRequest):
    """流式发送对话消息（SSE）——逐块 `delta`，末尾一个 `done`。

    历史欠账：会话此前只能等模型产出完整 JSON 再渲染，长回复时界面长时间空白。
    事件类型：`{"type":"delta","text":...}` / `{"type":"done",...完整状态}` /
    `{"type":"error","detail":...,"partial":...}`（上游中断时保留已产出的文本）。

    注意：这是 **POST 的 SSE**（消息体可能很大、还可能带附件），
    因此前端用 fetch + ReadableStream 读，而不是 EventSource。
    """
    import json as _json

    async def _event_stream():
        try:
            async for event in _forge.message_stream(
                session_id=req.session_id,
                user_message=req.message,
                persona_id=req.persona_id,
                attachments=[a.model_dump() for a in req.attachments] or None,
            ):
                yield f"data: {_json.dumps(event, ensure_ascii=False)}\n\n"
        except ChatForgeAttachmentError as e:
            yield f"data: {_json.dumps({'type': 'error', 'detail': str(e)}, ensure_ascii=False)}\n\n"
        except KeyError:
            yield f"data: {_json.dumps({'type': 'error', 'detail': f'Session not found: {req.session_id}'}, ensure_ascii=False)}\n\n"
        except Exception as e:  # noqa: BLE001 - 流已开始，只能以事件形式报错
            yield f"data: {_json.dumps({'type': 'error', 'detail': str(e)[:200]}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/knowledge")
async def chat_add_knowledge(req: ChatKnowledgeRequest) -> dict[str, Any]:
    """添加上下文知识库内容（超长按 H1 自动分段）。"""
    try:
        return await _forge.add_knowledge(
            session_id=req.session_id,
            content=req.content,
            source=req.source,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Session not found: {req.session_id}")


@router.get("/knowledge/{session_id}")
async def chat_list_knowledge(session_id: str) -> dict[str, Any]:
    """列出会话知识库条目（含 id/长度/预览，供前端编辑与删除）。"""
    items = _forge.list_knowledge(session_id)
    if items is None:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
    return {"items": items, "total": len(items)}


@router.get("/knowledge/{session_id}/{kb_id}")
async def chat_get_knowledge(session_id: str, kb_id: str) -> dict[str, Any]:
    """读取单条知识全文（编辑前）。"""
    item = _forge.get_knowledge(session_id, kb_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Knowledge not found: {kb_id}")
    return item


@router.put("/knowledge/{session_id}/{kb_id}")
async def chat_update_knowledge(
    session_id: str, kb_id: str, req: ChatKnowledgeUpdateRequest,
) -> dict[str, Any]:
    """编辑一条知识。只改存储的上下文，**不重跑 LLM**（重分析需用户显式触发）。"""
    if req.content is None and req.source is None:
        raise HTTPException(status_code=400, detail="content 与 source 至少要给一个")
    item = _forge.update_knowledge(session_id, kb_id, content=req.content, source=req.source)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Knowledge not found: {kb_id}")
    return item


@router.delete("/knowledge/{session_id}/{kb_id}")
async def chat_delete_knowledge(session_id: str, kb_id: str) -> dict[str, Any]:
    """删除一条知识。"""
    if not _forge.delete_knowledge(session_id, kb_id):
        raise HTTPException(status_code=404, detail=f"Knowledge not found: {kb_id}")
    return {"deleted": True, "id": kb_id}


@router.delete("/knowledge/{session_id}")
async def chat_clear_knowledge(session_id: str) -> dict[str, Any]:
    """清空会话知识库。"""
    if _forge.get_state(session_id) is None:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
    removed = _forge.clear_knowledge(session_id)
    return {"deleted": removed}


@router.post("/commit", response_model=PersonaManifest)
async def chat_commit(req: ChatCommitRequest, request: Request) -> PersonaManifest:
    """保存当前 Persona 草稿为正式配置。"""
    try:
        manifest = await _forge.commit(
            session_id=req.session_id,
            persona_id=req.persona_id,
            persona_name=req.persona_name,
        )
        # 轮 80（红线）：落盘产物标记归属，否则 jwt 模式下创建者自己读不到
        from clipwright.authz import current_user_id
        uid = current_user_id(request)
        if uid and not manifest.owner_id:
            manifest.owner_id = uid
            from clipwright.persona.repository import PersonaRepository
            PersonaRepository.from_settings().save_manifest(manifest)
        return manifest
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Session not found: {req.session_id}")


@router.get("/state/{session_id}")
async def chat_state(session_id: str) -> dict[str, Any]:
    """获取会话当前状态。"""
    state = _forge.get_state(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
    return state
