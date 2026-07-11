"""素材库 API — 查询、搜索、管理素材源。"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException

from clipwright.material import MaterialRegistry
from clipwright.schema.material import MaterialAsset, MaterialSearchResult

router = APIRouter(prefix="/api/material", tags=["material"])


@router.get("/sources")
async def list_sources() -> list[dict[str, str]]:
    """列出所有已注册的素材源。"""
    return MaterialRegistry.list()


@router.post("/search", response_model=list[MaterialSearchResult])
async def search_materials(
    query: str,
    top_k: int = 10,
    sources: Optional[list[str]] = None,
) -> list[MaterialSearchResult]:
    """跨素材源搜索素材。"""
    return await MaterialRegistry.search(
        query=query,
        top_k_per_source=top_k,
        source_ids=sources,
    )


@router.get("/asset/{source_id}/{asset_id}", response_model=MaterialAsset)
async def get_asset(source_id: str, asset_id: str) -> MaterialAsset:
    """获取单个素材详情。"""
    asset = await MaterialRegistry.get_asset(source_id, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail=f"Asset {asset_id} not found in {source_id}")
    return asset
