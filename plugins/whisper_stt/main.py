"""Whisper STT 插件 — 将内置 STTService 包装为可发现的 Tool 和 Skill。

支持的引擎（按优先级）：
  1. faster-whisper（需 pip install faster-whisper）
  2. openai-whisper（需 pip install openai-whisper）

模型大小通过 config.yaml 的 model_size 字段配置（tiny/base/small/medium/large）。
"""

from __future__ import annotations

from typing import Any, Optional

from clipwright.plugins import CapabilityPlugin
from clipwright.tool.base import BaseTool
from clipwright.tool.registry import ToolRegistry
from clipwright.skill.base import BaseSkill
from clipwright.skill.registry import SkillRegistry
from clipwright.schema.skill import SkillExecResult
from clipwright.schema.plugin import PluginManifest, PluginKind
from clipwright.config import logger


class WhisperTranscribeTool(BaseTool):
    """语音转文字 Tool — 调用 STTService 完成转录。"""

    name = "whisper_transcribe"
    agent_callable = True
    description = "将音频文件转录为文字，返回带时间戳的分段文本"
    parameters_schema = {
        "type": "object",
        "properties": {
            "audio_path": {"type": "string", "description": "音频文件路径"},
            "language": {"type": "string", "description": "语言代码（如 zh/en），留空自动检测"},
            "model_size": {"type": "string", "description": "模型大小 tiny/base/small/medium/large"},
        },
        "required": ["audio_path"],
    }

    def __init__(self, default_model: str = "base") -> None:
        self._default_model = default_model

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        from clipwright.services.stt import STTService
        audio_path = kwargs.get("audio_path", "")
        if not audio_path:
            return {"success": False, "error": "缺少 audio_path 参数"}
        language = kwargs.get("language", "")
        model_size = kwargs.get("model_size", self._default_model)
        try:
            svc = STTService()
            result = await svc.transcribe(audio_path, language=language, model_size=model_size)
            if result.success:
                return {
                    "success": True,
                    "text": result.text,
                    "segments": [
                        {"start": s.start, "end": s.end, "text": s.text}
                        for s in result.segments
                    ],
                    "language": result.language,
                }
            return {"success": False, "error": result.error or "转录失败"}
        except Exception as e:
            return {"success": False, "error": str(e)}


class TranscribeAndAlignSkill(BaseSkill):
    """转录并对齐到时间轴 Skill。"""

    name = "transcribe_and_align"
    description = "转录音频并生成可直接添加到时间轴的字幕 clip 列表"
    required_tools: list[str] = []

    async def execute(self, **kwargs) -> SkillExecResult:
        tool = WhisperTranscribeTool()
        result = await tool.execute(**kwargs)
        if not result.get("success"):
            return SkillExecResult(status="failed", skill_name=self.name, error=result.get("error"))
        clips = []
        for seg in result.get("segments", []):
            clips.append({
                "kind": "caption",
                "start_sec": seg["start"],
                "duration_sec": max(0.5, seg["end"] - seg["start"]),
                "text": seg["text"],
                "font_size": 28,
                "font_color": "#FFFFFF",
            })
        return SkillExecResult(status="success", skill_name=self.name, output={"clips": clips, "text": result.get("text", "")})


class WhisperSTTPlugin(CapabilityPlugin):
    manifest = PluginManifest(
        id="whisper_stt", name="Whisper STT", version="1.0.0",
        kind=PluginKind.CAPABILITY,
        description="Speech-to-text via Whisper, registered as Tool and Skill",
        author="Clipwright Team",
    )

    def initialize(self) -> None:
        model_size = (self.config or {}).get("model_size", "base")
        tool = WhisperTranscribeTool(default_model=model_size)
        ToolRegistry.register(tool, plugin_id=self.manifest.id)
        skill = TranscribeAndAlignSkill()
        SkillRegistry.register(skill, plugin_id=self.manifest.id)
        logger.info("[WhisperSTT] Tool + Skill 已注册 (model=%s)", model_size)

    def shutdown(self) -> None:
        pass


__all__ = ["WhisperSTTPlugin"]
