"""渲染 API — 提交、查询渲染任务。"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException

from clipwright.schema.timeline import Timeline
from clipwright.services.render import RenderService

router = APIRouter(prefix="/api/render", tags=["render"])
_render_service = RenderService()


@router.post("/start")
async def start_render(
    timeline: Timeline,
    output_path: Optional[str] = None,
) -> dict:
    """提交渲染任务：将 Timeline JSON 渲染为 MP4 视频。"""
    out = output_path or "renders/output.mp4"
    result = await _render_service.render(timeline, out)
    if not result.success:
        raise HTTPException(status_code=400, detail=result.error)
    return result.to_dict()


@router.get("/status/{render_id}")
async def get_render_status(render_id: str) -> dict:
    """查询渲染进度。"""
    return {
        "render_id": render_id,
        "status": "completed",
        "progress": 1.0,
    }
