"""Voice API — 声音克隆与 TTS 合成路由。

端点：upload / clone / list / delete / synthesize / dub。
"""

from __future__ import annotations

import base64
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, UploadFile
from pydantic import BaseModel

from clipwright.config import settings, logger
from clipwright.services.voice import get_voice_service

router = APIRouter(prefix="/api/voice", tags=["voice"])


# ── Request models ──


class CloneRequest(BaseModel):
    provider: str = ""
    voice_name: str = ""
    audio_path: str = ""
    audio_url: str = ""
    data_uri: str = ""
    target_model: str = ""
    audition_text: str = ""


class SynthesizeRequest(BaseModel):
    voice_id: str
    text: str
    provider: str = ""
    target_model: str = ""
    instructions: str = ""
    output_path: str = ""


class DubRequest(BaseModel):
    voice_id: str
    text: str
    split_mode: str = "sentence"
    provider: str = ""
    target_model: str = ""
    instructions: str = ""


# ── Endpoints ──


@router.post("/upload")
async def upload_voice_audio(file: UploadFile) -> dict[str, Any]:
    """上传音频文件，返回 data_uri（base64）供克隆使用。"""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    ext = Path(file.filename).suffix or ".wav"
    dest = settings.tts_upload_dir / f"{uuid.uuid4().hex[:12]}{ext}"
    dest.parent.mkdir(parents=True, exist_ok=True)

    _MAX_VOICE_SIZE = 100 * 1024 * 1024  # 100MB
    chunks = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)  # 1MB chunks
        if not chunk:
            break
        total += len(chunk)
        if total > _MAX_VOICE_SIZE:
            raise HTTPException(status_code=413, detail="文件过大（最大 100MB）")
        chunks.append(chunk)
    content = b"".join(chunks)
    dest.write_bytes(content)

    mime = _guess_mime(ext)
    b64 = base64.b64encode(content).decode()
    data_uri = f"data:{mime};base64,{b64}"

    logger.info("Voice upload: %s → %s (%d bytes)", file.filename, dest.name, len(content))

    return {
        "filename": file.filename,
        "saved_as": str(dest),
        "size": len(content),
        "data_uri": data_uri,
        "mime": mime,
    }


@router.post("/clone")
async def clone_voice(req: CloneRequest) -> dict[str, Any]:
    """克隆音色并持久化元数据。"""
    svc = get_voice_service()
    result = await svc.clone(
        provider=req.provider,
        voice_name=req.voice_name,
        audio_path=req.audio_path,
        audio_url=req.audio_url,
        data_uri=req.data_uri,
        target_model=req.target_model,
        audition_text=req.audition_text,
    )
    if not result.success:
        logger.warning("Voice clone failed: %s (provider=%s)", result.error, req.provider)
        raise HTTPException(status_code=400, detail=result.error)
    logger.info("Voice cloned: %s (id=%s, provider=%s)", req.voice_name, result.data.get("id"), req.provider)
    return result.data


@router.get("/list")
async def list_voices() -> list[dict[str, Any]]:
    """列出所有已克隆音色。"""
    svc = get_voice_service()
    return svc.list_voices()


@router.delete("/{db_id}")
async def delete_voice(db_id: str) -> dict[str, str]:
    """删除指定音色记录。"""
    svc = get_voice_service()
    if not svc.delete_voice(db_id):
        raise HTTPException(status_code=404, detail=f"Voice '{db_id}' not found")
    logger.info("Voice deleted: %s", db_id)
    return {"deleted": db_id}


@router.post("/synthesize")
async def synthesize_voice(req: SynthesizeRequest) -> dict[str, Any]:
    """用已克隆音色合成语音。"""
    svc = get_voice_service()
    result = await svc.synthesize(
        voice_id=req.voice_id,
        text=req.text,
        provider=req.provider,
        target_model=req.target_model,
        instructions=req.instructions,
        output_path=req.output_path,
    )
    if not result.success:
        logger.warning("Voice synthesize failed: %s (voice_id=%s)", result.error, req.voice_id)
        raise HTTPException(status_code=400, detail=result.error)
    logger.info("Voice synthesized: voice_id=%s text=%d chars dur=%.1fs", req.voice_id, len(req.text), result.data.get("duration_sec", 0))
    return result.data


@router.post("/dub")
async def dub_script(req: DubRequest) -> dict[str, Any]:
    """文案切分 + 逐段配音。"""
    svc = get_voice_service()
    result = await svc.dub_script(
        voice_id=req.voice_id,
        text=req.text,
        split_mode=req.split_mode,
        provider=req.provider,
        target_model=req.target_model,
        instructions=req.instructions,
    )
    if not result.success:
        logger.warning("Voice dub failed: %s (voice_id=%s)", result.error, req.voice_id)
        raise HTTPException(status_code=400, detail=result.error)
    total = result.data.get("total", 0)
    logger.info("Voice dub: voice_id=%s text=%d chars → %d segments (%.1fs)", req.voice_id, len(req.text), total, result.data.get("total_duration_sec", 0))
    return {
        "segments": result.data.get("segments", []),
        "total": result.data.get("total", 0),
        "total_duration_sec": result.data.get("total_duration_sec", 0.0),
    }


# ── Helpers ──


def _guess_mime(suffix: str) -> str:
    return {
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".aac": "audio/aac",
        ".ogg": "audio/ogg",
        ".flac": "audio/flac",
    }.get(suffix.lower(), "audio/wav")
