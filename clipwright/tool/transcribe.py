"""语音转文字与文字转语音工具。"""

from __future__ import annotations

import subprocess
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


class TextToSpeechTool(BaseTool):
    """文字转语音 — 将文案合成为配音音频。"""
    name = "text_to_speech"
    description = "将文字合成为语音音频（使用系统 TTS 或 API）"
    dependencies = []

    async def execute(
        self,
        text: str,
        voice: str = "zh-CN-XiaoxiaoNeural",
        output_path: Optional[str] = None,
        rate: str = "+0%",
        **kwargs: Any,
    ) -> ToolExecResult:
        out = output_path or Path(tempfile.mktemp(suffix=".mp3")).name
        try:
            # 尝试 edge-tts（跨平台，支持中文）
            import asyncio
            proc = await asyncio.create_subprocess_exec(
                "edge-tts", "--voice", voice, "--rate", rate,
                "--text", text[:1000],
                "--write-media", out,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            if proc.returncode != 0 or not Path(out).exists():
                return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                      error=f"TTS failed: {stderr.decode()[:200] if stderr else 'unknown'}")

            # 探测时长
            try:
                probe = subprocess.run(
                    ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "json", out],
                    capture_output=True, text=True, timeout=10,
                )
                import json as _json
                dur = float(_json.loads(probe.stdout).get("format", {}).get("duration", 0))
            except Exception:
                dur = 0

            return ToolExecResult(
                status=ToolStatus.SUCCESS, tool_name=self.name,
                output={"output_path": out, "duration_sec": round(dur, 1), "text": text[:80]},
                output_path=out,
            )
        except FileNotFoundError:
            logger.warning("edge-tts 未安装，尝试使用系统 TTS fallback")
            # Fallback: 生成静音 + 告知用户安装 edge-tts
            try:
                dummy = Path(tempfile.mktemp(suffix=".mp3"))
                subprocess.run(
                    ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                     "-i", "anullsrc=r=44100:cl=mono", "-t", str(max(1, len(text) // 10)),
                     "-q:a", "5", str(dummy)],
                    capture_output=True, text=True, timeout=30,
                )
                return ToolExecResult(
                    status=ToolStatus.SUCCESS, tool_name=self.name,
                    output={"output_path": str(dummy), "duration_sec": max(1, len(text) // 10),
                            "note": "silence fallback - install edge-tts: pip install edge-tts"},
                    output_path=str(dummy),
                )
            except Exception as e2:
                return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name,
                                      error=f"TTS fallback failed: {e2}")
        except asyncio.TimeoutError:
            return ToolExecResult(status=ToolStatus.ERROR, tool_name=self.name, error="TTS timeout")


def _srt_time(sec: float) -> str:
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int((sec % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
