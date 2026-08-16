"""项目管理 API — RESTful CRUD matching frontend Project contract."""

from __future__ import annotations

import threading
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from clipwright.authz import current_user_id, enforce_owner, filter_by_owner
from clipwright.config import logger
from clipwright.services.project_manager import ProjectManager

router = APIRouter(prefix="/api/project", tags=["project"])
_manager = ProjectManager()


def _load_owned(request: Request, project_id: str) -> dict[str, Any]:
    """加载项目并校验所有权（P3-3B）。"""
    try:
        data = _manager.load(project_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if data is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    enforce_owner(request, data.get("owner_id"), "项目")
    return data


# ── Background thumbnail helper (fire-and-forget) ──

def _bg_thumbnail(project_id: str) -> None:
    """Regenerate thumbnail in a background thread.

    This is intentionally fire-and-forget: the PUT response returns
    immediately while the thumbnail is produced asynchronously.  Failures
    are logged but never surface to the caller.
    """
    try:
        _manager.regenerate_thumbnail(project_id)
    except Exception as exc:
        logger.warning("bg thumbnail failed for %s: %s", project_id, exc)


# ── Request models ──

class ProjectCreateRequest(BaseModel):
    name: str = ""
    timeline: Any = None
    persona_id: str | None = None
    plugin_id: str | None = None
    folder: str = ""
    tags: list[str] | None = None
    agent_state: Any = None


class ProjectUpdateRequest(BaseModel):
    name: str | None = None
    timeline: Any = None
    persona_id: str | None = None
    plugin_id: str | None = None
    folder: str | None = None
    tags: list[str] | None = None
    agent_state: Any = None  # 需求对话/简报/规划书/执行日志，随项目持久化


class RenameRequest(BaseModel):
    name: str


class FolderRequest(BaseModel):
    folder: str


class TagRequest(BaseModel):
    tag: str


class FolderRenameRequest(BaseModel):
    old: str
    new: str


class FolderDeleteRequest(BaseModel):
    name: str


# ── Routes ──

@router.post("")
async def create_project(req: ProjectCreateRequest, request: Request) -> dict[str, Any]:
    """Create a new project (backend assigns id; P3-3B: 记录 owner_id)。"""
    data = _manager.create(
        name=req.name,
        timeline=req.timeline,
        persona_id=req.persona_id,
        plugin_id=req.plugin_id,
        folder=req.folder,
        tags=req.tags,
        agent_state=req.agent_state,
    )
    uid = current_user_id(request)
    if uid:
        try:
            _manager.save(data["id"], {"owner_id": uid})
            data["owner_id"] = uid
        except Exception as e:
            logger.warning("owner_id 写入失败: %s", e)
    # P5-B5: 审计
    from clipwright import audit
    audit.record("project_create", uid, {"project_id": data["id"], "name": data.get("name", "")})
    return data


@router.get("")
async def list_projects(
    request: Request,
    folder: str | None = Query(None),
    tag: str | None = Query(None),
) -> list[dict[str, Any]]:
    """List all projects, optionally filtered by folder/tag（P3-3B: 按 owner 过滤）。"""
    return filter_by_owner(request, _manager.list_projects(folder=folder, tag=tag))


@router.post("/folders/rename")
async def rename_folder(req: FolderRenameRequest) -> dict[str, Any]:
    """Rename a folder label across all projects."""
    updated = _manager.rename_folder(req.old, req.new)
    return {"updated": updated}


@router.post("/folders/delete")
async def delete_folder(req: FolderDeleteRequest) -> dict[str, Any]:
    """Unfile all projects in a folder (clears folder field, never deletes projects)."""
    updated = _manager.delete_folder(req.name)
    return {"updated": updated}


@router.get("/{project_id}")
async def get_project(project_id: str, request: Request) -> dict[str, Any]:
    """Load a project by id（P3-3B: 校验所有权）。"""
    return _load_owned(request, project_id)


@router.put("/{project_id}")
async def update_project(project_id: str, req: ProjectUpdateRequest, request: Request) -> dict[str, Any]:
    """Update an existing project.

    When the request includes a ``timeline`` (or any content change), a
    background thread regenerates the thumbnail asynchronously so the
    PUT response is never delayed by FFmpeg.
    """
    _load_owned(request, project_id)  # P3-3B
    update_data = req.model_dump(exclude_unset=True)
    try:
        data = _manager.save(project_id, update_data)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Fire-and-forget background thumbnail generation
    if "timeline" in update_data:
        threading.Thread(target=_bg_thumbnail, args=(project_id,), daemon=True).start()

    return data


@router.delete("/{project_id}")
async def delete_project(project_id: str, request: Request) -> dict[str, Any]:
    """Delete a project（P3-3B: 校验所有权）。"""
    _load_owned(request, project_id)
    try:
        ok = _manager.delete(project_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    # P5-B5: 审计
    from clipwright import audit
    audit.record("project_delete", current_user_id(request), {"project_id": project_id})
    return {"status": "deleted", "id": project_id}


@router.post("/{project_id}/duplicate")
async def duplicate_project(project_id: str, request: Request) -> dict[str, Any]:
    """Deep-copy a project with a new id（P3-3B: 校验所有权，副本归属当前用户）。"""
    _load_owned(request, project_id)
    try:
        dup = _manager.duplicate(project_id)
        uid = current_user_id(request)
        if uid:
            try:
                _manager.save(dup["id"], {"owner_id": uid})
            except Exception:
                pass
        return dup
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch("/{project_id}/rename")
async def rename_project(project_id: str, req: RenameRequest, request: Request) -> dict[str, Any]:
    """Rename a project（P3-3B）。"""
    _load_owned(request, project_id)
    try:
        return _manager.rename(project_id, req.name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch("/{project_id}/folder")
async def set_folder(project_id: str, req: FolderRequest, request: Request) -> dict[str, Any]:
    """Set project folder（P3-3B）。"""
    _load_owned(request, project_id)
    try:
        return _manager.set_folder(project_id, req.folder)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{project_id}/tags")
async def add_tag(project_id: str, req: TagRequest, request: Request) -> dict[str, Any]:
    """Add a tag to a project（P3-3B）。"""
    _load_owned(request, project_id)
    try:
        return _manager.add_tag(project_id, req.tag)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{project_id}/tags/{tag}")
async def remove_tag(project_id: str, tag: str, request: Request) -> dict[str, Any]:
    """Remove a tag from a project（P3-3B）。"""
    _load_owned(request, project_id)
    try:
        return _manager.remove_tag(project_id, tag)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{project_id}/thumbnail")
async def get_thumbnail(project_id: str, request: Request, force: bool = Query(False)):
    """Get project thumbnail image.

    When *force* is ``True`` the thumbnail is regenerated even if it is
    already up-to-date (stale-aware check is skipped).
    """
    import os
    from pathlib import Path
    from fastapi.responses import FileResponse

    data = _load_owned(request, project_id)  # P3-3B

    thumb_path = data.get("thumbnail")
    if thumb_path and Path(thumb_path).exists() and not force:
        return FileResponse(thumb_path, media_type="image/jpeg")

    # Try to generate (or regenerate when force=True)
    result = _manager.regenerate_thumbnail(project_id, force=force)
    if result and Path(result).exists():
        return FileResponse(result, media_type="image/jpeg")

    raise HTTPException(status_code=404, detail="No thumbnail available")
