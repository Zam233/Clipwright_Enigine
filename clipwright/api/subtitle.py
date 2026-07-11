"""字幕 API — SRT 导入/导出。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.params import Body

from clipwright.services.subtitle import (
    parse_srt,
    segments_to_timeline_clips,
    timeline_clips_to_segments,
    to_srt,
)

router = APIRouter(prefix="/api/subtitle", tags=["subtitle"])


@router.post("/import")
async def import_srt(srt_content: str = Body(...)) -> list[dict]:
    """导入 SRT 字幕文件 → Timeline caption clips。"""
    segments = parse_srt(srt_content)
    if not segments:
        raise HTTPException(status_code=400, detail="无法解析 SRT 内容")
    return segments_to_timeline_clips(segments)


@router.post("/export")
async def export_srt(clips: list[dict]) -> str:
    """将 Timeline caption clips → SRT 格式文本。"""
    segments = timeline_clips_to_segments(clips)
    if not segments:
        raise HTTPException(status_code=400, detail="没有可导出的字幕片段")
    return to_srt(segments)
