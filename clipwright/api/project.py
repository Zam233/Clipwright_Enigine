"""项目管理 API — RESTful CRUD matching frontend Project contract."""

from __future__ import annotations

import threading
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from clipwright.config import logger
from clipwright.services.project_manager import ProjectManager

router = APIRouter(prefix="/api/project", tags=["project"])
_manager = ProjectManager()


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
async def create_project(req: ProjectCreateRequest) -> dict[str, Any]:
    """Create a new project (backend assigns id)."""
    data = _manager.create(
        name=req.name,
        timeline=req.timeline,
        persona_id=req.persona_id,
        plugin_id=req.plugin_id,
        folder=req.folder,
        tags=req.tags,
    )
    return data


@router.get("")
async def list_projects(
    folder: str | None = Query(None),
    tag: str | None = Query(None),
) -> list[dict[str, Any]]:
    """List all projects, optionally filtered by folder/tag."""
    return _manager.list_projects(folder=folder, tag=tag)


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
async def get_project(project_id: str) -> dict[str, Any]:
    """Load a project by id."""
    try:
        data = _manager.load(project_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if data is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    return data


@router.put("/{project_id}")
async def update_project(project_id: str, req: ProjectUpdateRequest) -> dict[str, Any]:
    """Update an existing project.

    When the request includes a ``timeline`` (or any content change), a
    background thread regenerates the thumbnail asynchronously so the
    PUT response is never delayed by FFmpeg.
    """
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
async def delete_project(project_id: str) -> dict[str, Any]:
    """Delete a project."""
    try:
        ok = _manager.delete(project_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    return {"status": "deleted", "id": project_id}


@router.post("/{project_id}/duplicate")
async def duplicate_project(project_id: str) -> dict[str, Any]:
    """Deep-copy a project with a new id."""
    try:
        return _manager.duplicate(project_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch("/{project_id}/rename")
async def rename_project(project_id: str, req: RenameRequest) -> dict[str, Any]:
    """Rename a project."""
    try:
        return _manager.rename(project_id, req.name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch("/{project_id}/folder")
async def set_folder(project_id: str, req: FolderRequest) -> dict[str, Any]:
    """Set project folder."""
    try:
        return _manager.set_folder(project_id, req.folder)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{project_id}/tags")
async def add_tag(project_id: str, req: TagRequest) -> dict[str, Any]:
    """Add a tag to a project."""
    try:
        return _manager.add_tag(project_id, req.tag)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{project_id}/tags/{tag}")
async def remove_tag(project_id: str, tag: str) -> dict[str, Any]:
    """Remove a tag from a project."""
    try:
        return _manager.remove_tag(project_id, tag)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{project_id}/thumbnail")
async def get_thumbnail(project_id: str, force: bool = Query(False)):
    """Get project thumbnail image.

    When *force* is ``True`` the thumbnail is regenerated even if it is
    already up-to-date (stale-aware check is skipped).
    """
    import os
    from pathlib import Path
    from fastapi.responses import FileResponse

    try:
        data = _manager.load(project_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if data is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")

    thumb_path = data.get("thumbnail")
    if thumb_path and Path(thumb_path).exists() and not force:
        return FileResponse(thumb_path, media_type="image/jpeg")

    # Try to generate (or regenerate when force=True)
    result = _manager.regenerate_thumbnail(project_id, force=force)
    if result and Path(result).exists():
        return FileResponse(result, media_type="image/jpeg")

    raise HTTPException(status_code=404, detail="No thumbnail available")
