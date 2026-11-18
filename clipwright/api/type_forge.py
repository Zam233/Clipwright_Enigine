"""TypeForge API — 对话式视频类型创建。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from clipwright.services.type_forge import TypeForge

router = APIRouter(prefix="/api/type-forge/chat", tags=["type-forge"])
_forge = TypeForge()


class StartRequest(BaseModel):
    context: dict[str, Any] = Field(default_factory=dict)


class Attachment(BaseModel):
    type: str = "file"
    name: str = ""
    mime: str = ""
    data_url: str = ""


class MessageRequest(BaseModel):
    session_id: str
    message: str
    attachments: list[Attachment] = Field(default_factory=list)


class CommitRequest(BaseModel):
    session_id: str
    type_id: str = ""
    definition: dict[str, Any] | None = None


@router.post("/start")
async def start(req: StartRequest) -> dict[str, Any]:
    return await _forge.start()


@router.get("/sessions")
async def sessions() -> dict[str, Any]:
    items = _forge.list_sessions()
    return {"sessions": items, "total": len(items)}


@router.get("/sessions/{session_id}")
async def detail(session_id: str) -> dict[str, Any]:
    data = _forge.detail(session_id)
    if data is None:
        raise HTTPException(status_code=404, detail="TypeForge session not found")
    return data


@router.delete("/sessions/{session_id}")
async def delete(session_id: str) -> dict[str, Any]:
    if not _forge.get(session_id):
        raise HTTPException(status_code=404, detail="TypeForge session not found")
    _forge._sessions.pop(session_id, None)
    path = _forge.SESSIONS_DIR / f"{session_id}.json"
    path.unlink(missing_ok=True)
    return {"deleted": True, "session_id": session_id}


@router.post("/message")
async def message(req: MessageRequest) -> dict[str, Any]:
    try:
        return await _forge.message(req.session_id, req.message, [a.model_dump() for a in req.attachments])
    except KeyError:
        raise HTTPException(status_code=404, detail="TypeForge session not found")


@router.post("/commit")
async def commit(req: CommitRequest) -> dict[str, Any]:
    try:
        return await _forge.commit(req.session_id, req.type_id, req.definition)
    except KeyError:
        raise HTTPException(status_code=404, detail="TypeForge session not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
