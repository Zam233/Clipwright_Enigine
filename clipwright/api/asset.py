"""资产管理 API — 上传、列表、查看。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse

from clipwright.services.asset_manager import AssetManager

router = APIRouter(prefix="/api/asset", tags=["asset"])
_manager = AssetManager()

# 上传体积上限（防大文件 DoS）
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024  # 2GB


@router.post("/upload")
async def upload_asset(file: UploadFile) -> dict:
    """上传媒体文件 -> 导入 + 格式检测 + 缩略图。"""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    # 保存到临时位置（mkdtemp 避免 mktemp 竞态；分块读取并限制体积）
    import shutil
    import tempfile
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

        info = await _manager.import_file(tmp)
        if info.error:
            raise HTTPException(status_code=400, detail=info.error)
        return info.to_dict()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@router.get("/list")
async def list_assets() -> list[dict]:
    """列出所有已导入的素材。"""
    return [a.to_dict() for a in await _manager.list_assets()]


@router.get("/{asset_id}")
async def get_asset(asset_id: str) -> dict:
    """获取素材信息。"""
    asset = _manager.get(asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail=f"Asset {asset_id} not found")
    return asset.to_dict()


@router.get("/{asset_id}/file")
async def get_asset_file(asset_id: str):
    """获取素材文件。"""
    asset = _manager.get(asset_id)
    if asset is None or not Path(asset.file_path).exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(asset.file_path)


@router.get("/{asset_id}/thumbnail")
async def get_asset_thumbnail(asset_id: str):
    """获取素材缩略图。"""
    asset = _manager.get(asset_id)
    if asset is None or not asset.thumbnail_path or not Path(asset.thumbnail_path).exists():
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    return FileResponse(asset.thumbnail_path)
