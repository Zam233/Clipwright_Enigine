"""项目时间线版本管理 API（G1：接线 VersionManager）。

- 版本以项目为会话（versions/<project_id>/），文件落盘；
- 所有权校验与 project API 一致（P3-3B）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from clipwright.authz import current_user_id, enforce_owner
from clipwright.services.project_manager import ProjectManager
from clipwright.services.versioning import VersionManager

router = APIRouter(prefix="/api/project", tags=["project-versions"])
_manager = ProjectManager()


def _owned_project(request: Request, project_id: str) -> None:
    """加载项目并校验所有权（不存在/无权 → 404/403）。"""
    try:
        data = _manager.load(project_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if data is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    enforce_owner(request, data.get("owner_id"), "项目")


def _vm(project_id: str) -> VersionManager:
    return VersionManager(project_id)


class SnapshotRequest(BaseModel):
    label: str = ""


@router.get("/{project_id}/versions")
async def list_versions(project_id: str, request: Request) -> list[dict[str, Any]]:
    """列出项目的全部时间线版本（含 is_current 标记）。"""
    _owned_project(request, project_id)
    return _vm(project_id).get_version_list()


@router.post("/{project_id}/versions")
async def create_snapshot(project_id: str, req: SnapshotRequest, request: Request) -> dict[str, Any]:
    """把项目当前 timeline 存为一个版本快照（G1）。"""
    _owned_project(request, project_id)
    data = _manager.load(project_id)
    timeline = (data or {}).get("timeline")
    if not timeline:
        raise HTTPException(status_code=400, detail="项目还没有时间线内容")
    version_id = _vm(project_id).snapshot(timeline, label=req.label or "")
    # P5-B5: 审计
    from clipwright import audit
    audit.record("project_version_create", current_user_id(request), {
        "project_id": project_id, "version_id": version_id, "label": req.label or "",
    })
    return {"version_id": version_id, "count": len(_vm(project_id).get_version_list())}


@router.post("/{project_id}/versions/{position}/restore")
async def restore_version(project_id: str, position: int, request: Request) -> dict[str, Any]:
    """恢复指定版本：返回快照 timeline 并写回项目（G1）。"""
    _owned_project(request, project_id)
    vm = _vm(project_id)
    version = vm.goto(position)
    if version is None or "data" not in version:
        raise HTTPException(status_code=404, detail=f"版本 {position} 不存在")
    timeline = version["data"]
    try:
        _manager.save(project_id, {"timeline": timeline})
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    from clipwright import audit
    audit.record("project_version_restore", current_user_id(request), {
        "project_id": project_id, "position": position,
    })
    return {"version_id": version.get("meta", {}).get("version_id", ""), "timeline": timeline}


@router.delete("/{project_id}/versions")
async def clear_versions(project_id: str, request: Request) -> dict[str, Any]:
    """清空该项目的全部版本快照（G1）。"""
    _owned_project(request, project_id)
    vm = _vm(project_id)
    vm.delete_all()
    return {"deleted": True}
