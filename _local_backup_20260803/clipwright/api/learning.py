"""自适应 Persona + 版本管理 API。"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException

from clipwright.config import logger
from clipwright.services.persona_learner import get_learner
from clipwright.services.versioning import VersionManager, EditHistory

router = APIRouter(prefix="/api/learn", tags=["learning"])

# 全局编辑历史
_edit_history = EditHistory()


# ── Persona 学习 ──

@router.post("/persona/{persona_id}/record")
async def record_edit(persona_id: str, action: str, params: dict[str, Any] = {}) -> dict:
    """记录编辑操作并学习偏好。"""
    learner = get_learner(persona_id)
    learner.record_edit(action, params)
    return {"status": "recorded", "persona_id": persona_id, "edit_count": learner.to_dict()["edit_count"]}


@router.get("/persona/{persona_id}/preferences")
async def get_preferences(persona_id: str) -> dict:
    """获取学习到的 Persona 偏好。"""
    learner = get_learner(persona_id)
    return {"persona_id": persona_id, "preferences": learner.get_persona_updates(), "edit_count": learner.to_dict()["edit_count"]}


@router.get("/persona/{persona_id}/history")
async def get_learning_history(persona_id: str) -> dict:
    """获取编辑学习历史。"""
    learner = get_learner(persona_id)
    return learner.to_dict()


# ── 版本管理 ──

@router.post("/version/{session_id}/snapshot")
async def create_snapshot(session_id: str, data: dict[str, Any], label: str = "") -> dict:
    """创建版本快照。"""
    vm = VersionManager(session_id)
    version_id = vm.snapshot(data, label)
    return {"version_id": version_id, "position": vm._position, "label": label}


@router.post("/version/{session_id}/undo")
async def undo_version(session_id: str) -> dict:
    """回退到上一个版本。"""
    vm = VersionManager(session_id)
    data = vm.undo()
    if data is None:
        raise HTTPException(status_code=400, detail="Already at oldest version")
    return {"status": "undone", "position": vm._position, "data": data.get("data")}


@router.post("/version/{session_id}/redo")
async def redo_version(session_id: str) -> dict:
    """前进到下一个版本。"""
    vm = VersionManager(session_id)
    data = vm.redo()
    if data is None:
        raise HTTPException(status_code=400, detail="Already at newest version")
    return {"status": "redone", "position": vm._position, "data": data.get("data")}


@router.get("/version/{session_id}/list")
async def list_versions(session_id: str) -> dict:
    """列出所有版本。"""
    vm = VersionManager(session_id)
    return {"versions": vm.get_version_list(), "position": vm._position}


@router.post("/version/{session_id}/goto/{position}")
async def goto_version(session_id: str, position: int) -> dict:
    """跳转到指定版本。"""
    vm = VersionManager(session_id)
    data = vm.goto(position)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Version position {position} not found")
    return {"status": "loaded", "position": position, "data": data.get("data")}


@router.get("/version/{session_id}/diff")
async def diff_versions(session_id: str, pos_a: int = 0, pos_b: int = 0) -> dict:
    """对比两个版本的差异。"""
    vm = VersionManager(session_id)
    return vm.diff(pos_a, pos_b)


# ── 编辑历史 ──

@router.post("/history/push")
async def push_history(action: str, state: dict[str, Any] = {}) -> dict:
    """记录编辑历史。"""
    pos = _edit_history.push(action, state)
    return {"position": pos}


@router.post("/history/undo")
async def history_undo() -> dict:
    """撤销。"""
    state = _edit_history.undo()
    if state is None:
        raise HTTPException(status_code=400, detail="Nothing to undo")
    return state


@router.post("/history/redo")
async def history_redo() -> dict:
    """重做。"""
    state = _edit_history.redo()
    if state is None:
        raise HTTPException(status_code=400, detail="Nothing to redo")
    return state


@router.get("/history")
async def get_history() -> list[dict]:
    """获取编辑历史。"""
    return _edit_history.list()
