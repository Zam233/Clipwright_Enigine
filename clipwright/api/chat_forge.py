"""ChatForge API — 对话式 Persona 构建端点。"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from clipwright.schema.persona import PersonaManifest
from clipwright.services.chat_forge import ChatForge

router = APIRouter(prefix="/api/persona/forge/chat", tags=["chat-forge"])

_forge = ChatForge()


class ChatStartRequest(BaseModel):
    persona_id: str = ""


class ChatMessageRequest(BaseModel):
    session_id: str
    message: str
    persona_id: str = ""


class ChatKnowledgeRequest(BaseModel):
    session_id: str
    content: str
    source: str = "user_upload"


class ChatCommitRequest(BaseModel):
    session_id: str
    persona_id: str = ""
    persona_name: str = ""


@router.post("/start")
async def chat_start(req: ChatStartRequest) -> dict[str, Any]:
    """开始新的对话会话。"""
    return await _forge.start(req.persona_id)


@router.post("/message")
async def chat_message(req: ChatMessageRequest) -> dict[str, Any]:
    """发送对话消息。"""
    try:
        return await _forge.message(
            session_id=req.session_id,
            user_message=req.message,
            persona_id=req.persona_id,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Session not found: {req.session_id}")


@router.post("/knowledge")
async def chat_add_knowledge(req: ChatKnowledgeRequest) -> dict[str, Any]:
    """添加上下文知识库内容。"""
    try:
        return await _forge.add_knowledge(
            session_id=req.session_id,
            content=req.content,
            source=req.source,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Session not found: {req.session_id}")


@router.post("/commit", response_model=PersonaManifest)
async def chat_commit(req: ChatCommitRequest) -> PersonaManifest:
    """保存当前 Persona 草稿为正式配置。"""
    try:
        return await _forge.commit(
            session_id=req.session_id,
            persona_id=req.persona_id,
            persona_name=req.persona_name,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Session not found: {req.session_id}")


@router.get("/state/{session_id}")
async def chat_state(session_id: str) -> dict[str, Any]:
    """获取会话当前状态。"""
    state = _forge.get_state(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
    return state
