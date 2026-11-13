"""基础技能包插件（skill_pack）。

引擎侧只保留 Skill 契约（`BaseSkill` / `SkillRegistry` / 工具面接线），业务技能
全部由插件提供——本插件承接原先 `clipwright/skill/builtin.py` 里的三个技能：

- analyze_video_structure（scene_detect + audio_extract + bpm_detect）
- generate_captions（纯逻辑切段）
- analyze_audio_rhythm（bpm_detect → 节奏档位 / 建议镜头时长）

实现逐字平移（2026-11-14 skill 插件化），仅改归属来源。
"""

from __future__ import annotations

from typing import Any

from clipwright.plugins import CapabilityPlugin
from clipwright.plugins.contribution import Contribution, ContributionKind
from clipwright.schema.plugin import PluginManifest
from clipwright.schema.skill import SkillExecResult, SkillStatus
from clipwright.skill.base import BaseSkill


class AnalyzeVideoStructureSkill(BaseSkill):
    """视频结构分析技能。

    组合调用：scene_detect + audio_extract + bpm_detect
    输出视频的完整结构分析报告。
    """

    name = "analyze_video_structure"
    description = "分析视频的结构：场景分割 + 节奏检测 + 音频特征"
    required_tools = ["scene_detect", "audio_extract", "bpm_detect"]

    async def execute(
        self,
        video_path: str,
        scene_threshold: float = 0.3,
        **kwargs: Any,
    ) -> SkillExecResult:
        tool_calls: list[dict[str, Any]] = []

        scene_result = await self._run_tool(
            "scene_detect", input_path=video_path, threshold=scene_threshold,
        )
        tool_calls.append({
            "tool": "scene_detect",
            "input": {"input_path": video_path, "threshold": scene_threshold},
            "output": scene_result,
        })

        audio_result = await self._run_tool(
            "audio_extract", input_path=video_path, format="wav",
        )
        tool_calls.append({
            "tool": "audio_extract",
            "input": {"input_path": video_path, "format": "wav"},
            "output": audio_result,
        })

        bpm = 120
        audio_path = (audio_result.get("output") or {}).get("output_path", "")
        if audio_path:
            bpm_result = await self._run_tool("bpm_detect", input_path=audio_path)
            tool_calls.append({
                "tool": "bpm_detect",
                "input": {"input_path": audio_path},
                "output": bpm_result,
            })
            bpm = (bpm_result.get("output") or {}).get("bpm")

        scenes = (scene_result.get("output") or {}).get("scene_changes", [])
        scene_count = len(scenes)
        avg_shot_duration = (
            sum(s["timestamp_sec"] for s in scenes) / max(scene_count, 1)
            if scene_count > 1
            else 0
        )

        return SkillExecResult(
            status=SkillStatus.SUCCESS,
            skill_name=self.name,
            output={
                "video_path": video_path,
                "scene_count": scene_count,
                "scene_changes": scenes,
                "avg_shot_duration_sec": round(avg_shot_duration, 2),
                "bpm": bpm,
                "has_audio": bool(audio_path),
            },
            tool_calls=tool_calls,
        )


class GenerateCaptionSkill(BaseSkill):
    """字幕生成技能：从文本生成带时间戳的字幕片段。"""

    name = "generate_captions"
    description = "从文本生成带时间戳的字幕片段"
    required_tools: list[str] = []

    async def execute(
        self,
        text: str,
        words_per_segment: int = 5,
        duration_per_segment: float = 2.0,
        **kwargs: Any,
    ) -> SkillExecResult:
        words = text.split()
        segments = []
        for i in range(0, len(words), words_per_segment):
            chunk = " ".join(words[i:i + words_per_segment])
            start = (i // words_per_segment) * duration_per_segment
            segments.append({
                "start_sec": start,
                "end_sec": start + duration_per_segment,
                "text": chunk,
            })

        return SkillExecResult(
            status=SkillStatus.SUCCESS,
            skill_name=self.name,
            output={
                "segments": segments,
                "total_segments": len(segments),
                "total_duration_sec": len(segments) * duration_per_segment,
            },
        )


class AnalyzeAudioRhythmSkill(BaseSkill):
    """音频节奏分析技能：BPM → 节奏档位 + 建议镜头时长。"""

    name = "analyze_audio_rhythm"
    description = "分析音频的节奏特征（BPM 等）"
    required_tools = ["bpm_detect"]

    async def execute(
        self,
        audio_path: str,
        **kwargs: Any,
    ) -> SkillExecResult:
        tool_calls: list[dict[str, Any]] = []

        bpm_result = await self._run_tool("bpm_detect", input_path=audio_path)
        tool_calls.append({
            "tool": "bpm_detect",
            "input": {"input_path": audio_path},
            "output": bpm_result,
        })

        _out = bpm_result.get("output") or {}
        _raw_bpm = _out.get("bpm")
        bpm = float(_raw_bpm) if isinstance(_raw_bpm, (int, float)) else None
        if bpm is None or bpm <= 0:
            return SkillExecResult(
                status=SkillStatus.TOOL_FAILED,
                skill_name=self.name,
                error="BPM 检测失败：未得到有效 BPM（不伪造默认值）",
                tool_calls=tool_calls,
                output={"bpm": None, "rhythm_profile": None, "cut_suggestion_ms": None},
            )

        rhythm_profile = "fast" if bpm >= 120 else ("medium" if bpm >= 80 else "slow")
        return SkillExecResult(
            status=SkillStatus.SUCCESS,
            skill_name=self.name,
            output={
                "bpm": bpm,
                "rhythm_profile": rhythm_profile,
                "cut_suggestion_ms": _bpm_to_cut_suggestion(bpm),
            },
            tool_calls=tool_calls,
        )


def _bpm_to_cut_suggestion(bpm: float) -> int:
    """根据 BPM 推荐镜头时长（毫秒）。"""
    if bpm >= 140:
        return 2000
    if bpm >= 110:
        return 3000
    if bpm >= 80:
        return 5000
    return 7000


class SkillPackPlugin(CapabilityPlugin):
    """基础技能包：把三个技能作为插件贡献注册。"""

    def __init__(self) -> None:
        super().__init__()
        self.manifest: PluginManifest | None = None

    def contributions(self) -> list[Contribution]:
        return [
            Contribution(ContributionKind.SKILL, "analyze_video_structure",
                         AnalyzeVideoStructureSkill()),
            Contribution(ContributionKind.SKILL, "generate_captions",
                         GenerateCaptionSkill()),
            Contribution(ContributionKind.SKILL, "analyze_audio_rhythm",
                         AnalyzeAudioRhythmSkill()),
        ]

    def shutdown(self) -> None:
        pass
