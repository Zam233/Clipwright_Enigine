"""渲染 API — 提交、查询渲染任务。"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter

router = APIRouter(prefix="/api/render", tags=["render"])


@router.post("/start")
async def start_render(timeline_id: str, output_path: Optional[str] = None) -> dict:
    """提交渲染任务。"""
    # Phase 1 占位
    return {
        "status": "placeholder",
        "timeline_id": timeline_id,
        "output_path": output_path,
    }


@router.get("/status/{render_id}")
async def get_render_status(render_id: str) -> dict:
    """查询渲染进度。"""
    return {
        "render_id": render_id,
        "status": "pending",
        "progress": 0.0,
    }
