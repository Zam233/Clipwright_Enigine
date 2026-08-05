"""对话式视频编辑 API — 通过自然语言对话修改已生成的视频。"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException

from clipwright.config import logger
from clipwright.schema.timeline import Timeline
from clipwright.services.video_editor import VideoEditor

router = APIRouter(prefix="/api/edit", tags=["video-editor"])
_editor = VideoEditor()


@router.post("/session/create")
async def create_session(
    timeline: Optional[Timeline] = None,
    pipeline_id: str = "",
    video_path: str = "",
) -> dict:
    """创建编辑会话，关联已有的时间线/管线/视频。"""
    import uuid
    session_id = f"edit_{uuid.uuid4().hex[:12]}"
    session = _editor.create_session(session_id, timeline)
    session.pipeline_id = pipeline_id
    session.current_video_path = video_path
    logger.info("编辑会话已创建: %s", session_id)
    return {"session_id": session_id, "status": "created"}


@router.post("/session/{session_id}/chat")
async def chat_edit(session_id: str, message: str) -> dict:
    """发送自然语言编辑请求。"""
    session = _editor.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    result = await _editor.process_edit(session_id, message)
    return result


@router.get("/session/{session_id}")
async def get_session(session_id: str) -> dict:
    """获取编辑会话状态。"""
    session = _editor.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return session.to_dict()


@router.get("/session/{session_id}/history")
async def get_history(session_id: str) -> list[dict]:
    """获取编辑历史。"""
    session = _editor.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return session.history


@router.post("/session/{session_id}/timeline")
async def update_timeline(session_id: str, timeline: Timeline) -> dict:
    """更新会话的时间线。"""
    session = _editor.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    session.timeline = timeline
    return {"status": "updated", "session_id": session_id}


@router.get("/capabilities")
async def get_capabilities() -> list[dict]:
    """列出支持的编辑能力。"""
    from clipwright.services.video_editor import _EDIT_CAPABILITIES
    return _EDIT_CAPABILITIES
