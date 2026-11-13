"""通用 artifact API（M6）—— 供所有视频类型/插件共用。

- `GET /api/artifacts`：按 project/task/plugin/status/role 过滤，cursor 分页；
- `GET /api/artifacts/{plugin_id}/{artifact_id}/raw`：按归属返回原始文件。

不暴露任意本地路径；文件必须仍在 `<project>/plugins/<plugin_id>/` 内。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from clipwright.services.artifact_query import (
    ArtifactNotFound,
    artifact_path,
    list_artifacts,
)

router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])


@router.get("")
async def list_artifact_api(
    project_id: str = Query(..., description="项目 id（必填）"),
    plugin_id: str = "",
    task_id: str = "",
    status: str = "",
    role: str = "",
    cursor: str = "",
    limit: int = Query(default=50, ge=1, le=100),
) -> dict:
    try:
        page = list_artifacts(
            project_id, plugin_id=plugin_id, task_id=task_id,
            status=status, role=role, cursor=cursor, limit=limit,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {
        "items": page.items,
        "next_cursor": page.next_cursor,
        "total_estimate": page.total_estimate,
    }


@router.get("/{plugin_id}/{artifact_id}/raw")
async def artifact_raw(
    plugin_id: str,
    artifact_id: str,
    project_id: str = Query(..., description="项目 id（必填）"),
) -> FileResponse:
    try:
        path, entry = artifact_path(project_id, plugin_id, artifact_id)
    except ArtifactNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    media_type = str(entry.get("mime") or "application/octet-stream")
    return FileResponse(str(path), media_type=media_type, filename=path.name)
