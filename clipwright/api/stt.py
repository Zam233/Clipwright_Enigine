"""语音转文字 API — 转录 + 对齐。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException

from clipwright.services.stt import STTService

router = APIRouter(prefix="/api/stt", tags=["stt"])
_service = STTService()


@router.post("/transcribe")
async def transcribe(
    audio_path: str = Body(..., description="音频/视频文件路径"),
    language: str = Body(default=""),
    model_size: str = Body(default="base"),
    word_timestamps: bool = Body(default=True),
) -> dict[str, Any]:
    """将音频/视频文件转录为带时间戳的文字。"""
    result = await _service.transcribe(
        audio_path=audio_path,
        language=language,
        model_size=model_size,
        word_timestamps=word_timestamps,
    )
    if not result.success and result.error:
        raise HTTPException(status_code=400, detail=result.error)
    return result.to_dict()


@router.post("/align")
async def align(
    audio_path: str = Body(..., description="音频/视频文件路径"),
    transcript_text: str = Body(..., description="已有文案内容"),
    language: str = Body(default=""),
) -> dict[str, Any]:
    """将已有文案与音频对齐，生成带时间戳的字幕分段。"""
    result = await _service.align(
        audio_path=audio_path,
        transcript_text=transcript_text,
        language=language,
    )
    if not result.success:
        raise HTTPException(status_code=400, detail=result.error)
    return result.to_dict()
