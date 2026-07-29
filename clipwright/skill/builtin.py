"""内置技能（Built-in Skills）。

技能是比工具更高层级的可组合能力：
- 一个技能可以编排多个工具调用
- 技能可以包含自有逻辑（聚合、分析、判断）
- 技能对外暴露统一的 execute() 接口
"""

from __future__ import annotations

from typing import Any

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

        # 1. 场景检测
        scene_result = await self._run_tool(
            "scene_detect",
            input_path=video_path,
            threshold=scene_threshold,
        )
        tool_calls.append({
            "tool": "scene_detect",
            "input": {"input_path": video_path, "threshold": scene_threshold},
            "output": scene_result,
        })

        # 2. 提取音频
        audio_result = await self._run_tool(
            "audio_extract",
            input_path=video_path,
            format="wav",
        )
        tool_calls.append({
            "tool": "audio_extract",
            "input": {"input_path": video_path, "format": "wav"},
            "output": audio_result,
        })

        # 3. BPM 检测（如果有音频输出路径）
        bpm = 120
        audio_path = (audio_result.get("output") or {}).get("output_path", "")
        if audio_path:
            bpm_result = await self._run_tool("bpm_detect", input_path=audio_path)
            tool_calls.append({
                "tool": "bpm_detect",
                "input": {"input_path": audio_path},
                "output": bpm_result,
            })
            bpm = (bpm_result.get("output") or {}).get("bpm", 120)

        # 聚合结果
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
    """字幕生成技能。

    从文本生成带时间戳的字幕片段。
    不需要依赖外部工具，纯逻辑处理。
    """

    name = "generate_captions"
    description = "从文本生成带时间戳的字幕片段"
    required_tools = []

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
            chunk = " ".join(words[i : i + words_per_segment])
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
    """音频节奏分析技能。

    分析音频的 BPM、节奏特征。
    """

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

        bpm = (bpm_result.get("output") or {}).get("bpm", 120)

        # 节奏分段
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
        return 2000  # 快节奏：2 秒/镜头
    if bpm >= 110:
        return 3000  # 中快：3 秒/镜头
    if bpm >= 80:
        return 5000  # 中等：5 秒/镜头
    return 7000  # 慢节奏：7 秒/镜头


# 注册辅助函数
def register_builtin_skills() -> None:
    """注册所有内置技能到 SkillRegistry。"""
    from clipwright.skill.registry import SkillRegistry

    from clipwright.skill.dub import DubScriptSkill

    skills = [
        AnalyzeVideoStructureSkill(),
        GenerateCaptionSkill(),
        AnalyzeAudioRhythmSkill(),
        DubScriptSkill(),
    ]
    for skill in skills:
        SkillRegistry.register(skill)
