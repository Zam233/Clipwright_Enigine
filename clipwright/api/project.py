"""项目管理 API — 保存/加载 Pipeline 状态。"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException

from clipwright.services.project_manager import ProjectManager

router = APIRouter(prefix="/api/project", tags=["project"])
_manager = ProjectManager()


@router.post("/save")
async def save_project(
    pipeline_id: str,
    state: dict[str, Any],
    name: str = "",
) -> dict:
    """保存 Pipeline 状态。"""
    project_id = await _manager.save(pipeline_id, state, name)
    return {"project_id": project_id, "status": "saved"}


@router.get("/load/{project_id}")
async def load_project(project_id: str) -> dict:
    """加载 Pipeline 状态。"""
    state = await _manager.load(project_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    return {"project_id": project_id, "state": state}


@router.get("/list")
async def list_projects() -> list[dict[str, Any]]:
    """列出所有已保存的项目。"""
    return await _manager.list_projects()


@router.delete("/{project_id}")
async def delete_project(project_id: str) -> dict:
    """删除项目。"""
    ok = await _manager.delete(project_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    return {"status": "deleted", "project_id": project_id}
