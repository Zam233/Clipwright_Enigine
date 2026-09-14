"""质检 Agent（QualityAgent）— 风格一致性与合规校验。

检查项：
1. 时长合规（已有）
2. 轨道完整性（已有）
3. 节奏方差分析 — 镜头时长是否太均匀（缺少起伏）
4. 动画覆盖率 — 文字轨是否有动画
5. 转场覆盖率 — 视频片段之间是否有转场
5b. 轨道相邻性（整数 tick 精确判定）— 视频轨片段重叠 / 间隙
6. 音量峰值检查
7. 空镜头检测 — video/image 素材黑帧/全白帧（frame_validator，有界并行）
8. 动画生效检查 — renderer 降级 / mg_html 缺失 / clip 越界
9. 帧级素材匹配（视觉 LLM 门控）— 关键 scene 抽帧 → VisionService 分析 →
    与文案做 token 重叠打分，低于阈值产出 material_match 错误问题
    （触发 redo_agent="material"，复用与 material_agent 相同的 enable_visual_llm 开关）
10. LLM 语义质检（enable_semantic_qa 门控，默认关闭）— 文案与创意简报一致性 +
    错别字/风格；LLM 失败/超时/非 JSON 一律静默跳过（零行为变化）

质检 Agent 只检测与报告，绝不自动替换素材或重跑管线。
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
from clipwright.schema.timeline import Clip, ClipKind, Timeline, Track
from clipwright.tool.registry import ToolRegistry

# 空镜头检测有界抽样参数（Bounded sampling）
_MAX_FRAME_CHECK_CLIPS = 30  # 单次质检最多校验的 video/image clip 数，超限跳过并 note
_FRAME_CHECK_CONCURRENCY = 4  # frame_validator 并行度（Semaphore）
_FRAME_CHECK_TIMEOUT_SEC = 30.0  # 单 clip 帧校验超时（秒），超时记 warning
_ANIMATION_RENDERERS = ("hyperframes", "mg_hyperframes")  # 允许的动画渲染器
_FLOAT_EPS = 1e-6
# 轮 84（数码评测专项）：语义质检默认开启的类型——型号/参数/文案一致性
# 是这两类内容的发布硬要求；其余类型维持默认关闭（显式设置仍可覆盖）
_SEMANTIC_QA_DEFAULT_ON = {"digital_review", "knowledge_longform"}
_GLOSSARY_MAX_ISSUES = 10  # 词表校验最多报的问题数，防长文本刷屏


class QualityAgent(BaseAgent[QualityInput, QualityOutput]):
    """质检 Agent：校验时间线是否符合 Persona 规范和硬约束。"""

    agent_name = "quality_agent"

    async def execute(
        self, input_data: QualityInput, context: AgentContext
    ) -> QualityOutput:
        issues: list[QualityIssue] = []
        constraints = input_data.constraints or {}
        timeline = input_data.timeline

        # 质检深度默认策略 — basic（仅结构/时长/节奏，零媒体/LLM 开销）
        #   standard（默认，现有行为）/ deep（强制视觉 LLM + 语义质检）。
        # 显式设置 quality_depth 时以它为准，否则沿用各自的独立门控默认值。
        qdepth = str(constraints.get("quality_depth", "standard")).lower()
        enable_visual = constraints.get("enable_visual_llm", False)
        # 轮 84（数码评测专项）：语义质检对评测/知识类默认开启——型号/参数/文案
        # 一致性是这两类内容的发布硬要求；其余类型维持默认关闭。
        semantic_default = context.category_plugin_id in _SEMANTIC_QA_DEFAULT_ON
        enable_semantic = constraints.get("enable_semantic_qa", semantic_default)
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
                    # info → warning（客户端仅展示 error/warning 级，
                    # info 意味着用户确认的特殊要求在结果页完全不可见）
                    issues.append(QualityIssue(
                        severity="warning", category="brief_requirement",
                        message=f"简报要求（请人工核对是否落实）: {req}",
                    ))
        except Exception:
            logger.exception("简报要求核对失败（该项质检结果缺失）")

        # ── 1. 时长校验 ──
        max_duration = constraints.get("max_duration_sec", 900)
        if timeline.duration_sec > max_duration:
            issues.append(QualityIssue(
                severity="error", category="duration",
                message=f"视频时长 {timeline.duration_sec:.0f}s 超过上限 {max_duration}s",
            ))

        # 最短时长门接入 Persona constraints.min_duration_sec（旧实现硬编码 10s）
        min_duration = constraints.get("min_duration_sec", 10)
        if timeline.duration_sec < min_duration:
            issues.append(QualityIssue(
                severity="warning", category="duration",
                message=(f"视频时长仅 {timeline.duration_sec:.0f}s，"
                         f"低于 Persona 最短要求 {min_duration}s"),
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

        # ── 5b. 轨道相邻性（整数 tick 精确判定，O1 第二阶段）──
        # 视频/图片轨是**顺序拼接**：上一段终点与下一段起点必须严格相接。
        # 重叠会让裁剪/顺序对不上；间隙不会渲染成黑帧（渲染端按顺序 concat），
        # 而是让成片比时间线短——两者都是浮点秒判不准的结构问题，故用 tick 判。
        try:
            adjacency = timeline.adjacency_issues()
        except Exception as exc:  # 结构异常不应让质检整体失败
            logger.debug("QualityAgent: 相邻性检查跳过: %s", exc)
            adjacency = []
        _video_track_ids = {t.id for t in tracks
                            if t.kind in (ClipKind.VIDEO, ClipKind.IMAGE)}
        overlaps = [a for a in adjacency if a["kind"] == "overlap" and a["track_id"] in _video_track_ids]
        gaps = [a for a in adjacency if a["kind"] == "gap" and a["track_id"] in _video_track_ids]
        if overlaps:
            worst = min(overlaps, key=lambda a: a["delta_ticks"])
            issues.append(QualityIssue(
                severity="warning", category="structure",
                message=f"{len(overlaps)} 处视频片段重叠（最大 {-worst['delta_sec']:.3f}s："
                        f"{worst['prev']} 与 {worst['next']}），渲染按顺序拼接会与时间线不一致",
            ))
        if gaps:
            largest = max(gaps, key=lambda a: a["delta_ticks"])
            issues.append(QualityIssue(
                severity="info", category="structure",
                message=f"{len(gaps)} 处视频片段间隙（最大 {largest['delta_sec']:.3f}s："
                        f"{largest['prev']} 与 {largest['next']}），间隙不会渲染成黑帧，成片会变短",
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

        # ── 6b. 批2 质检补盲：字幕缺失 / 画面-音频时长不一致 ──
        try:
            has_text = any(
                t.kind in (ClipKind.CAPTION, ClipKind.TEXT) and t.clips
                for t in tracks
            )
            if not has_text:
                issues.append(QualityIssue(
                    severity="warning", category="duration",
                    message="时间线无任何字幕/文字 clip，成片将无字幕",
                ))
            video_dur = sum(
                c.duration_sec for t in tracks if t.kind == ClipKind.VIDEO
                for c in t.clips
            )
            audio_ends = [
                c.start_sec + c.duration_sec
                for t in tracks if t.kind == ClipKind.AUDIO for c in t.clips
                if not (getattr(c, "metadata", {}) or {}).get("bgm")
            ]
            audio_end = max(audio_ends) if audio_ends else 0.0
            if audio_end > 0 and abs(video_dur - audio_end) > 1.5:
                issues.append(QualityIssue(
                    severity="warning", category="duration",
                    message=(f"画面轨总长 {video_dur:.1f}s 与音频终点 {audio_end:.1f}s "
                             f"不一致（偏差 {abs(video_dur - audio_end):.1f}s）"),
                ))
        except Exception:
            logger.exception("音画时长一致性校验失败（该项质检结果缺失）")

        # ── 7. 空镜头检测（frame_validator，有界并行；不检查音频轨）──
        # 批A(R7)：受 quality_depth 门控——basic 跳过（远程逐 clip ffprobe
        # 最多 30×30s，纯校验即拖慢管线数分钟）
        if qdepth != "basic":
            await self._check_blank_shots(video_clips, issues)

        # ── 8. 动画生效检查 ──
        self._check_animations(tracks, timeline, issues)

        # ── 9. 帧级素材匹配检查（视觉 LLM 门控；C3 quality_depth 归一）──
        # 与 material_agent 共用 enable_visual_llm 开关；仅在开启时执行，
        # 避免新增一条常开视觉路径。检查结果进 _quality_issues →
        # redo_agent 建议 material 重做素材。
        if enable_visual:
            frame_issues = await self._check_frame_matches(timeline, context, constraints, enabled=True)
            issues.extend(frame_issues)

        # ── 10. LLM 语义质检（enable_semantic_qa 门控；C3 quality_depth 归一）──
        # 文案与简报一致性 + 错别字/风格；复用视觉 LLM 门控开关模式，
        # 默认关闭；LLM 失败/超时/非 JSON 静默跳过（零行为变化）。
        if enable_semantic:
            # 批D(D3)：proceed 复用路径下简报摘要随 script_skeleton.brief
            # 流转——creative_brief 缺失（raw /run-async）时回退使用
            _brief_for_qa = input_data.creative_brief
            if not _brief_for_qa:
                _sk = getattr(input_data, "script_skeleton", None) or {}
                _sk_brief = _sk.get("brief") if isinstance(_sk, dict) else None
                if isinstance(_sk_brief, dict) and _sk_brief:
                    _brief_for_qa = _sk_brief
            semantic_issues = await self._check_semantic_qa(
                timeline, _brief_for_qa, context
            )
            issues.extend(semantic_issues)

        # ── 11. 术语词表校验（轮 84 数码评测专项；glossary 非空才执行）──
        # 型号/参数/品牌词拼写是评测类发布的硬约束；纯确定性检查（编辑距离
        # ≤2 的近似词 → error），零 LLM 开销。
        glossary = context.extra_params.get("glossary") or []
        if isinstance(glossary, list) and glossary:
            issues.extend(self._check_glossary(timeline, [str(t) for t in glossary if str(t).strip()]))

        # ── 判定 ──
        errors = [i for i in issues if i.severity == "error"]
        decision = AgentDecision.FAIL if errors else AgentDecision.PASS

        # 依据 error 类别建议重做的 Agent（取最上游责任方，下游会联动重做）：
        #   material / material_match → material（重新选材，配合重选循环）
        #   structure/duration/rhythm → edit（重建粗剪时间线）
        #   semantic → edit（C6: 文案错别字/简报偏差多源于文案与结构，映射 edit；
        #              原映射缺失使语义错误既不触发自愈也不令管线失败）
        #   animation/transition      → animation
        #   audio                     → audio
        # 修复：删除复制粘贴产生的重复块——旧实现 audio 分支立即被
        # redo_agent="edit" 覆盖（audio 类 error 会错误重做 edit），且后两个
        # 分支永不可达。保留单一映射表。
        redo_agent = ""
        error_cats = {i.category for i in errors}
        if error_cats & {"material", "material_match"}:
            redo_agent = "material"
        elif error_cats & {"structure", "duration", "rhythm", "semantic"}:
            redo_agent = "edit"
        elif error_cats & {"animation", "transition"}:
            redo_agent = "animation"
        elif "audio" in error_cats:
            redo_agent = "audio"

        # 轮 84（评分闭环）：确定性 0-100 质量分（error -25 / warning -8 / info -2）
        score = self._compute_score(issues)

        return QualityOutput(
            decision=decision,
            passed=len(errors) == 0,
            issues=issues,
            fix_suggestions=[i.message for i in issues if i.severity in ("error", "warning")],
            redo_agent=redo_agent,
            score=score,
        )

    @staticmethod
    def _compute_score(issues: list[QualityIssue]) -> int:
        """由 issue 列表推导 0-100 质量分（确定性权重，无 LLM）。

        权重：error -25（致命缺陷）、warning -8（观感缺陷）、info -2（提示）。
        passed 判定不使用该分数——分数是审阅参考与可选门槛
        （extra_params.quality_min_score，默认 0 = 不启用）。
        """
        penalty = sum(
            -25 if i.severity == "error" else (-8 if i.severity == "warning" else -2)
            for i in issues
        )
        return max(0, min(100, 100 + penalty))

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
            from clipwright.agents.base import unified_llm_call
            from clipwright.services.llm import LLMService

            result = await unified_llm_call(
                "QualityAgent.semantic_qa",
                lambda: LLMService().structured_output(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    use_flash=True,
                    pipeline_id=context.pipeline_id,
                ),
                retries=1, timeout=30,
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

    # ── 空镜头检测 ──

    @staticmethod
    def _clip_media_path(clip: Clip) -> str:
        """解析 clip 的本地/远程媒体路径（metadata 优先，回退 asset_id）。"""
        meta = clip.metadata or {}
        path = meta.get("local_path") or meta.get("url") or clip.asset_id or ""
        return str(path).strip()

    # ── 11. 术语词表校验（轮 84 数码评测专项）──────────────

    @staticmethod
    def _levenshtein(a: str, b: str, cap: int = 3) -> int:
        """编辑距离（带提前截断上限，避免长串无谓计算）。"""
        if abs(len(a) - len(b)) > cap:
            return cap + 1
        prev = list(range(len(b) + 1))
        for i, ca in enumerate(a, 1):
            cur = [i]
            for j, cb in enumerate(b, 1):
                cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
            if min(cur) > cap:
                return cap + 1
            prev = cur
        return prev[-1]

    def _check_glossary(self, timeline: Timeline, terms: list[str]) -> list[QualityIssue]:
        """词表校验：字幕/文字轨中出现词表术语的「近似变体」→ error。

        评测类视频的型号/参数拼写是发布硬约束（如 RTX 5080 写成 5080Ti）。
        确定性检查：精确命中放行；编辑距离 ≤2 的滑窗片段（词长 ≥4）判疑似
        错误。纯文本计算，零 LLM/媒体开销。最多报 _GLOSSARY_MAX_ISSUES 条。
        """
        issues: list[QualityIssue] = []
        if timeline is None or not terms:
            return issues
        texts: list[str] = []
        for t in timeline.tracks:
            if t.kind not in (ClipKind.CAPTION, ClipKind.TEXT):
                continue
            for c in t.clips:
                txt = (getattr(c, "text", "") or "").strip()
                if txt:
                    texts.append(txt)
        if not texts:
            return issues

        import re as _re

        for term in terms:
            if len(term) < 3:
                continue
            if any(term in txt for txt in texts):
                continue  # 精确命中
            # 滑窗近似匹配：短词（≤3 字）误报率高，仅允许距离 1
            max_dist = 1 if len(term) <= 3 else 2
            lo, hi = len(term) - max_dist, len(term) + max_dist
            for txt in texts:
                found = None
                # 从最长窗口降序匹配：报告最接近词表词形的片段（如「安兔免」
                # 而非其前缀「安兔」）
                for size in range(hi, max(2, lo) - 1, -1):
                    for start in range(0, max(1, len(txt) - size + 1)):
                        token = txt[start:start + size]
                        if token == term or not token.strip():
                            continue
                        # 词边界检查：窗口边缘紧贴 ASCII 字母/数字时视为更长
                        # 单词的内部片段（如 "RTX 50" ⊂ "RTX 5080Ti"），跳过，
                        # 避免把正确产品名的子串误判为近似词
                        prev_ch = txt[start - 1] if start > 0 else ""
                        next_ch = txt[start + size] if start + size < len(txt) else ""
                        if (prev_ch.isascii() and prev_ch.isalnum()) or (
                            next_ch.isascii() and next_ch.isalnum()
                        ):
                            continue
                        if self._levenshtein(token, term, cap=max_dist) <= max_dist:
                            found = token
                            break
                    if found:
                        break
                if found:
                    issues.append(QualityIssue(
                        severity="error", category="glossary",
                        message=(f"术语疑似拼写错误：文本出现「{found}」，"
                                 f"词表术语为「{term}」"),
                    ))
                    break
            if len(issues) >= _GLOSSARY_MAX_ISSUES:
                break
        return issues

    async def _check_blank_shots(
        self, video_clips: list[Clip], issues: list[QualityIssue]
    ) -> None:
        """空镜头检测：并行有界调用 frame_validator，is_blank/is_white → error(material)。

        工具失败/超时 → warning（不升级为 error，避免误判）；单次最多校验 30 个
        clip，超出部分跳过并附 note。只校验 video/image 轨内容，不检查音频轨。
        """
        if not video_clips:
            return

        # 轮 81：出片硬门——全占位时间线（asset_id 为空的文字/纯色占位）此前
        # 一路 COMPLETED。占位占比过高 → error 级，接入自愈/门禁阻止「全占位
        # 成片当成功交付」。
        placeholders = [c for c in video_clips if not (c.asset_id or "").strip()]
        if video_clips and len(placeholders) / len(video_clips) > 0.5:
            issues.append(QualityIssue(
                severity="error", category="material",
                message=(f"时间线中 {len(placeholders)}/{len(video_clips)} 个画面片段为无素材占位"
                         "（素材检索失败或素材不可用），成片将为文字/纯色占位；"
                         "请检查素材源配置或 API Key"),
            ))

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
