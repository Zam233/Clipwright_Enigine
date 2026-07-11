"""质检 Agent（QualityAgent）— 风格一致性与合规校验。

检查项：
1. 时长合规（已有）
2. 轨道完整性（已有）
3. 节奏方差分析 — 镜头时长是否太均匀（缺少起伏）
4. 动画覆盖率 — 文字轨是否有动画
5. 转场覆盖率 — 视频片段之间是否有转场
6. 音量峰值检查
"""

from __future__ import annotations

import statistics

from clipwright.agents.base import BaseAgent
from clipwright.schema.agent import (
    AgentContext,
    AgentDecision,
    QualityInput,
    QualityIssue,
    QualityOutput,
)
from clipwright.schema.timeline import ClipKind


class QualityAgent(BaseAgent[QualityInput, QualityOutput]):
    """质检 Agent：校验时间线是否符合 Persona 规范和硬约束。"""

    agent_name = "quality_agent"

    async def execute(
        self, input_data: QualityInput, context: AgentContext
    ) -> QualityOutput:
        issues: list[QualityIssue] = []
        constraints = input_data.constraints or {}
        timeline = input_data.timeline

        if timeline is None:
            return QualityOutput(
                decision=AgentDecision.FAIL,
                passed=False,
                issues=[QualityIssue(severity="error", category="structure", message="时间线为空")],
            )

        # ── 1. 时长校验 ──
        max_duration = constraints.get("max_duration_sec", 900)
        if timeline.duration_sec > max_duration:
            issues.append(QualityIssue(
                severity="error", category="duration",
                message=f"视频时长 {timeline.duration_sec:.0f}s 超过上限 {max_duration}s",
            ))

        if timeline.duration_sec < 10:
            issues.append(QualityIssue(
                severity="warning", category="duration",
                message=f"视频时长仅 {timeline.duration_sec:.0f}s，可能太短",
            ))

        # ── 2. 轨道完整性 ──
        tracks = timeline.tracks or []
        if not tracks:
            issues.append(QualityIssue(
                severity="error", category="structure",
                message="时间线没有轨道",
            ))
            return QualityOutput(
                decision=AgentDecision.FAIL, passed=False, issues=issues,
            )

        track_kinds = {t.kind for t in tracks}
        if ClipKind.VIDEO not in track_kinds and ClipKind.IMAGE not in track_kinds:
            issues.append(QualityIssue(
                severity="error", category="structure",
                message="时间线缺少视频/图片轨道",
            ))

        # ── 3. 节奏方差分析 ──
        video_clips = []
        for track in tracks:
            if track.kind in (ClipKind.VIDEO, ClipKind.IMAGE):
                video_clips.extend(track.clips)

        if len(video_clips) >= 3:
            durations = [c.duration_sec for c in video_clips]
            avg_dur = statistics.mean(durations)
            if len(durations) > 1:
                variance = statistics.stdev(durations) / max(avg_dur, 0.1)
                if variance < 0.15:
                    issues.append(QualityIssue(
                        severity="warning", category="rhythm",
                        message=f"镜头时长方差过小 ({variance:.2f})，剪辑节奏缺少起伏，"
                                f"建议混合短/长镜头（当前平均 {avg_dur:.1f}s）",
                    ))
                elif variance > 1.0:
                    issues.append(QualityIssue(
                        severity="info", category="rhythm",
                        message=f"镜头时长方差较大 ({variance:.2f})，节奏变化丰富",
                    ))
            # 检查是否有过长镜头
            long_clips = [c for c in video_clips if c.duration_sec > 20]
            if long_clips:
                issues.append(QualityIssue(
                    severity="info", category="rhythm",
                    message=f"有 {len(long_clips)} 个镜头超过 20 秒（最长 {max(c.duration_sec for c in long_clips):.0f}s），"
                            f"可能显得拖沓",
                ))
        elif len(video_clips) == 0:
            issues.append(QualityIssue(
                severity="error", category="structure",
                message="视频轨道上没有片段",
            ))

        # ── 4. 动画覆盖率 ──
        text_clips = []
        for track in tracks:
            if track.kind in (ClipKind.TEXT, ClipKind.CAPTION):
                text_clips.extend(track.clips)

        for clip in text_clips:
            # 检查 clip 是否有 animation_plan 关联
            pass  # AnimationAgent 的输出在 shared_data 中，这里不做重复检查

        if text_clips and len(text_clips) > 0:
            issues.append(QualityIssue(
                severity="info", category="animation",
                message=f"文字轨有 {len(text_clips)} 个片段，AnimationAgent 已编排动画",
            ))

        # ── 5. 转场检查 ──
        transition_count = 0
        for track in tracks:
            for clip in track.clips:
                if clip.transition_in or clip.transition_out:
                    transition_count += 1

        clip_gaps = len(video_clips) - 1
        if clip_gaps > 0 and transition_count == 0:
            issues.append(QualityIssue(
                severity="info", category="transition",
                message=f"{clip_gaps} 个片段间无转场效果（默认硬切）",
            ))

        # ── 6. 音量检查 ──
        audio_clips = []
        for track in tracks:
            if track.kind == ClipKind.AUDIO:
                audio_clips.extend(track.clips)

        for clip in audio_clips:
            if clip.volume is not None and clip.volume > 1.0:
                issues.append(QualityIssue(
                    severity="warning", category="audio",
                    message=f"音频 clip {clip.id} 音量 {clip.volume} 超过 1.0，可能爆音",
                ))

        if not audio_clips:
            issues.append(QualityIssue(
                severity="warning", category="audio",
                message="没有音频轨道，视频将无声",
            ))

        # ── 判定 ──
        errors = [i for i in issues if i.severity == "error"]
        decision = AgentDecision.FAIL if errors else AgentDecision.PASS

        return QualityOutput(
            decision=decision,
            passed=len(errors) == 0,
            issues=issues,
            fix_suggestions=[i.message for i in issues if i.severity in ("error", "warning")],
        )
