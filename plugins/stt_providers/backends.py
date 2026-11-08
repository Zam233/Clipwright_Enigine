"""Whisper 转录实现（**能力方**：插件拥有实现，engine 只留服务编排）。

2026-11-08 能力迁移（审计 B7-T7.4）：`services/stt.py` 里的
`_transcribe_whisper` / `_transcribe_faster_whisper` 方法体已搬到这里；
engine 侧只剩 3 行转发（供 `STTService.transcribe` 与既有调用面使用）。

模型缓存是**模块级**的（按 `model_size` 分键）：插件被禁用/卸载时随之失效，
这正是"能力归插件"的可观察点。
"""
from __future__ import annotations

from typing import Any

#: 按 model_size 分键的已加载模型（不同尺寸不互相复用）
_MODELS: dict[str, Any] = {}
_FASTER_MODELS: dict[str, Any] = {}


def probe_whisper() -> bool:
    """openai-whisper 是否可导入。"""
    try:
        import importlib.util
        return importlib.util.find_spec("whisper") is not None
    except Exception:  # noqa: BLE001
        return False


def probe_faster_whisper() -> bool:
    """faster-whisper 是否可导入。"""
    try:
        import importlib.util
        return importlib.util.find_spec("faster_whisper") is not None
    except Exception:  # noqa: BLE001
        return False


def transcribe_whisper(
    audio_path: str, language: str, model_size: str, word_timestamps: bool,
) -> Any:
    """使用 openai-whisper 转录（返回 `STTResult`）。"""
    from clipwright.services.stt import STTResult

    import whisper  # type: ignore[import-untyped]

    if model_size not in _MODELS:
        _MODELS[model_size] = whisper.load_model(model_size)
    model = _MODELS[model_size]

    opts: dict[str, Any] = {"word_timestamps": word_timestamps}
    if language:
        opts["language"] = language

    result = model.transcribe(audio_path, **opts)

    segments = []
    for seg in result.get("segments", []):
        segments.append({
            "start": round(seg.get("start", 0), 2),
            "end": round(seg.get("end", 0), 2),
            "text": seg.get("text", "").strip(),
            "confidence": seg.get("confidence", 0),
        })

    return STTResult(
        text=result.get("text", ""),
        segments=segments,
        language=result.get("language", language),
        duration_sec=result.get("duration", 0),
        model=f"whisper-{model_size}",
        success=True,
    )


def transcribe_faster_whisper(
    audio_path: str, language: str, model_size: str, word_timestamps: bool,
) -> Any:
    """使用 faster-whisper 转录（返回 `STTResult`）。"""
    from clipwright.services.stt import STTResult

    from faster_whisper import WhisperModel  # type: ignore[import-untyped]

    if model_size not in _FASTER_MODELS:
        _FASTER_MODELS[model_size] = WhisperModel(
            model_size, device="cpu", compute_type="int8"
        )
    model = _FASTER_MODELS[model_size]
    segments_raw, info = model.transcribe(
        audio_path, language=language or None, word_timestamps=word_timestamps
    )

    segments = []
    full_text = ""
    for seg in segments_raw:
        segments.append({
            "start": round(seg.start, 2),
            "end": round(seg.end, 2),
            "text": seg.text.strip(),
            "confidence": round(seg.avg_logprob, 4) if seg.avg_logprob else 0,
        })
        full_text += seg.text + " "

    return STTResult(
        text=full_text.strip(),
        segments=segments,
        language=info.language if info else language,
        duration_sec=round(info.duration, 2) if info else 0,
        model=f"faster-whisper-{model_size}",
        success=True,
    )


def reset_caches() -> None:
    """测试用：清空模型缓存（等价于"插件重新加载"）。"""
    _MODELS.clear()
    _FASTER_MODELS.clear()
