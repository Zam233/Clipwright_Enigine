"""资产管理 API — 上传、列表、查看、删除。支持按项目隔离。"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from clipwright.services.asset_manager import AssetManager

router = APIRouter(prefix="/api/asset", tags=["asset"])

# 每个项目一个 AssetManager 实例（惰性创建，缓存）
_managers: dict[str, AssetManager] = {}

# 上传体积上限（防大文件 DoS）
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024  # 2GB


def _get_manager(project_id: str | None) -> AssetManager:
    """获取指定项目的 AssetManager（惰性创建）。"""
    if not project_id:
        # 全局回退
        return AssetManager()
    if project_id not in _managers:
        _managers[project_id] = AssetManager(project_id=project_id)
    return _managers[project_id]


@router.post("/upload")
async def upload_asset(file: UploadFile, project_id: str = Query("")) -> dict:
    """上传媒体文件 -> 导入 + 格式检测 + 缩略图。

    project_id: 所属项目 ID（不传则为全局共享）。
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    manager = _get_manager(project_id or None)
    ext = Path(file.filename).suffix or ".bin"
    tmp_dir = Path(tempfile.mkdtemp(prefix="asset_up_"))
    tmp = tmp_dir / f"upload{ext}"
    try:
        size = 0
        with open(tmp, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="文件过大（上限 2GB）")
                out.write(chunk)

        info = await manager.import_file(tmp)
        if info.error:
            raise HTTPException(status_code=400, detail=info.error)
        return info.to_dict()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@router.get("/list")
async def list_assets(project_id: str = Query("")) -> list[dict]:
    """列出项目的所有已导入素材。"""
    manager = _get_manager(project_id or None)
    return [a.to_dict() for a in await manager.list_assets()]


@router.get("/{asset_id}")
async def get_asset(asset_id: str, project_id: str = Query("")) -> dict:
    """获取素材信息。"""
    manager = _get_manager(project_id or None)
    asset = manager.get(asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail=f"Asset {asset_id} not found")
    return asset.to_dict()


@router.get("/{asset_id}/file")
async def get_asset_file(asset_id: str, project_id: str = Query("")):
    """获取素材文件。"""
    manager = _get_manager(project_id or None)
    asset = manager.get(asset_id)
    if asset is None or not Path(asset.file_path).exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(asset.file_path)


@router.get("/{asset_id}/thumbnail")
async def get_asset_thumbnail(asset_id: str, project_id: str = Query("")):
    """获取素材缩略图。"""
    manager = _get_manager(project_id or None)
    asset = manager.get(asset_id)
    if asset is None or not asset.thumbnail_path or not Path(asset.thumbnail_path).exists():
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    return FileResponse(asset.thumbnail_path)


@router.delete("/{asset_id}")
async def delete_asset(asset_id: str, project_id: str = Query("")) -> dict:
    """删除素材（仅删除软连接和元数据，保留原始文件）。"""
    manager = _get_manager(project_id or None)
    ok = manager.delete_asset(asset_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Asset {asset_id} not found")
    return {"status": "ok", "asset_id": asset_id}


class ImportPathRequest(BaseModel):
    path: str
    project_id: str = ""


@router.post("/import-path")
async def import_asset_by_path(req: ImportPathRequest) -> dict:
    """通过文件路径导入素材（不复制文件，创建软连接）。"""
    from pathlib import Path
    src = Path(req.path)
    if not src.exists():
        raise HTTPException(status_code=400, detail=f"文件不存在: {req.path}")
    manager = _get_manager(req.project_id or None)
    info = await manager.import_file(src)
    if info.error:
        raise HTTPException(status_code=400, detail=info.error)
    return info.to_dict()
