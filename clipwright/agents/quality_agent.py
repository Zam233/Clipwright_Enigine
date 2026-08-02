"""质检 Agent（QualityAgent）— 风格一致性与合规校验。

检查项：
1. 时长合规（已有）
2. 轨道完整性（已有）
3. 节奏方差分析 — 镜头时长是否太均匀（缺少起伏）
4. 动画覆盖率 — 文字轨是否有动画
5. 转场覆盖率 — 视频片段之间是否有转场
6. 音量峰值检查
7. 空镜头检测 — video/image 素材黑帧/全白帧（frame_validator，有界并行）
8. 动画生效检查 — renderer 降级 / mg_html 缺失 / clip 越界

质检 Agent 只检测与报告，绝不自动替换素材或重跑管线。
"""

from __future__ import annotations

import asyncio
import statistics

from clipwright.agents.base import BaseAgent
from clipwright.schema.agent import (
    AgentContext,
    AgentDecision,
    QualityInput,
    QualityIssue,
    QualityOutput,
)
from clipwright.schema.timeline import Clip, ClipKind, Timeline, Track
from clipwright.tool.registry import ToolRegistry

# 空镜头检测有界抽样参数（Bounded sampling）
_MAX_FRAME_CHECK_CLIPS = 30  # 单次质检最多校验的 video/image clip 数，超限跳过并 note
_FRAME_CHECK_CONCURRENCY = 4  # frame_validator 并行度（Semaphore）
_FRAME_CHECK_TIMEOUT_SEC = 30.0  # 单 clip 帧校验超时（秒），超时记 warning
_ANIMATION_RENDERERS = ("hyperframes", "mg_hyperframes")  # 允许的动画渲染器
_FLOAT_EPS = 1e-6


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
                    message=f"有 {len(long_clips)} 个镜头超过 20 秒"
                            f"（最长 {max(c.duration_sec for c in long_clips):.0f}s），"
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

        # ── 7. 空镜头检测（frame_validator，有界并行；不检查音频轨）──
        await self._check_blank_shots(video_clips, issues)

        # ── 8. 动画生效检查 ──
        self._check_animations(tracks, timeline, issues)

        # ── 判定 ──
        errors = [i for i in issues if i.severity == "error"]
        decision = AgentDecision.FAIL if errors else AgentDecision.PASS

        # 依据 error 类别建议重做的 Agent（取最上游责任方，下游会联动重做）：
        #   material                  → material（重新选材，配合 MaterialAgent 重选循环）
        #   structure/duration/rhythm → edit（重建粗剪时间线）
        #   animation/transition      → animation
        #   audio                     → audio
        redo_agent = ""
        error_cats = {i.category for i in errors}
        if "material" in error_cats:
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

    # ── 空镜头检测 ──

    @staticmethod
    def _clip_media_path(clip: Clip) -> str:
        """解析 clip 的本地/远程媒体路径（metadata 优先，回退 asset_id）。"""
        meta = clip.metadata or {}
        path = meta.get("local_path") or meta.get("url") or clip.asset_id or ""
        return str(path).strip()

    async def _check_blank_shots(
        self, video_clips: list[Clip], issues: list[QualityIssue]
    ) -> None:
        """空镜头检测：并行有界调用 frame_validator，is_blank/is_white → error(material)。

        工具失败/超时 → warning（不升级为 error，避免误判）；单次最多校验 30 个
        clip，超出部分跳过并附 note。只校验 video/image 轨内容，不检查音频轨。
        """
        if not video_clips:
            return
        to_check = video_clips[:_MAX_FRAME_CHECK_CLIPS]
        if len(video_clips) > _MAX_FRAME_CHECK_CLIPS:
            issues.append(QualityIssue(
                severity="info", category="material",
                message=f"素材数量超过单次质检上限 {_MAX_FRAME_CHECK_CLIPS}，"
                        f"跳过 {len(video_clips) - _MAX_FRAME_CHECK_CLIPS} 个未校验",
            ))

        sem = asyncio.Semaphore(_FRAME_CHECK_CONCURRENCY)

        async def _check_one(clip: Clip) -> None:
            async with sem:
                path = self._clip_media_path(clip)
                if not path:
                    return  # 无路径无法校验 → 跳过
                try:
                    result = await asyncio.wait_for(
                        ToolRegistry.execute("frame_validator", video_url=path),
                        timeout=_FRAME_CHECK_TIMEOUT_SEC,
                    )
                except asyncio.TimeoutError:
                    issues.append(QualityIssue(
                        severity="warning", category="material",
                        message=f"素材 {clip.id} 帧校验超时，跳过校验",
                        location=clip.id,
                    ))
                    return
                except Exception as exc:  # noqa: BLE001 — 工具异常按 warning 处理
                    issues.append(QualityIssue(
                        severity="warning", category="material",
                        message=f"素材 {clip.id} 帧校验失败: {str(exc)[:120]}",
                        location=clip.id,
                    ))
                    return
                output = (result.output or {}) if getattr(result, "output", None) else {}
                if not output.get("valid"):
                    issues.append(QualityIssue(
                        severity="warning", category="material",
                        message=f"素材 {clip.id} 帧校验不可用"
                                f"（{str(output.get('error', 'unknown'))[:120]}）",
                        location=clip.id,
                    ))
                    return
                if output.get("is_blank") or output.get("is_white"):
                    issues.append(QualityIssue(
                        severity="error", category="material",
                        message=f"素材 {clip.id} 为空镜头/全白帧，需重新选材",
                        location=clip.id,
                    ))

        await asyncio.gather(*(_check_one(c) for c in to_check))

    # ── 动画生效检查 ──

    def _check_animations(
        self, tracks: list[Track], timeline: Timeline, issues: list[QualityIssue]
    ) -> None:
        """动画生效检查：renderer 降级 / mg_html 缺失 → warning(animation)；越界 → warning。

        只检查 animation 轨 clip；质检只报告，不自动替换。
        """
        for track in tracks:
            if track.kind != ClipKind.ANIMATION:
                continue
            for clip in track.clips:
                meta = clip.metadata or {}
                renderer = str(meta.get("renderer", "") or "").strip() or "drawtext"
                reason = str(meta.get("mg_fallback_template") or "hyperframes 不可用/降级")
                if renderer not in _ANIMATION_RENDERERS:
                    issues.append(QualityIssue(
                        severity="warning", category="animation",
                        message=f"动画 clip {clip.id} 未走 hyperframes 渲染"
                                f"（renderer={renderer}），已降级: {reason}",
                        location=clip.id,
                    ))
                elif renderer == "mg_hyperframes" and not str(
                    meta.get("mg_html", "") or ""
                ).strip():
                    issues.append(QualityIssue(
                        severity="warning", category="animation",
                        message=f"动画 clip {clip.id} mg_hyperframes 缺少 mg_html"
                                f"（{reason}），动画可能未生效",
                        location=clip.id,
                    ))
                # 越界检查
                end = clip.start_sec + clip.duration_sec
                if clip.start_sec < 0 or end > timeline.duration_sec + _FLOAT_EPS:
                    issues.append(QualityIssue(
                        severity="warning", category="animation",
                        message=f"动画 clip {clip.id} 越界"
                                f"（{clip.start_sec:.1f}s~{end:.1f}s 超出时间线 "
                                f"{timeline.duration_sec:.1f}s）",
                        location=clip.id,
                    ))
