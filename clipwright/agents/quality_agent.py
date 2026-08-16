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
8. LLM 语义质检（enable_semantic_qa 门控，默认关闭）— 文案与创意简报一致性 +
    错别字/风格；LLM 失败/超时/非 JSON 一律静默跳过（零行为变化）
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

        # C3: 质检深度默认策略 — basic（仅结构/时长/节奏，零媒体/LLM 开销）
        #   standard（默认，现有行为）/ deep（强制视觉 LLM + 语义质检）。
        # 显式设置 quality_depth 时以它为准，否则沿用各自的独立门控默认值。
        qdepth = str(constraints.get("quality_depth", "standard")).lower()
        enable_visual = constraints.get("enable_visual_llm", False)
        enable_semantic = constraints.get("enable_semantic_qa", False)
        if qdepth == "basic":
            enable_visual = enable_semantic = False
        elif qdepth == "deep":
            enable_visual = enable_semantic = True
        # standard：保持门控开关原样

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

        # ── 7. 帧级素材匹配检查（视觉 LLM 门控；C3 quality_depth 归一）──
        # 与 material_agent 共用 enable_visual_llm 开关；仅在开启时执行，
        # 避免新增一条常开视觉路径。检查结果进 _quality_issues →
        # redo_agent 建议 material 重做素材。
        if enable_visual:
            frame_issues = await self._check_frame_matches(timeline, context, constraints, enabled=True)
            issues.extend(frame_issues)

        # ── 8. LLM 语义质检（enable_semantic_qa 门控；C3 quality_depth 归一）──
        # 文案与简报一致性 + 错别字/风格；复用视觉 LLM 门控开关模式，
        # 默认关闭；LLM 失败/超时/非 JSON 静默跳过（零行为变化）。
        if enable_semantic:
            semantic_issues = await self._check_semantic_qa(
                timeline, input_data.creative_brief, context
            )
            issues.extend(semantic_issues)

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
        enabled: bool = False,
    ) -> list[QualityIssue]:
        """帧级素材匹配检查（视觉 LLM 门控）。

        Gate: ``constraints["enable_visual_llm"]`` —— 与 material_agent 的
        material_plugin_config 使用同一开关；关闭时直接返回空（不引入常开视觉路径）。
        C3: quality_depth 归一后由调用方传入 enabled（basic=关，deep=强制开）。

        开启时对有界的关键 scene（有文案 + 可提取帧源的 video/image clip，最多
        ``quality_check_max_clips`` 个）抽帧 → VisionService 分析 → 用与
        material_agent 一致的 token 重叠启发式打分；低于阈值产出
        ``material_match`` 错误问题（触发 redo_agent="material"）。
        """
        if not enabled:
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

    # ── LLM 语义质检（enable_semantic_qa 门控）─────────────

    async def _check_semantic_qa(
        self,
        timeline: Timeline,
        creative_brief: dict[str, Any] | None,
        context: AgentContext,
    ) -> list[QualityIssue]:
        """LLM 语义质检：文案与创意简报一致性 + 错别字/风格。

        Gate: ``constraints["enable_semantic_qa"]``（默认关闭）——与视觉 LLM
        （enable_visual_llm）共用同一 constraints 门控模式；关闭或 LLM
        失败/超时/非 JSON 时直接返回空（零行为变化）。

        LLM 输出仅作数据消费：severity 仅接受 error/warning/info（其余丢弃），
        category 固定为 ``semantic``，location 取首个关键片段 ID。
        """
        # 收集关键片段文案（有文案的 video/image clip，最多 3 个）
        clip_copy: list[tuple[str, str]] = []  # (clip_id, text)
        for track in timeline.tracks or []:
            if track.kind not in (ClipKind.VIDEO, ClipKind.IMAGE):
                continue
            for clip in track.clips:
                text = self._clip_expected_text(clip)
                if not text and clip.text:
                    text = str(clip.text).strip()
                if text:
                    clip_copy.append((clip.id, text))
                    if len(clip_copy) >= 3:
                        break
            if len(clip_copy) >= 3:
                break

        if not clip_copy:
            return []

        # 简报上下文（缺省时占位，避免拼接 None）
        brief = creative_brief or {}
        brief_lines = [
            f"- {key}: {value}"
            for key in ("special_requirements", "overview")
            if (value := brief.get(key)) is not None
        ]
        brief_text = "\n".join(brief_lines) or "（无简报内容）"

        clips_text = "\n".join(f"[{cid}] {text}" for cid, text in clip_copy)
        system_prompt = (
            "你是一名视频质检编辑。检查给定视频文案与创意简报的一致性、"
            "错别字与风格问题。只输出 JSON。"
        )
        user_prompt = (
            "【创意简报】\n"
            f"{brief_text}\n\n"
            "【视频文案】\n"
            f"{clips_text}\n\n"
            "请检查文案与简报的一致性、错别字与风格问题。"
            '输出格式: {"issues": [{"severity": "error|warning|info", '
            '"category": "semantic", "message": "问题描述"}]}\n'
            "其中 severity 仅可为 error / warning / info。"
        )

        try:
            from clipwright.services.llm import LLMService

            result = await asyncio.wait_for(
                LLMService().structured_output(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    use_flash=True,
                    pipeline_id=context.pipeline_id,
                ),
                timeout=30,
            )
        except Exception as e:
            # LLM 失败/超时 → 静默跳过（零行为变化）
            logger.debug("QualityAgent: 语义质检 LLM 调用失败，跳过: %s", e)
            return []

        # structured_output 已剥离 markdown 围栏并做 JSON 解析；解析失败时
        # 返回 {"content": ...}（无 issues 键），同样视为无可用结果
        raw_issues = result.get("issues") if isinstance(result, dict) else None
        if not isinstance(raw_issues, list):
            logger.debug("QualityAgent: 语义质检返回缺少 issues 列表，跳过")
            return []

        issues: list[QualityIssue] = []
        for item in raw_issues:
            if not isinstance(item, dict):
                continue
            severity = str(item.get("severity", "")).strip().lower()
            if severity not in ("error", "warning", "info"):
                continue
            message = str(item.get("message", "")).strip()
            if not message:
                continue
            issues.append(QualityIssue(
                severity=severity,
                category="semantic",
                message=message,
                location=clip_copy[0][0],
            ))

        if issues:
            from clipwright.services.trace import add_event

            add_event(
                context.pipeline_id,
                "quality",
                "llm",
                f"语义质检: {len(issues)} 条问题",
                {"issues": [i.model_dump() for i in issues]},
            )

        return issues
