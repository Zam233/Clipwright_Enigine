"""语音克隆与 TTS 合成工具 — 委托 VoiceService 编排层。

VoiceCloneTool：从音频样本克隆音色并持久化元数据。
TextToSpeechTool：用已克隆音色将文本合成为语音文件。
"""

from __future__ import annotations

from typing import Any

from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.tool.base import BaseTool


class VoiceCloneTool(BaseTool):
    """声音克隆工具 — 从音频样本克隆音色并入库。"""

    name = "voice_clone"
    description = "声音克隆：从音频样本克隆音色并入库"
    dependencies: list[str] = []

    async def execute(
        self,
        *,
        audio_path: str = "",
        audio_url: str = "",
        voice_name: str = "",
        provider: str = "",
        target_model: str = "",
        audition_text: str = "",
        **kwargs: Any,
    ) -> ToolExecResult:
        from clipwright.services.voice import get_voice_service

        svc = get_voice_service()
        result = await svc.clone(
            audio_path=audio_path,
            audio_url=audio_url,
            voice_name=voice_name,
            provider=provider,
            target_model=target_model,
            audition_text=audition_text,
        )
        if not result.success:
            return ToolExecResult(
                status=ToolStatus.ERROR,
                tool_name=self.name,
                error=result.error,
            )
        return ToolExecResult(
            status=ToolStatus.SUCCESS,
            tool_name=self.name,
            output=result.data,
        )


class TextToSpeechTool(BaseTool):
    """TTS 文字转语音工具 — 用已克隆音色将文本合成语音文件。"""

    name = "text_to_speech"
    description = "TTS 文字转语音：用已克隆音色将文本合成语音文件"
    dependencies: list[str] = []

    async def execute(
        self,
        *,
        text: str = "",
        voice_id: str = "",
        provider: str = "",
        target_model: str = "",
        instructions: str = "",
        output_path: str = "",
        **kwargs: Any,
    ) -> ToolExecResult:
        from clipwright.services.voice import get_voice_service

        svc = get_voice_service()
        result = await svc.synthesize(
            voice_id=voice_id,
            text=text,
            provider=provider,
            target_model=target_model,
            instructions=instructions,
            output_path=output_path,
        )
        if not result.success:
            return ToolExecResult(
                status=ToolStatus.ERROR,
                tool_name=self.name,
                error=result.error,
            )
        return ToolExecResult(
            status=ToolStatus.SUCCESS,
            tool_name=self.name,
            output=result.data,
            output_path=result.data.get("audio_path"),
        )
