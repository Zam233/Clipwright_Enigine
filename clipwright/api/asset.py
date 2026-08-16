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
        # Preserve original filename (temp file is named "upload{ext}")
        info.filename = file.filename
        return info.to_dict()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@router.get("/list")
async def list_assets(project_id: str = Query("")) -> list[dict]:
    """列出项目的所有已导入素材。"""
    manager = _get_manager(project_id or None)
    result: list[dict] = []
    for a in await manager.list_assets():
        d = a.to_dict()
        if project_id:
            d["project_id"] = project_id
        result.append(d)
    return result


@router.get("/by-path")
async def get_asset_by_path(path: str = Query(...)):
    """通过本地路径返回媒体文件流（安全白名单代理）。

    仅服务 allowed_media_roots() 白名单内的路径（renders / library /
    editor_projects / projects / PluginData / persona_dir / tts_output_dir）。
    素材候选的 local_path 若在白名单外（如 json_source 自定义路径、_cache 裁剪缓存）
    → 返回 403，前端须优雅降级（预览回退占位块）。
    """
    from clipwright.security import SecurityViolation, assert_allowed_path

    try:
        target = assert_allowed_path(Path(path))
    except SecurityViolation as e:
        # 显式转 403：绕过 main.py 全局 SecurityViolation 处理（返回 400），给出明确语义
        raise HTTPException(status_code=403, detail=f"Path not allowed: {e}") from e
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Path not found")
    return FileResponse(target, media_type=_guess_media_type(target))


def _guess_media_type(path: Path) -> str:
    """按扩展名推断媒体 MIME 类型（视频/图片/音频）。"""
    return {
        ".mp4": "video/mp4",
        ".webm": "video/webm",
        ".mov": "video/quicktime",
        ".mkv": "video/x-matroska",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".m4a": "audio/mp4",
        ".aac": "audio/aac",
        ".ogg": "audio/ogg",
        ".flac": "audio/flac",
    }.get(path.suffix.lower(), "application/octet-stream")


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
    """获取素材文件（P0-1：返回前强制白名单校验）。"""
    from clipwright.security import SecurityViolation, assert_allowed_path

    manager = _get_manager(project_id or None)
    asset = manager.get(asset_id)
    if asset is None or not asset.file_path or not Path(asset.file_path).exists():
        raise HTTPException(status_code=404, detail="File not found")
    try:
        fp = assert_allowed_path(Path(asset.file_path))
    except SecurityViolation:
        raise HTTPException(status_code=403, detail="素材路径不在白名单（请重新导入）") from None
    return FileResponse(fp)


@router.get("/{asset_id}/thumbnail")
async def get_asset_thumbnail(asset_id: str, project_id: str = Query("")):
    """获取素材缩略图（P0-1：返回前强制白名单校验）。"""
    from clipwright.security import SecurityViolation, assert_allowed_path

    manager = _get_manager(project_id or None)
    asset = manager.get(asset_id)
    if asset is None or not asset.thumbnail_path or not Path(asset.thumbnail_path).exists():
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    try:
        tp = assert_allowed_path(Path(asset.thumbnail_path))
    except SecurityViolation:
        raise HTTPException(status_code=403, detail="缩略图路径不在白名单") from None
    return FileResponse(tp)


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


class ImportUrlRequest(BaseModel):
    url: str
    filename: str
    project_id: str = ""


@router.post("/import-path")
async def import_asset_by_path(req: ImportPathRequest) -> dict:
    """通过文件路径导入素材（白名单内创建软连接，白名单外安全复制）。"""
    from pathlib import Path
    src = Path(req.path)
    if not src.exists():
        raise HTTPException(status_code=400, detail=f"文件不存在: {req.path}")
    # P0-1: 拒绝点文件（.env/.git/.ssh 等敏感文件不可导入，阻断任意文件读取链入口）
    if src.name.startswith("."):
        raise HTTPException(status_code=400, detail="不允许导入隐藏文件")
    manager = _get_manager(req.project_id or None)
    info = await manager.import_file(src)
    if info.error:
        raise HTTPException(status_code=400, detail=info.error)
    return info.to_dict()


@router.post("/import-url")
async def import_asset_by_url(req: ImportUrlRequest) -> dict:
    """通过 URL 下载素材并导入到项目素材库（P0-7：SSRF 防护 + 流式大小上限）。"""
    import httpx
    from pathlib import Path

    from clipwright.security import SecurityViolation, assert_public_url

    # P0-7: 拒绝回环/私网/链路本地/云元数据地址（防 SSRF）
    try:
        assert_public_url(req.url)
    except SecurityViolation as e:
        raise HTTPException(status_code=400, detail=f"URL 不允许: {e}") from e

    ext = Path(req.filename).suffix or ".bin"
    tmp_dir = Path(tempfile.mkdtemp(prefix="asset_url_"))
    tmp = tmp_dir / f"download{ext}"
    try:
        # P0-7: 流式写盘 + 大小上限，避免整读内存
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            async with client.stream("GET", req.url) as resp:
                resp.raise_for_status()
                size = 0
                with open(tmp, "wb") as out:
                    async for chunk in resp.aiter_bytes(1024 * 1024):
                        size += len(chunk)
                        if size > MAX_UPLOAD_BYTES:
                            raise HTTPException(status_code=413, detail="文件过大（上限 2GB）")
                        out.write(chunk)

        manager = _get_manager(req.project_id or None)
        info = await manager.import_file(tmp)
        if info.error:
            raise HTTPException(status_code=400, detail=info.error)
        return info.to_dict()
    except HTTPException:
        raise
    except httpx.HTTPError as e:
        raise HTTPException(status_code=400, detail=f"下载失败: {e}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ── P9: 素材治理与合规 ─────────────────────────


@router.post("/governance/patrol")
async def patrol_assets(project_id: str = Query("")) -> dict:
    """P9: URL 失效巡检 — 检查 HTTP(S) 引用素材的可达性并标记。"""
    manager = _get_manager(project_id or None)
    return await manager.patrol_urls()


@router.post("/governance/violations")
async def detect_violations(project_id: str = Query("")) -> dict:
    """P9: 违规内容检测 — 图片走视觉模型，文本走关键词；可选第三方服务。"""
    manager = _get_manager(project_id or None)
    return await manager.detect_violations()


@router.get("/governance/summary")
async def governance_summary(project_id: str = Query("")) -> dict:
    """P9: 素材治理摘要 — 总数/去重数/使用统计/异常状态。"""
    manager = _get_manager(project_id or None)
    assets = await manager.list_assets()
    total = len(assets)
    deduped = sum(1 for a in assets if a.error == "deduplicated")
    used = sum(1 for a in assets if a.used_count > 0)
    total_uses = sum(a.used_count for a in assets)
    missing = sum(1 for a in assets if a.status == "missing")
    violated = sum(1 for a in assets if a.status == "violated")
    return {
        "total": total,
        "deduplicated": deduped,
        "used": used,
        "total_uses": total_uses,
        "missing": missing,
        "violated": violated,
    }
