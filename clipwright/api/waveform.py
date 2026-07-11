"""波形 API — 生成音频波形数据。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.params import Body

from clipwright.services.waveform import WaveformGenerator

router = APIRouter(prefix="/api/waveform", tags=["waveform"])


@router.post("/generate")
async def generate_waveform(
    audio_path: str = Body(...),
    samples: int = Body(default=200),
) -> dict:
    """从音频文件生成波形采样数据（用于时间轴可视化）。"""
    result = await WaveformGenerator.generate(audio_path, samples)
    return result
