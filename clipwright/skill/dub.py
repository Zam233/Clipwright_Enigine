"""dub_script 技能 — 文案切分 + 逐段配音。

委托 VoiceService.dub_script()，供 Agent 和 /api/skill 调用。
"""

from __future__ import annotations

from typing import Any

from clipwright.schema.skill import SkillExecResult, SkillStatus
from clipwright.skill.base import BaseSkill


class DubScriptSkill(BaseSkill):
    """文案切分 + 逐段配音技能。

    将文案按句/段切分，用指定音色逐段合成语音并返回各段元数据。
    """

    name = "dub_script"
    description = "文案切分配音：将文案按句/段切分，用克隆音色逐段合成语音"
    required_tools: list[str] = []

    async def execute(
        self,
        *,
        voice_id: str,
        text: str,
        split_mode: str = "sentence",
        provider: str = "",
        target_model: str = "",
        instructions: str = "",
        **kwargs: Any,
    ) -> SkillExecResult:
        from clipwright.services.voice import get_voice_service

        svc = get_voice_service()
        result = await svc.dub_script(
            voice_id=voice_id,
            text=text,
            split_mode=split_mode,
            provider=provider,
            target_model=target_model,
            instructions=instructions,
        )
        if not result.success:
            return SkillExecResult(
                status=SkillStatus.ERROR,
                skill_name=self.name,
                error=result.error,
            )
        return SkillExecResult(
            status=SkillStatus.SUCCESS,
            skill_name=self.name,
            output={
                "segments": result.data.get("segments", []),
                "total_segments": result.data.get("total", 0),
                "total_duration_sec": result.data.get("total_duration_sec", 0.0),
            },
        )
