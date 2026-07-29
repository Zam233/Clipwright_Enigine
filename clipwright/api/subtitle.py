"""字幕 API — SRT 导入/导出 + 语音转文字自动生成。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.params import Body

from clipwright.security import assert_allowed_path
from clipwright.services.stt import STTService
from clipwright.services.subtitle import (
    parse_srt,
    segments_to_timeline_clips,
    timeline_clips_to_segments,
    to_srt,
)

router = APIRouter(prefix="/api/subtitle", tags=["subtitle"])
_stt = STTService()


# ── SRT 导入/导出 ──

@router.post("/import")
async def import_srt(srt_content: str = Body(...)) -> dict:
    """导入 SRT 字幕文件 → Timeline caption clips。"""
    segments = parse_srt(srt_content)
    if not segments:
        raise HTTPException(status_code=400, detail="无法解析 SRT 内容")
    clips = segments_to_timeline_clips(segments)
    return {
        "segments": len(segments),
        "clips": clips,
        "srt": srt_content,
    }


@router.post("/export")
async def export_srt(clips: list[dict]) -> dict:
    """将 Timeline caption clips → SRT 格式文本。"""
    segments = timeline_clips_to_segments(clips)
    if not segments:
        raise HTTPException(status_code=400, detail="没有可导出的字幕片段")
    srt_text = to_srt(segments)
    return {
        "segments": len(segments),
        "srt": srt_text,
    }


# ── 语音转文字自动生成字幕 ──

@router.post("/transcribe")
async def transcribe_subtitle(
    audio_path: str = Body(..., description="音频/视频文件路径"),
    language: str = Body(default="", description="语言代码，留空自动检测"),
    model_size: str = Body(default="base"),
    format: str = Body(default="timeline", description="输出格式: timeline / srt"),
) -> dict:
    """从音频自动转录音频 → 生成带时间戳的字幕（支持 Whisper 或保底对齐）。"""
    assert_allowed_path(Path(audio_path))
    result = await _stt.transcribe(
        audio_path=audio_path,
        language=language,
        model_size=model_size,
        word_timestamps=True,
    )
    if not result.success and result.error:
        # Whisper 不可用时，返回空结果
        return {
            "success": False,
            "error": result.error,
            "segments": 0,
            "clips": [],
            "srt": "",
        }

    # 转为字幕片段
    from clipwright.services.subtitle import SubtitleSegment
    segs = [
        SubtitleSegment(i + 1, s["start"], s["end"], s["text"])
        for i, s in enumerate(result.segments)
    ]
    clips = segments_to_timeline_clips(segs)
    srt_text = to_srt(segs)

    response: dict = {
        "success": True,
        "segments": len(segs),
        "language": result.language,
        "duration_sec": result.duration_sec,
        "model": result.model,
    }

    if format == "srt":
        response["srt"] = srt_text
    else:
        response["clips"] = clips

    return response


@router.post("/align")
async def align_subtitle(
    audio_path: str = Body(..., description="音频/视频文件路径"),
    script_text: str = Body(..., description="已有文案/脚本内容"),
    language: str = Body(default=""),
    format: str = Body(default="timeline", description="输出格式: timeline / srt"),
) -> dict:
    """将已有文案与音频对齐 → 生成带时间戳的字幕（无需 Whisper）。"""
    assert_allowed_path(Path(audio_path))
    result = await _stt.align(
        audio_path=audio_path,
        transcript_text=script_text,
        language=language,
    )
    if not result.success:
        raise HTTPException(status_code=400, detail=result.error)

    from clipwright.services.subtitle import SubtitleSegment
    segs = [
        SubtitleSegment(i + 1, s["start"], s["end"], s["text"])
        for i, s in enumerate(result.segments)
    ]
    clips = segments_to_timeline_clips(segs)
    srt_text = to_srt(segs)

    response: dict = {
        "success": True,
        "segments": len(segs),
        "duration_sec": result.duration_sec,
        "model": result.model,
    }

    if format == "srt":
        response["srt"] = srt_text
    else:
        response["clips"] = clips

    return response
