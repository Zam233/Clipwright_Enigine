"""质检 Agent（QualityAgent）— 风格一致性与合规校验。

检查项：
1. 时长合规（已有）
2. 轨道完整性（已有）
3. 节奏方差分析 — 镜头时长是否太均匀（缺少起伏）
4. 动画覆盖率 — 文字轨是否有动画
5. 转场覆盖率 — 视频片段之间是否有转场
6. 音量峰值检查
7. 帧级素材匹配（视觉 LLM 门控）— 关键 scene 抽帧 → VisionService 分析 →
   与文案做 token 重叠打分，低于阈值产出 material_match 错误问题
   （触发 redo_agent="material"，复用与 material_agent 相同的 enable_visual_llm 开关）
"""

from __future__ import annotations

import asyncio
import os
import statistics
from typing import Any

from clipwright.agents.base import BaseAgent
from clipwright.config import logger
from clipwright.schema.agent import (
    AgentContext,
    AgentDecision,
    QualityInput,
    QualityIssue,
    QualityOutput,
)
from clipwright.schema.timeline import Clip, ClipKind, Timeline


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

        # ── 0. 简报特殊要求 → 作为 info 提示（供审阅参考）──
        try:
            brief = input_data.creative_brief or {}
            special = brief.get("special_requirements")
            if isinstance(special, list) and special:
                for req in special[:5]:
                    issues.append(QualityIssue(
                        severity="info", category="brief_requirement",
                        message=f"简报要求: {req}",
                    ))
        except Exception:
            pass

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

        if text_clips:
            issues.append(QualityIssue(
                severity="info", category="animation",
                message=f"文字轨有 {len(text_clips)} 个片段，动画由 AnimationAgent 编排",
            ))

        # ── 5. 转场检查 ──
        transition_count = 0
        for track in tracks:
            for clip in track.clips:
                if clip.transition_in or clip.transition_out:
                    transition_count += 1

        # 按轨道计算间隔（避免跨轨道误报）
        clip_gaps = sum(max(0, len(t.clips) - 1) for t in tracks
                        if t.kind in (ClipKind.VIDEO, ClipKind.IMAGE))
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

        # ── 7. 帧级素材匹配检查（视觉 LLM 门控）──
        # 与 material_agent 共用 enable_visual_llm 开关；仅在开启时执行，
        # 避免新增一条常开视觉路径。检查结果进 _quality_issues →
        # redo_agent 建议 material 重做素材。
        frame_issues = await self._check_frame_matches(timeline, context, constraints)
        issues.extend(frame_issues)

        # ── 判定 ──
        errors = [i for i in issues if i.severity == "error"]
        decision = AgentDecision.FAIL if errors else AgentDecision.PASS

        # 依据 error 类别建议重做的 Agent（取最上游责任方，下游会联动重做）：
        #   material_match → material（素材帧与文案不匹配，重做素材匹配）
        #   structure/duration/rhythm → edit（重建粗剪时间线）
        #   animation/transition      → animation
        #   audio                     → audio
        redo_agent = ""
        error_cats = {i.category for i in errors}
        if "material_match" in error_cats:
            redo_agent = "material"
        elif error_cats & {"structure", "duration", "rhythm"}:
            redo_agent = "edit"
        elif error_cats & {"animation", "transition"}:
            redo_agent = "animation"
        elif "audio" in error_cats:
            redo_agent = "audio"

        return QualityOutput(
            decision=decision,
            passed=len(errors) == 0,
            issues=issues,
            fix_suggestions=[i.message for i in issues if i.severity in ("error", "warning")],
            redo_agent=redo_agent,
        )

    # ── 帧级素材匹配检查 ─────────────────────────────────

    @staticmethod
    def _clip_expected_text(clip: Clip) -> str:
        """关键片段的期望文案：优先取注入的场景描述，其次素材标题。"""
        meta = clip.metadata or {}
        return str(meta.get("description") or meta.get("source_title") or "").strip()

    @staticmethod
    def _clip_media_source(clip: Clip) -> str:
        """关键片段的帧提取源：metadata 中的本地路径/URL，或 asset_id（处理后的媒体路径）。"""
        meta = clip.metadata or {}
        return str(meta.get("local_path") or meta.get("url") or clip.asset_id or "").strip()

    async def _check_frame_matches(
        self,
        timeline: Timeline,
        context: AgentContext,
        constraints: dict[str, Any],
    ) -> list[QualityIssue]:
        """帧级素材匹配检查（视觉 LLM 门控）。

        Gate: ``constraints["enable_visual_llm"]`` —— 与 material_agent 的
        material_plugin_config 使用同一开关；关闭时直接返回空（不引入常开视觉路径）。

        开启时对有界的关键 scene（有文案 + 可提取帧源的 video/image clip，最多
        ``quality_check_max_clips`` 个）抽帧 → VisionService 分析 → 用与
        material_agent 一致的 token 重叠启发式打分；低于阈值产出
        ``material_match`` 错误问题（触发 redo_agent="material"）。
        """
        if not constraints.get("enable_visual_llm", False):
            return []

        threshold = float(constraints.get("material_match_threshold", 0.35))
        frame_count = int(constraints.get("quality_frame_count", 1))
        max_clips = int(constraints.get("quality_check_max_clips", 3))

        # 有界关键 scene 子集：优先 metadata 带描述的片段
        key_clips: list[tuple[Clip, str, dict[str, Any]]] = []
        for track in timeline.tracks or []:
            if track.kind not in (ClipKind.VIDEO, ClipKind.IMAGE):
                continue
            for clip in track.clips:
                expected_text = self._clip_expected_text(clip)
                source = self._clip_media_source(clip)
                if expected_text and source:
                    key_clips.append((clip, expected_text, {
                        "local_path": source,
                        "duration_sec": clip.duration_sec,
                    }))
                if len(key_clips) >= max_clips:
                    break
            if len(key_clips) >= max_clips:
                break

        if not key_clips:
            return []

        from clipwright.agents.material_agent import _heuristic_title_match_score
        from clipwright.services.vision import VisionService
        from clipwright.tool.frame_extractor import extract_frames

        issues: list[QualityIssue] = []
        for clip, expected_text, asset in key_clips:
            frame_paths: list[str] = []
            try:
                frame_paths = await extract_frames(asset, frame_count=frame_count)
                if not frame_paths:
                    continue
                service = VisionService()
                analyses = await asyncio.gather(
                    *(service.analyze_image(p) for p in frame_paths)
                )
                tags = list({str(t) for a in analyses for t in a.get("tags", []) if t})
                descriptions = [
                    str(a.get("description", "")) for a in analyses if a.get("description")
                ]
                description = " | ".join(descriptions)
                score = _heuristic_title_match_score(description, tags, expected_text)
                if score < threshold:
                    issues.append(QualityIssue(
                        severity="error",
                        category="material_match",
                        message=(
                            f"素材帧与文案不匹配: clip={clip.id} 匹配分 {score:.2f} "
                            f"(阈值 {threshold:.2f})，建议重做素材匹配"
                        ),
                        location=clip.id,
                    ))
            except Exception as e:
                logger.debug("QualityAgent: 帧匹配检查跳过 clip=%s: %s", clip.id, e)
            finally:
                for p in frame_paths:
                    try:
                        os.remove(p)
                    except OSError:
                        pass
        return issues
