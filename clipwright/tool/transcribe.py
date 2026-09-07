"""语音转文字与文字转语音工具。"""

from __future__ import annotations

import subprocess
import uuid
import tempfile
from pathlib import Path
from typing import Any, Optional

from clipwright.config import logger
from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.tool.base import BaseTool


class WhisperTranscribeTool(BaseTool):
    """语音转文字 — 使用 Whisper 或 API 进行语音识别。"""
    name = "whisper_transcribe"
    description = "将音频文件转为文字字幕（SRT/JSON/VTT），支持多语种"
    dependencies = []

    async def execute(
        self,
        audio_path: str,
        language: str = "zh",
        output_format: str = "json",
        **kwargs: Any,
    ) -> ToolExecResult:
        if not Path(audio_path).exists():
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error=f"音频文件不存在: {audio_path}")

        try:
            # 使用 stt service
            from clipwright.services.stt import STTService
            svc = STTService()
            result = await svc.transcribe(audio_path, language=language)
            segments = result.get("segments", [])
            text = result.get("text", "")

            output = {
                "text": text,
                "segments": segments,
                "language": language,
                "duration_sec": round(segments[-1]["end"], 1) if segments else 0,
            }

            if output_format == "srt":
                lines = []
                for i, seg in enumerate(segments, 1):
                    start = _srt_time(seg["start"])
                    end = _srt_time(seg["end"])
                    lines.append(f"{i}\n{start} --> {end}\n{seg['text']}\n")
                output["srt"] = "\n".join(lines)

            return ToolExecResult(status=ToolStatus.SUCCESS, tool_name=self.name, output=output)
        except Exception as e:
            logger.error("WhisperTranscribe 失败: %s", e)
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error=str(e))


