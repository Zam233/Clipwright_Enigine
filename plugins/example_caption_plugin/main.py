"""Example third-party caption plugin for Clipwright.

Demonstrates plugin registration of both a Tool and a Skill.
"""

from __future__ import annotations

from typing import Any

from clipwright.plugins import CapabilityPlugin
from clipwright.schema.plugin import PluginManifest, PluginKind
from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.schema.skill import SkillExecResult, SkillStatus
from clipwright.skill import SkillRegistry
from clipwright.skill.base import BaseSkill
from clipwright.tool.base import BaseTool
from clipwright.tool.registry import ToolRegistry


# ── Plugin class ──────────────────────────────────────────────

class ExampleCaptionPlugin(CapabilityPlugin):
    """Example plugin that registers a caption tool and a caption skill."""

    manifest = PluginManifest(
        id="example_caption_plugin",
        name="Example Caption Plugin",
        version="1.0.0",
        kind=PluginKind.CAPABILITY,
        description="Demonstrates tool + skill registration via plugin",
        author="Clipwright Team",
    )

    def __init__(self) -> None:
        self._initialized = False

    def initialize(self) -> None:
        """注册插件提供的 Tool 和 Skill。"""
        ToolRegistry.register(CaptionSegmentTool())
        SkillRegistry.register(SummarizeCaptionSkill())
        self._initialized = True

    def shutdown(self) -> None:
        self._initialized = False


# ── Tool: 纯逻辑字幕切分工具 ──────────────────────────────────

class CaptionSegmentTool(BaseTool):
    """字幕片段切分工具。"""
    name = "caption_segment"
    agent_callable = True
    description = "将文本切分为带时间戳的字幕片段"
    dependencies = []  # 纯逻辑，无外部依赖

    async def execute(
        self,
        text: str,
        words_per_segment: int = 5,
        duration_per_segment: float = 2.0,
        **kwargs: Any,
    ) -> ToolExecResult:
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

        return ToolExecResult(
            status=ToolStatus.SUCCESS,
            tool_name=self.name,
            output={
                "segments": segments,
                "total_segments": len(segments),
            },
        )


# ── Skill: 字幕总结技能 ───────────────────────────────────────

class SummarizeCaptionSkill(BaseSkill):
    """字幕总结技能：切分字幕 + 统计。"""
    name = "summarize_captions"
    description = "将文本切分为字幕并统计信息"
    required_tools = ["caption_segment"]

    async def execute(
        self,
        text: str,
        **kwargs: Any,
    ) -> SkillExecResult:
        # 调用 Tool
        result = await self._run_tool(
            "caption_segment",
            text=text,
            words_per_segment=kwargs.get("words_per_segment", 5),
        )
        segments = (result.get("output") or {}).get("segments", [])
        total_words = len(text.split())

        return SkillExecResult(
            status=SkillStatus.SUCCESS,
            skill_name=self.name,
            output={
                "total_words": total_words,
                "total_segments": len(segments),
                "segments": segments,
                "words_per_segment": round(total_words / max(len(segments), 1), 1),
            },
            tool_calls=[{"tool": "caption_segment", "input": {"text_len": len(text)}, "output": result}],
        )


__all__ = ["ExampleCaptionPlugin"]
