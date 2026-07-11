"""音效 Agent（AudioAgent）— BGM 匹配与混音。

输入：带动画的时间线 + Persona 音频参数
输出：混音后的时间线（含 BGM 参考 + 音量包络）
"""

from __future__ import annotations

from clipwright.agents.base import BaseAgent
from clipwright.schema.agent import (
    AgentContext,
    AgentDecision,
    AudioInput,
    AudioOutput,
)
from clipwright.schema.timeline import Clip, ClipKind, Track
from clipwright.tool.registry import ToolRegistry


class AudioAgent(BaseAgent[AudioInput, AudioOutput]):
    """音效 Agent：BGM 匹配、混音编排。"""

    agent_name = "audio_agent"

    async def execute(
        self, input_data: AudioInput, context: AgentContext
    ) -> AudioOutput:
        notes: list[str] = []
        try:
            timeline = input_data.timeline
            audio_config = input_data.audio_config or {}

            if timeline is None:
                return AudioOutput(decision=AgentDecision.PASS, timeline=timeline)

            # 1. 解析 Persona 音频配置
            bgm_slots = audio_config.get("bgm_slots", {})
            voice_model = audio_config.get("voice", "")
            bpm_mode = bool(audio_config.get("bpm_detect", False))

            # 2. 确保有音频轨
            audio_track = None
            for t in timeline.tracks:
                if t.kind == ClipKind.AUDIO:
                    audio_track = t
                    break

            if audio_track is None:
                audio_track = Track(
                    id="a_agent",
                    name="音效 Agent",
                    kind=ClipKind.AUDIO,
                    index=len(timeline.tracks),
                )
                timeline.tracks.append(audio_track)

            # 3. 如果有 BPM 检测工具且配置启用，分析音频节奏
            bpm = 120
            if bpm_mode and ToolRegistry.get("bpm_detect") is not None:
                for clip in audio_track.clips:
                    if clip.asset_id:
                        result = await ToolRegistry.execute(
                            "bpm_detect",
                            input_path=clip.asset_id,
                        )
                        if result.status == "success":
                            detected = result.output.get("bpm", 120)
                            bpm = detected
                            notes.append(f"检测到 BPM: {bpm}")
                            break

            # 4. 标记 BGM 建议到音频 clip 的 metadata
            for clip in audio_track.clips:
                clip.volume = clip.volume or 0.7
                clip.metadata = {
                    **getattr(clip, "metadata", {}),
                    "bpm": bpm,
                    "bgm_slot": self._match_bgm_slot(clip.start_sec, timeline.duration_sec, bgm_slots),
                }

            # 5. 设置淡入淡出
            if audio_track.clips:
                first = audio_track.clips[0]
                first.volume = 0.3  # 淡入起点

            # 6. 如果没有音频 clip，添加一个占位 BGM 建议
            if not audio_track.clips:
                first_bgm = next(iter(bgm_slots.values()), [""])[0] if bgm_slots else ""
                notes.append(f"建议 BGM: {first_bgm or '未配置'}" if first_bgm else "无 BGM 配置")

            notes.append(f"音频配置: voice={voice_model or '默认'}, BPM模式={bpm_mode}")
            notes.append(f"BGM 槽位: {len(bgm_slots)} 个")

            return AudioOutput(
                decision=AgentDecision.PASS,
                timeline=timeline,
                audio_notes=notes,
            )

        except Exception as e:
            return self.build_error_output(str(e), AudioOutput)

    @staticmethod
    def _match_bgm_slot(
        clip_start: float, total_duration: float, bgm_slots: dict
    ) -> str:
        """根据时间位置匹配合适的 BGM 槽位。"""
        if not bgm_slots:
            return "default"
        progress = clip_start / max(total_duration, 1)
        # 按进度选择：开头、中间、高潮、结尾
        if progress < 0.15:
            for key in ("intro", "opening", "hook"):
                if key in bgm_slots:
                    return key
        elif progress < 0.4:
            for key in ("backing", "background", "theory_backing"):
                if key in bgm_slots:
                    return key
        elif progress < 0.7:
            for key in ("climax", "build", "intensity"):
                if key in bgm_slots:
                    return key
        else:
            for key in ("outro", "resolution", "ending"):
                if key in bgm_slots:
                    return key
        return list(bgm_slots.keys())[0]
