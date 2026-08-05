"""素材预处理 API — 查询/触发预处理任务。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from clipwright.config import logger
from clipwright.services.material_preprocessor import (
    MaterialPreprocessor, enqueue_preprocessing, get_preprocessing_status,
    preprocess_worker,
)

router = APIRouter(prefix="/api/preprocess", tags=["preprocess"])


@router.post("/start/{asset_id}")
async def start_preprocessing(asset_id: str, file_path: str = "") -> dict:
    """对指定素材启动预处理。"""
    if not file_path:
        raise HTTPException(status_code=400, detail="file_path required")
    result = await enqueue_preprocessing(asset_id, file_path)
    return result


@router.get("/status/{asset_id}")
async def get_status(asset_id: str) -> dict:
    """查询预处理状态。"""
    status = get_preprocessing_status(asset_id)
    if not status:
        raise HTTPException(status_code=404, detail=f"Task {asset_id} not found")
    return status


@router.get("/tasks")
async def list_tasks() -> list[dict]:
    """列出所有预处理任务。"""
    return [t.to_dict() for t in MaterialPreprocessor.list_tasks()]
