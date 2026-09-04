"""音效 Agent（AudioAgent）— BGM 匹配与混音。

输入：带动画的时间线 + Persona 音频参数
输出：混音后的时间线（含 BGM 参考 + 音量包络）
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from clipwright.agents.base import BaseAgent, uid as _uid
from clipwright.config import logger, settings
from clipwright.material.registry import MaterialRegistry
from clipwright.schema.agent import (
    AgentContext,
    AgentDecision,
    AudioInput,
    AudioOutput,
)
from clipwright.schema.timeline import Clip, ClipKind, Timeline, Track
from clipwright.services.llm import LLMService
from clipwright.tool.registry import ToolRegistry


async def _search_bgm_from_library(bgm_slots: dict, top_k: int = 3) -> list[dict]:
    """从素材库检索 BGM 音频（A1）。

    先以 "music" 通用词检索，再按 bgm_slots 槽位风格词补充搜索；
    仅保留 MaterialType.AUDIO 结果，按分数降序去重。
    素材库为空 / 无音频结果 / 任何异常一律返回 []，
    由调用方回退原有 bgm_slots 规则（行为零改动）。
    """
    try:
        from clipwright.schema.material import MaterialType

        if not MaterialRegistry.list():
            return []
        queries = ["music"]
        for v in bgm_slots.values():
            for item in (v if isinstance(v, list) else [v]):
                if isinstance(item, str) and item.strip():
                    queries.append(item.strip())
        seen: dict[str, dict] = {}
        for q in queries[:5]:
            results = await MaterialRegistry.search(q, top_k_per_source=top_k)
            for r in results:
                asset = r.asset
                if getattr(asset, "type", None) == MaterialType.AUDIO:
                    key = (
                        getattr(asset, "id", None)
                        or getattr(asset, "url", None)
                        or getattr(asset, "title", "")
                    )
                    if key and key not in seen:
                        seen[key] = {"asset": asset, "score": r.score}
        return sorted(seen.values(), key=lambda x: x["score"], reverse=True)[:top_k]
    except Exception:
        return []


class AudioAgent(BaseAgent[AudioInput, AudioOutput]):
    """音效 Agent：BGM 匹配、混音编排。"""

    agent_name = "audio_agent"

    def __init__(self) -> None:
        super().__init__()
        self._llm = LLMService()

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

            # 1b. 简报 BGM 需求 → 记录到 BGM 建议（供后续 BGM 检索/混音参考）
            brief_bgm = ""
            try:
                brief = input_data.creative_brief or {}
                bgm_req = brief.get("bgm_requirement")
                if isinstance(bgm_req, dict):
                    bgm_req = " ".join(str(v) for v in bgm_req.values() if v)
                if bgm_req:
                    brief_bgm = str(bgm_req)[:300]
                    notes.append(f"简报 BGM 需求: {brief_bgm}")
            except Exception:
                pass

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

            # 2b. 若用户上传了配音文件（audio_path），将其作为音频 clip 铺满整个配音时长，
            #     使时间轴总长锚定到配音实际长度（而非脚本估算值）。
            audio_path = context.extra_params.get("audio_path", "")
            audio_duration = float(context.extra_params.get("audio_duration_sec", 0) or 0)
            if audio_path and audio_duration > 0:
                has_dub = any(
                    getattr(c, "metadata", {}).get("dubbing") for c in audio_track.clips
                )
                if not has_dub:
                    dub_clip = Clip(
                        id=f"dub_{uuid.uuid4().hex[:8]}",
                        kind=ClipKind.AUDIO,
                        asset_id=audio_path,
                        track_id=audio_track.id,
                        start_sec=0.0,
                        duration_sec=audio_duration,
                        volume=1.0,
                        eq_preset="voice",
                        metadata={"dubbing": True, "source": "upload"},
                    )
                    audio_track.clips.insert(0, dub_clip)
                    if timeline.duration_sec < audio_duration:
                        timeline.duration_sec = audio_duration
                    notes.append(f"已导入配音文件: {audio_duration:.0f}s")

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

            # 3b. 素材库 BGM 检索（A1）：优先用素材库音频填充 BGM；
            #     无素材源/无音频结果/失败一律回退原有 bgm_slots 规则（零改动）。
            library_bgm: list[dict] = []
            try:
                if MaterialRegistry.list():
                    library_bgm = await _search_bgm_from_library(bgm_slots)
            except Exception:
                library_bgm = []
            if library_bgm:
                lib_title = (getattr(library_bgm[0]["asset"], "title", "") or "").strip()
                notes.append(f"BGM 来自素材库: {lib_title or '未命名素材'}")

            # 4. 标记 BGM 建议到音频 clip 的 metadata
            # 4a. LLM 情绪匹配（A2）：按场景情绪推荐 BGM 槽位风格 + 音量包络 + 停顿；
            #     任何失败/不可用都回退 `_match_bgm_slot` 规则（与无 LLM 时行为一致）。
            llm_alloc: dict[str, Any] = {}
            try:
                scenes_emotions = self._collect_scenes_emotions(input_data, timeline)
                if scenes_emotions:
                    llm_alloc = await self._llm_match_bgm(
                        scenes_emotions, bgm_slots, context.pipeline_id
                    )
                    allocs = llm_alloc.get("allocations", [])
                    if allocs:
                        notes.append(f"LLM BGM 情绪匹配: {len(allocs)} 个槽位")
            except Exception as e:
                logger.warning("AudioAgent: LLM BGM 匹配异常，回退规则: %s", e)
                llm_alloc = {}

            for i, clip in enumerate(audio_track.clips):
                clip.volume = clip.volume if clip.volume is not None else 0.7
                meta: dict[str, Any] = {
                    "bpm": bpm,
                    "bgm_slot": self._match_bgm_slot(
                        clip.start_sec, timeline.duration_sec, bgm_slots
                    ),
                }
                # A1: 素材库音频 → 填充 bgm_style / bgm_library（无结果时零改动）
                if library_bgm:
                    lib_asset = library_bgm[i % len(library_bgm)]["asset"]
                    lib_title = (getattr(lib_asset, "title", "") or "").strip()
                    meta["bgm_library"] = {
                        "title": lib_title,
                        "url": getattr(lib_asset, "url", None),
                        "local_path": getattr(lib_asset, "local_path", None),
                    }
                    meta["bgm_style"] = (lib_title or "library music")[:200]
                allocation = self._allocation_for_clip(
                    llm_alloc, clip.start_sec, timeline.duration_sec, bgm_slots
                )
                if allocation is not None:
                    if allocation.get("style"):
                        meta["bgm_style"] = allocation["style"]
                    if allocation.get("volume_envelope"):
                        meta["volume_envelope"] = allocation["volume_envelope"]
                    if allocation.get("pause_design"):
                        meta["pause_design"] = allocation["pause_design"]
                clip.metadata = {
                    **getattr(clip, "metadata", {}),
                    **meta,
                }

            # 5. 淡入淡出（B6: 原 hack 将首个 clip 音量硬压 0.3——上传配音插在
            # index 0 时全片人声只剩 30%，且无真正淡出。改用 Clip.audio_fade_in/out_sec
            # 写入真实 afade 曲线：配音短淡入防爆音，BGM 淡入 1s/淡出 2s）
            for _clip in audio_track.clips:
                _meta = getattr(_clip, "metadata", {}) or {}
                _is_voice = bool(_meta.get("dubbing") or _meta.get("narration"))
                if _is_voice:
                    _clip.audio_fade_in_sec = 0.3
                    _clip.audio_fade_out_sec = 0.5
                else:
                    _clip.audio_fade_in_sec = 1.0
                    _clip.audio_fade_out_sec = 2.0

            # 5b. C1: 素材库 BGM 实际入轨——此前 BGM 元数据只是"建议"（bgm_library
            # 挂在 asset_id 为空的占位 clip 上，render 只混音有真实文件路径的
            # clip），成片从无 BGM。有素材库音频时创建真实 BGM clip（低音量）铺满
            # 时间线，render 正常混音。
            if library_bgm:
                _tl_dur = float(getattr(timeline, "duration_sec", 0) or 0)
                _has_real_bgm = any(
                    (getattr(c, "asset_id", "") or "")
                    and not (getattr(c, "metadata", {}) or {}).get("dubbing")
                    and not (getattr(c, "metadata", {}) or {}).get("narration")
                    for t in timeline.tracks if t.kind == ClipKind.AUDIO
                    for c in t.clips
                )
                if _tl_dur > 0 and not _has_real_bgm:
                    _bgm_cursor = 0.0
                    _bgm_count = 0
                    _bgm_idx = 0
                    while _bgm_cursor < _tl_dur - 0.5:
                        _entry = library_bgm[_bgm_idx % len(library_bgm)]
                        _bgm_idx += 1
                        _asset = _entry.get("asset")
                        _a_path = (getattr(_asset, "local_path", None)
                                   or getattr(_asset, "url", None) or "")
                        if not _a_path:
                            continue
                        try:
                            _a_dur = float(getattr(_asset, "duration_sec", 0) or 0)
                        except (TypeError, ValueError):
                            _a_dur = 0.0
                        _seg = min(_a_dur if _a_dur > 0 else _tl_dur, _tl_dur - _bgm_cursor)
                        if _seg <= 0.5:
                            break
                        audio_track.clips.append(Clip(
                            id=f"bgm_{uuid.uuid4().hex[:8]}",
                            kind=ClipKind.AUDIO,
                            asset_id=_a_path,
                            track_id=audio_track.id,
                            start_sec=round(_bgm_cursor, 3),
                            duration_sec=round(_seg, 3),
                            volume=0.25,
                            audio_fade_in_sec=1.0,
                            audio_fade_out_sec=2.0,
                            metadata={"bgm": True, "source": "library"},
                        ))
                        _bgm_count += 1
                        _bgm_cursor += _seg
                    if _bgm_count:
                        notes.append(f"素材库 BGM 已入轨: {_bgm_count} 段（volume=0.25）")

            # 6. 如果没有音频 clip，添加一个占位 BGM 建议
            if not audio_track.clips:
                first_bgm = next(iter(bgm_slots.values()), [""])[0] if bgm_slots else ""
                if library_bgm:
                    first_bgm = (getattr(library_bgm[0]["asset"], "title", "") or "").strip()
                notes.append(f"建议 BGM: {first_bgm or '未配置'}" if first_bgm else "无 BGM 配置")

            notes.append(f"音频配置: voice={voice_model or '默认'}, BPM模式={bpm_mode}")
            notes.append(f"BGM 槽位: {len(bgm_slots)} 个")

            # ── 7. 自动配音（无人声配音时触发）──
            try:
                voice_id = audio_config.get("voice_id", "")
                auto_dub = bool(audio_config.get("auto_dub", True))
                script_text = context.extra_params.get("script_text", "")
                video_mode = context.extra_params.get("video_mode", "")
                # B7: 已上传配音（audio_path）时跳过 TTS——原 has_dub（配音插入）
                # 与 has_narration（TTS 门控）互不感知，两者同时满足会把上传配音
                # 与整条 TTS 旁白同时混音，成片出现双语音叠加。
                uploaded_dub = bool(audio_path)
                if uploaded_dub and auto_dub and voice_id and script_text.strip() and video_mode == "voiceover":
                    notes.append("已提供上传配音，跳过 TTS 旁白生成（避免双语音叠加）")

                # 门控：voiceover 模式 + auto_dub + 有 voice_id + 有文案 + 无上传配音
                if (
                    auto_dub
                    and voice_id
                    and script_text.strip()
                    and video_mode == "voiceover"
                    and not uploaded_dub
                ):
                    # 检查是否已有旁白轨（避免重复）
                    has_narration = any(
                        any(
                            getattr(c, "metadata", {}).get("narration")
                            for c in t.clips
                        )
                        for t in timeline.tracks
                    )

                    if not has_narration:
                        from clipwright.skill.registry import SkillRegistry

                        res = await SkillRegistry.execute(
                            "dub_script",
                            voice_id=voice_id,
                            text=script_text,
                            split_mode="sentence",
                        )

                        if res.status == "success":
                            segments = res.output.get("segments", [])
                            if segments:
                                # 查找或创建旁白轨
                                narr_track = None
                                for t in timeline.tracks:
                                    if t.id == "a_narration":
                                        narr_track = t
                                        break
                                if narr_track is None:
                                    narr_track = Track(
                                        id="a_narration",
                                        name="旁白 TTS",
                                        kind=ClipKind.AUDIO,
                                        index=len(timeline.tracks),
                                    )
                                    timeline.tracks.append(narr_track)

                                # 按顺序铺设旁白 clip
                                # B9: 失败分段（无音频文件/零时长）不入轨——原实现
                                # 零长 clip 照样入轨并推进 cursor，字幕按成功段重排
                                # 后与实际音频系统性漂移
                                cursor = 0.0
                                _skipped_segments = 0
                                for idx, seg in enumerate(segments):
                                    dur = float(seg.get("duration_sec", 0) or 0)
                                    _seg_audio = (seg.get("audio_path", "") or "").strip()
                                    if dur <= 0 or not _seg_audio:
                                        _skipped_segments += 1
                                        continue
                                    clip = Clip(
                                        id=f"narr_{idx}_{uuid.uuid4().hex[:6]}",
                                        kind=ClipKind.AUDIO,
                                        asset_id=_seg_audio,
                                        track_id="a_narration",
                                        start_sec=cursor,
                                        duration_sec=dur,
                                        volume=1.0,
                                        eq_preset="voice",
                                        metadata={
                                            "narration": True,
                                            "text": seg.get("text", ""),
                                            "voice_id": voice_id,
                                            "seed": seg.get("seed"),
                                        },
                                    )
                                    narr_track.clips.append(clip)
                                    cursor += dur
                                if _skipped_segments:
                                    notes.append(f"TTS 失败分段已跳过: {_skipped_segments} 段（零时长/无音频）")

                                # 更新时间线总时长（如需）
                                if timeline.duration_sec < cursor:
                                    timeline.duration_sec = cursor

                                # 生产加固 2.2：字幕轨按实测旁白窗口重建。
                                # EditAgent 先于 AudioAgent 运行，字幕是字数比例估算；
                                # 此处用 TTS 实测句级时间戳替换（仅首次配音触发，
                                # has_narration=False，不覆盖用户后续手改）。
                                self._realign_captions_to_narration(timeline, segments)
                                # Phase 2.3：提取 NEL 配音事件线挂到旁白轨 metadata
                                # （数字/强调/转折/设问/枚举 cue → 动画对齐消费）
                                try:
                                    from clipwright.services.narration_events import (
                                        align_animations_to_nel,
                                        attach_nel_to_timeline,
                                    )
                                    attach_nel_to_timeline(timeline, segments, bpm=float(bpm) if bpm else None)
                                    # Phase 2.4/2.5：后置对齐 — MG 动画 clip 吸附到 NEL 事件/节拍
                                    # （AnimationAgent 先于本 Agent 运行，生成时 NEL 尚不存在）
                                    align_animations_to_nel(timeline)
                                except Exception as e:
                                    logger.warning("NEL 提取/对齐失败（跳过，不影响管线）: %s", e)

                                notes.append(
                                    f"自动配音: {len(segments)} 段旁白"
                                )

                                # ── 7b. 由旁白分段生成字幕 clip（受 subtitle_enabled 门控）──
                                if (
                                    bool(audio_config.get("subtitle_enabled", True))
                                    and not audio_path
                                    and any(
                                        (getattr(c, "metadata", {}) or {}).get("text")
                                        for c in narr_track.clips
                                    )
                                ):
                                    # 查找或创建字幕轨
                                    text_track = None
                                    for t in timeline.tracks:
                                        if t.kind == ClipKind.TEXT:
                                            text_track = t
                                            break
                                    if text_track is None:
                                        used_indexes = {t.index for t in timeline.tracks}
                                        text_index = 1
                                        while text_index in used_indexes:
                                            text_index += 1
                                        text_track = Track(
                                            id=_uid("t"),
                                            name="字幕轨",
                                            kind=ClipKind.TEXT,
                                            index=text_index,
                                        )
                                        timeline.tracks.append(text_track)

                                    caption_clips: list[Clip] = []
                                    for nclip in narr_track.clips:
                                        text = (getattr(nclip, "metadata", {}) or {}).get("text", "")
                                        if not text:
                                            continue
                                        # 去重：该分段已被现有点位重合的字幕覆盖 → 跳过
                                        if any(
                                            c.kind == ClipKind.CAPTION
                                            and abs(c.start_sec - nclip.start_sec) < 1e-6
                                            for c in [*text_track.clips, *caption_clips]
                                        ):
                                            continue
                                        caption_clips.append(
                                            Clip(
                                                id=_uid("cc"),
                                                kind=ClipKind.CAPTION,
                                                asset_id="",
                                                track_id=text_track.id,
                                                start_sec=nclip.start_sec,
                                                duration_sec=nclip.duration_sec,
                                                text=text[:100],
                                                font="sans-serif",
                                                font_size=36,
                                                font_color="#ffffff",
                                                metadata={
                                                    "category": "caption",
                                                    "renderer": "drawtext",
                                                    "position": "bottom",
                                                },
                                            )
                                        )
                                    if caption_clips:
                                        text_track.clips.extend(caption_clips)
                                        text_track.clips.sort(key=lambda c: c.start_sec)
                                        notes.append(f"字幕: {len(caption_clips)} 条")
                        else:
                            notes.append(
                                f"自动配音失败: {getattr(res, 'error', 'unknown')}"
                            )
            except Exception as dub_err:
                notes.append(f"自动配音失败: {str(dub_err)[:200]}")

            # ── 7b. 配音门控诊断：有文案但未触发配音时说明哪个条件未满足 ──
            try:
                voice_id = audio_config.get("voice_id", "")
                auto_dub = bool(audio_config.get("auto_dub", True))
                script_text = context.extra_params.get("script_text", "")
                video_mode = context.extra_params.get("video_mode", "")
                gate_ok = (
                    auto_dub
                    and voice_id
                    and script_text.strip()
                    and video_mode == "voiceover"
                )
                if not gate_ok and script_text.strip():
                    failed = []
                    if not voice_id:
                        failed.append("voice_id 未配置（Persona voice_model 为空）")
                    if video_mode != "voiceover":
                        failed.append(f"video_mode={video_mode or '未设置'}（需 voiceover）")
                    if not auto_dub:
                        failed.append("auto_dub 已关闭")
                    if context.extra_params.get("audio_path", ""):
                        failed.append("已提供上传配音（B7: 跳过 TTS）")
                    notes.append(f"配音未触发: {'; '.join(failed)}")
            except Exception:
                pass

            # ── 8. 无声音检测：音频轨全是占位 clip（asset_id 为空）且无旁白 ──
            try:
                has_real_audio = False
                for t in timeline.tracks:
                    if t.kind != ClipKind.AUDIO:
                        continue
                    for c in t.clips:
                        if (getattr(c, "asset_id", "") or ""):
                            has_real_audio = True
                            break
                    if has_real_audio:
                        break
                if not has_real_audio:
                    # ── demo 配音回退（B8: 需显式 allow_demo_audio=true 才启用）──
                    # 原实现无声音时静默用内置 demo voice.mp3 铺满成片并拉伸
                    # timeline 时长——生产运行可能交付一段与内容无关的演示配音
                    # 且状态为成功。默认路径诚实输出无音轨 + 警告。
                    allow_demo_audio = bool((context.extra_params or {}).get("allow_demo_audio", False))
                    demo_voice = self._resolve_demo_voice() if allow_demo_audio else ""
                    if demo_voice:
                        demo_dur = self._probe_demo_duration(demo_voice)
                        tl_dur = float(getattr(timeline, "duration_sec", 0) or 0)
                        dur = max(0.0, demo_dur or tl_dur)
                        if dur <= 0:
                            dur = tl_dur
                        # 移除空占位 clip，避免与 demo 配音重复/混淆
                        for t in timeline.tracks:
                            if t.kind == ClipKind.AUDIO:
                                t.clips = [
                                    c for c in t.clips
                                    if (getattr(c, "asset_id", "") or "")
                                ]
                        demo_clip = Clip(
                            id=f"dub_{uuid.uuid4().hex[:8]}",
                            kind=ClipKind.AUDIO,
                            asset_id=demo_voice,
                            track_id=audio_track.id,
                            start_sec=0.0,
                            duration_sec=dur,
                            volume=1.0,
                            eq_preset="voice",
                            metadata={"dubbing": True, "source": "demo"},
                        )
                        if demo_clip not in audio_track.clips:
                            audio_track.clips.insert(0, demo_clip)
                        if tl_dur < dur:
                            timeline.duration_sec = dur
                        notes.append(f"使用 demo 配音 voice.mp3（{dur:.0f}s）")
                        has_real_audio = True
                    if not has_real_audio:
                        msg = (
                            "无配音与BGM配置（voice 未配置、无 TTS/音乐 key）"
                            + ("，成片将无声音" if not allow_demo_audio
                               else "，且无 demo voice.mp3 可用，成片将无声音")
                        )
                        if not allow_demo_audio and demo_voice:
                            msg += "（检测到内置 demo 音频，如需启用请传 allow_demo_audio=true）"
                        elif not allow_demo_audio:
                            msg += "（demo 音频兜底需显式传 allow_demo_audio=true）"
                        logger.warning("AudioAgent: %s", msg)
                        notes.append(msg)
                        try:
                            from clipwright.services.trace import add_event as _evt
                            _evt(context.pipeline_id, "audio", "warning", msg)
                        except Exception:
                            pass
            except Exception:
                pass

            return AudioOutput(
                decision=AgentDecision.PASS,
                timeline=timeline,
                audio_notes=notes,
            )

        except Exception as e:
            return self.build_error_output(str(e), AudioOutput)

    @staticmethod
    def _realign_captions_to_narration(timeline, segments: list[dict]) -> None:
        """生产加固 2.2：用 TTS 实测句级时间戳重建字幕轨（替换估算比例时间）。

        仅处理带 audio_path+text 的成功段；样式与 EditAgent 字幕 clip 保持一致
        （stroke/shadow 显式注入，renderer=ass）。无字幕轨/无成功段时 no-op。
        """
        narr = [s for s in segments if s.get("audio_path") and (s.get("text") or "").strip()]
        if not narr:
            return
        for t in timeline.tracks:
            if t.kind != ClipKind.CAPTION:
                continue
            new_clips = []
            for i, seg in enumerate(narr):
                start = float(seg.get("start_sec", 0) or 0)
                end = float(seg.get("end_sec", start) or start)
                new_clips.append(Clip(
                    id=f"cc_narr_{i}_{uuid.uuid4().hex[:6]}",
                    kind=ClipKind.CAPTION,
                    asset_id="",
                    track_id=t.id,
                    start_sec=round(start, 3),
                    duration_sec=round(max(0.3, end - start), 3),
                    text=str(seg["text"]),
                    font="sans-serif",
                    stroke_width=2.0,
                    stroke_color="#000000",
                    shadow_x=1.0,
                    shadow_y=1.0,
                    shadow_color="#80000000",
                    metadata={
                        "category": "caption",
                        "position": "bottom",
                        "renderer": "ass",
                        "source": "narration_aligned",
                    },
                ))
            if new_clips:
                t.clips = new_clips
                logger.info("AudioAgent: 字幕轨按实测旁白时间重建 %d 条", len(new_clips))
        # B9: 重建全部字幕轨（原实现 return 只处理首轨，多字幕轨时其余轨保留
        # 过时的估算时间轴 → 音画/字膜漂移）

    @staticmethod
    def _resolve_demo_voice() -> str:
        """定位内置 demo 配音文件（data/demo/voice.mp3，相对仓库根）。"""
        candidates = [
            Path(__file__).resolve().parents[2] / "data" / "demo" / "voice.mp3",
            Path("data/demo/voice.mp3"),
            Path("_cache/demo/voice.mp3"),
        ]
        for p in candidates:
            if p.exists() and p.stat().st_size > 2000:
                return str(p)
        return ""

    @staticmethod
    def _probe_demo_duration(path: str) -> float:
        """用 ffprobe 探测 demo 配音时长；失败返回 0。"""
        try:
            import subprocess
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", path],
                capture_output=True, text=True, timeout=15,
            )
            if r.returncode == 0 and r.stdout.strip():
                return float(r.stdout.strip())
        except Exception:
            pass
        return 0.0

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

    # ── LLM BGM 情绪匹配（A2）────────────────────────────

    @staticmethod
    def _collect_scenes_emotions(input_data: AudioInput, timeline: Timeline) -> list[dict]:
        """收集场景情绪输入（标题/描述/可选情绪），供 LLM BGM 匹配。

        优先取制作规划书的 scenes/raw_scenes；缺失时回退到视频轨 clip 的
        metadata（title/name/description）。
        """
        scenes: list[dict] = []
        plan = input_data.production_plan or {}
        for key in ("scenes", "raw_scenes"):
            raw = plan.get(key) or []
            if not isinstance(raw, list):
                continue
            for i, s in enumerate(raw):
                if not isinstance(s, dict):
                    continue
                item: dict[str, Any] = {
                    "index": i,
                    "title": str(s.get("title") or "")[:200],
                    "description": str(s.get("description") or "")[:500],
                }
                if s.get("emotion"):
                    item["emotion"] = str(s["emotion"])[:100]
                if item["title"] or item["description"]:
                    scenes.append(item)
        if scenes:
            return scenes
        for t in timeline.tracks:
            if t.kind != ClipKind.VIDEO:
                continue
            for c in t.clips:
                meta = getattr(c, "metadata", {}) or {}
                title = str(meta.get("title") or meta.get("name") or "")[:200]
                desc = str(meta.get("description") or "")[:500]
                if title or desc:
                    scenes.append({
                        "index": len(scenes),
                        "title": title,
                        "description": desc,
                    })
            if scenes:
                break
        return scenes

    async def _llm_match_bgm(
        self,
        scenes_emotions: list[dict],
        bgm_slots: dict,
        pipeline_id: str = "",
    ) -> dict:
        """按场景情绪调用 LLM 推荐 BGM 槽位分配（风格 + 音量包络 + 停顿设计）。

        与 structure_agent._enrich_scene_animations 相同的 LLM 调用模式
        （LLMService.structured_output + 输出 schema + pipeline_id 追踪）。

        返回 {"allocations": [{"slot", "style", "volume_envelope", "pause_design"}]}；
        未配置 API key / 输入为空 / LLM 失败 / 输出非法 → 一律返回 {}，
        由调用方回退 `_match_bgm_slot` 规则（管线在 LLM 不可用时仍可运行）。
        """
        if not (settings.llm_api_key or settings.llm_flash_api_key):
            return {}
        if not scenes_emotions or not bgm_slots:
            return {}
        slot_keys = [str(k) for k in bgm_slots.keys()]
        try:
            scenes_text = "\n".join(
                f"场景{s.get('index', i)}: 标题={s.get('title', '')} | "
                f"描述={s.get('description', '')}"
                + (f" | 情绪={s.get('emotion')}" if s.get("emotion") else "")
                for i, s in enumerate(scenes_emotions)
            )
            system_prompt = (
                "你是视频 BGM 情绪匹配专家。根据分镜场景的情绪基调，为每个 BGM 槽位"
                "推荐音乐风格，并设计音量包络与停顿。\n\n"
                "## BGM 槽位（按时间进度划分）\n"
                f"可用槽位: {'、'.join(slot_keys)}\n"
                "槽位含义参考：intro/opening/hook=开场铺垫；backing/background="
                "平稳推进；climax/build/intensity=高潮；outro/resolution=收尾。\n\n"
                "## 输出要求\n"
                '返回 JSON: {"allocations": [{"slot": "槽位key", '
                '"style": "音乐风格描述(如 warm ambient piano / tense electronic)", '
                '"volume_envelope": [{"t": 0-1(相对时长), "v": 0-1(音量)}], '
                '"pause_design": {"pause_before_sec": 秒, "pause_after_sec": 秒}}]}\n'
                "- slot 必须是给定槽位之一，每个槽位最多一条 allocation；\n"
                "- 包络至少 2 个点，t 由 0 递增到 1，音量 0-1；\n"
                "- 停顿秒数必须 >= 0。"
            )
            user_prompt = (
                f"以下是 {len(scenes_emotions)} 个分镜场景（含标题/描述/情绪），"
                f"请为各 BGM 槽位推荐风格与包络：\n\n{scenes_text}"
            )
            result = await self.llm_or_fallback(lambda: self._llm.structured_output(system_prompt=system_prompt, user_prompt=user_prompt, output_schema={
                    "type": "object",
                    "properties": {
                        "allocations": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "slot": {"type": "string"},
                                    "style": {"type": "string"},
                                    "volume_envelope": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "t": {"type": "number"},
                                                "v": {"type": "number"},
                                            },
                                            "required": ["t", "v"],
                                        },
                                    },
                                    "pause_design": {
                                        "type": "object",
                                        "properties": {
                                            "pause_before_sec": {"type": "number"},
                                            "pause_after_sec": {"type": "number"},
                                        },
                                    },
                                },
                                "required": ["slot"],
                            },
                        },
                    },
                    "required": ["allocations"],
                },
                pipeline_id=pipeline_id), fallback=None, retries=2)
            return self._sanitize_allocations(result, slot_keys)
        except Exception as e:
            logger.warning("AudioAgent: LLM BGM 匹配失败，回退规则: %s", e)
            return {}

    @staticmethod
    def _sanitize_allocations(result: Any, slot_keys: list[str]) -> dict:
        """校验并归一化 LLM 返回的 allocations（防误导性/畸形输出）。

        非法槽位、非法类型、越界数值一律丢弃；全部非法时返回 {}。
        """
        if not isinstance(result, dict):
            return {}
        raw = result.get("allocations")
        if not isinstance(raw, list):
            return {}
        ok: list[dict[str, Any]] = []
        seen: set[str] = set()
        for a in raw:
            if not isinstance(a, dict):
                continue
            slot = a.get("slot")
            if not isinstance(slot, str) or slot not in slot_keys or slot in seen:
                continue
            seen.add(slot)
            entry: dict[str, Any] = {"slot": slot}
            style = a.get("style")
            if isinstance(style, str) and style.strip():
                entry["style"] = style.strip()[:200]
            env = a.get("volume_envelope")
            if isinstance(env, list) and len(env) >= 2:
                pts = []
                for p in env:
                    if not isinstance(p, dict):
                        continue
                    t, v = p.get("t"), p.get("v")
                    if (
                        isinstance(t, (int, float)) and not isinstance(t, bool)
                        and isinstance(v, (int, float)) and not isinstance(v, bool)
                    ):
                        pts.append({
                            "t": max(0.0, min(1.0, float(t))),
                            "v": max(0.0, min(1.0, float(v))),
                        })
                if len(pts) >= 2:
                    pts.sort(key=lambda p: p["t"])
                    entry["volume_envelope"] = pts
            pause = a.get("pause_design")
            if isinstance(pause, dict):
                p_entry = {}
                for key in ("pause_before_sec", "pause_after_sec"):
                    val = pause.get(key)
                    if (
                        isinstance(val, (int, float)) and not isinstance(val, bool)
                        and val >= 0
                    ):
                        p_entry[key] = float(val)
                if p_entry:
                    entry["pause_design"] = p_entry
            if len(entry) > 1:  # 仅保留至少携带一项增强的分配（防空壳误导）
                ok.append(entry)
        if not ok:
            return {}
        return {"allocations": ok}

    @staticmethod
    def _allocation_for_clip(
        alloc: dict, clip_start: float, total_duration: float, bgm_slots: dict
    ) -> dict | None:
        """取 clip 时间位置命中的 LLM 分配项。

        槽位归属仍由 `_match_bgm_slot` 规则决定（时间进度分界不变），
        LLM 只提供该槽位的风格/包络/停顿增强。
        """
        if not alloc or not isinstance(alloc, dict):
            return None
        slot = AudioAgent._match_bgm_slot(clip_start, total_duration, bgm_slots)
        for a in alloc.get("allocations", []):
            if isinstance(a, dict) and a.get("slot") == slot:
                return a
        return None
