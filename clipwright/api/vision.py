"""视觉识别 API — 识图 + 自动素材入库。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.params import Body

from clipwright.material import JsonCatalogSource, MaterialRegistry
from clipwright.schema.material import MaterialAsset, MaterialType
from clipwright.services.vision import VisionService

router = APIRouter(prefix="/api/vision", tags=["vision"])
_service = VisionService()


@router.post("/analyze")
async def analyze_image(image_path: str = Body(...)) -> dict:
    """分析图片/视频内容，返回自动识别的标签和描述。"""
    result = await _service.analyze_image(image_path)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/import")
async def import_image(
    image_path: str = Body(...),
    catalog_id: str = Body(default="vision_catalog"),
    media_type: str = Body(default="image"),
) -> dict:
    """识别图片内容 → 自动生成标签 → 导入素材库。

    素材加入内存中的 JsonCatalogSource，可通过 /api/material/search 搜索。
    """
    # 1. 分析图片
    analysis = await _service.analyze_image(image_path)
    if "error" in analysis:
        raise HTTPException(status_code=400, detail=analysis["error"])

    # 2. 构建素材条目
    from pathlib import Path
    p = Path(image_path)
    asset = MaterialAsset(
        id=f"vision_{p.stem}",
        title=analysis.get("description", p.stem),
        type=MaterialType(media_type),
        local_path=image_path,
        tags=analysis.get("tags", []),
        source=f"vision_{catalog_id}",
        metadata={
            "model": analysis.get("model", ""),
            "labels": analysis.get("labels", []),
            "width": analysis.get("width", 0),
            "height": analysis.get("height", 0),
        },
    )

    # 3. 注册或追加到素材库
    existing = MaterialRegistry.get(catalog_id)
    if existing and isinstance(existing, JsonCatalogSource):
        # 追加到已有目录
        existing.add_asset(asset)
    else:
        # 创建新目录
        source = JsonCatalogSource(
            source_id=catalog_id,
            catalog_path="",  # 内存模式，不写文件
            source_name=f"视觉识别 {catalog_id}",
        )
        source.add_asset(asset)
        source._assets = [asset]
        MaterialRegistry.register(source)

    return {
        "asset": asset.model_dump(mode="json"),
        "analysis": analysis,
        "added_to": catalog_id,
    }
