"""EDL / FCPXML API — 导入导出。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.params import Body

from clipwright.services.edl import parse_edl, parse_fcpxml, to_edl, to_fcpxml

router = APIRouter(prefix="/api/edl", tags=["edl"])


@router.post("/import/edl")
async def import_edl(content: str = Body(...)) -> dict:
    """导入 EDL 文件 → Timeline clip 列表。"""
    clips = parse_edl(content)
    if not clips:
        raise HTTPException(status_code=400, detail="无法解析 EDL 内容")
    return {"clips": clips, "count": len(clips)}


@router.post("/import/fcpxml")
async def import_fcpxml(content: str = Body(...)) -> dict:
    """导入 FCPXML 文件 → Timeline clip 列表。"""
    clips = parse_fcpxml(content)
    return {"clips": clips, "count": len(clips)}


@router.post("/export/edl")
async def export_edl(clips: list[dict] = Body(...), fps: float = 30.0) -> dict:
    """将 Timeline clip 列表导出为 EDL 格式。"""
    edl = to_edl(clips, fps)
    return {"edl": edl, "format": "edl"}


@router.post("/export/fcpxml")
async def export_fcpxml(clips: list[dict] = Body(...), timeline: dict = {}) -> dict:
    """将 Timeline clip 列表导出为 FCPXML 格式。"""
    xml = to_fcpxml(clips, timeline)
    return {"fcpxml": xml, "format": "fcpxml"}
