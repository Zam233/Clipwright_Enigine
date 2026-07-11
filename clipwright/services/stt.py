"""语音转文字服务 — Whisper 转录 + 文本对齐。

能力：
1. transcribe: 将音频/视频文件转为带时间戳的文字
2. align: 将已有转录文本与音频对齐（匹配文案到配音）

后端策略：
- 优先使用 whisper Python 包
- 其次 faster-whisper
- 最后使用 FFmpeg + 模拟对齐（保底）
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional


class STTResult:
    """语音转文字的结果。"""
    def __init__(
        self,
        text: str,
        segments: list[dict[str, Any]],
        language: str = "",
        duration_sec: float = 0,
        model: str = "",
        success: bool = True,
        error: str = "",
    ):
        self.text = text
        self.segments = segments  # [{start, end, text, confidence}]
        self.language = language
        self.duration_sec = duration_sec
        self.model = model
        self.success = success
        self.error = error

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "text": self.text,
            "segments": self.segments,
            "language": self.language,
            "duration_sec": self.duration_sec,
            "model": self.model,
            "error": self.error,
        }


class STTService:
    """语音转文字服务。"""

    def __init__(self) -> None:
        self._model = None
        self._model_name = ""

    # ── 转录 ──

    async def transcribe(
        self,
        audio_path: str,
        language: str = "",
        model_size: str = "base",
        word_timestamps: bool = True,
    ) -> STTResult:
        """将音频文件转为带时间戳的文字。

        Args:
            audio_path: 音频或视频文件路径
            language: 语言代码（如 zh, en），留空自动检测
            model_size: Whisper 模型大小（tiny/base/small/medium/large）
            word_timestamps: 是否返回字级别时间戳
        """
        path = Path(audio_path)
        if not path.exists():
            return STTResult(text="", segments=[], success=False, error=f"文件不存在: {audio_path}")

        # 如果是视频，先提取音频
        audio_file = self._ensure_audio(path)

        try:
            # 尝试 whisper Python 包
            result = self._transcribe_whisper(audio_file, language, model_size, word_timestamps)
            if result.success:
                return result
        except Exception:
            pass

        try:
            # 尝试 faster-whisper
            result = self._transcribe_faster_whisper(audio_file, language, model_size, word_timestamps)
            if result.success:
                return result
        except Exception:
            pass

        # 保底：FFmpeg 提取音频信息 + 占位结果
        duration = self._get_duration(audio_file)
        return STTResult(
            text="",
            segments=[],
            duration_sec=duration,
            model="none",
            success=False,
            error="Whisper 未安装。请安装: pip install whisper 或 pip install faster-whisper",
        )

    # ── 文本对齐 ──

    async def align(
        self,
        audio_path: str,
        transcript_text: str,
        language: str = "",
    ) -> STTResult:
        """将已有的转录文本与音频对齐，生成带时间戳的字幕分段。

        即使没有 Whisper 模型，也可以通过音频时长估算时间戳。
        """
        path = Path(audio_path)
        if not path.exists():
            return STTResult(text="", segments=[], success=False, error=f"文件不存在: {audio_path}")

        audio_file = self._ensure_audio(path)
        duration = self._get_duration(audio_file)

        # 按句子分段并估算时间
        sentences = self._split_sentences(transcript_text)
        if not sentences:
            return STTResult(text="", segments=[], success=False, error="文本为空")

        # 按字符比例分配时间
        total_chars = sum(len(s) for s in sentences)
        if total_chars == 0:
            return STTResult(text="", segments=[], success=False, error="文本为空")

        segments: list[dict[str, Any]] = []
        current_time = 0.0
        for sent in sentences:
            char_ratio = len(sent) / total_chars
            seg_duration = duration * char_ratio
            segments.append({
                "start": round(current_time, 2),
                "end": round(current_time + seg_duration, 2),
                "text": sent.strip(),
                "confidence": 0.5,
            })
            current_time += seg_duration

        return STTResult(
            text=transcript_text,
            segments=segments,
            duration_sec=duration,
            model="align",
            success=True,
        )

    # ── 内部 ──

    def _ensure_audio(self, path: Path) -> str:
        """如果是视频文件，提取音频为临时 wav。"""
        video_exts = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv"}
        if path.suffix.lower() in video_exts:
            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            tmp.close()
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-i", str(path), "-vn", "-acodec", "pcm_s16le",
                     "-ar", "16000", "-ac", "1", tmp.name],
                    capture_output=True, text=True, timeout=300,
                )
                return tmp.name
            except Exception:
                return str(path)
        return str(path)

    def _transcribe_whisper(
        self, audio_path: str, language: str, model_size: str, word_timestamps: bool
    ) -> STTResult:
        """使用 openai-whisper 转录。"""
        import whisper  # type: ignore[import-untyped]

        model_key = f"{model_size}" if not self._model_name else self._model_name
        if self._model is None:
            self._model = whisper.load_model(model_key)
            self._model_name = model_key

        opts: dict[str, Any] = {"word_timestamps": word_timestamps}
        if language:
            opts["language"] = language

        result = self._model.transcribe(audio_path, **opts)

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

    def _transcribe_faster_whisper(
        self, audio_path: str, language: str, model_size: str, word_timestamps: bool
    ) -> STTResult:
        """使用 faster-whisper 转录。"""
        from faster_whisper import WhisperModel  # type: ignore[import-untyped]

        model = WhisperModel(model_size, device="cpu", compute_type="int8")
        segments_raw, info = model.transcribe(audio_path, language=language or None, word_timestamps=word_timestamps)

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

    @staticmethod
    def _get_duration(path: str) -> float:
        """用 FFmpeg 获取音频时长。"""
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "json", path],
                capture_output=True, text=True, timeout=30,
            )
            data = json.loads(result.stdout)
            return float(data.get("format", {}).get("duration", 0))
        except Exception:
            return 0

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        """按标点分句。"""
        sentences = re.split(r'[。！？.!?\n]', text)
        return [s.strip() for s in sentences if s.strip()]
