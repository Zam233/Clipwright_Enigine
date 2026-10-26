"""音效 Agent（AudioAgent）— BGM 匹配与混音。

输入：带动画的时间线 + Persona 音频参数
输出：混音后的时间线（含 BGM 参考 + 音量包络）
"""

from __future__ import annotations

import json
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


async def _search_bgm_from_library(bgm_slots: dict, top_k: int = 3,
                                   extra_query: str = "") -> list[dict]:
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
        if extra_query.strip():
            queries.append(extra_query.strip()[:60])
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
            # 轮 95：语音去噪开关（默认开；audio_config.denoise=false 关闭）
            _denoise_on = bool(audio_config.get("denoise", True))
            if timeline is None:
                return AudioOutput(decision=AgentDecision.PASS, timeline=timeline)

            # Persona 目标响度 → 时间线元数据（渲染端 loudnorm 消费）。
            # 注意放在 timeline None 守卫之后（轮64审计 D14：写元数据曾先于守卫）
            _loud = audio_config.get("target_loudness_lufs")
            if _loud is not None:
                try:
                    timeline.metadata["target_loudness_lufs"] = float(_loud)
                except (TypeError, ValueError):
                    pass

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
            if audio_path and Path(audio_path).exists():
                # ffprobe 实测配音时长（旧实现用需求阶段估算值 → 画面/配音错位）
                try:
                    import subprocess as _sub
                    _pr = _sub.run(
                        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                         "-of", "json", audio_path],
                        capture_output=True, text=True, timeout=15)
                    _real = float((json.loads(_pr.stdout or "{}").get("format", {}) or {})
                                  .get("duration", 0) or 0)
                except Exception:
                    _real = 0.0
                if _real > 0:
                    if abs(_real - audio_duration) > 1.0:
                        notes.append(f"配音实测时长 {_real:.1f}s（估算 {audio_duration:.1f}s，已校正）")
                    audio_duration = _real
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
                        metadata={"dubbing": True, "source": "upload",
                                  # 上传配音多为个人录制，默认降噪（audio_config.denoise 可关）
                                  "denoise": _denoise_on},
                    )
                    audio_track.clips.insert(0, dub_clip)
                    if timeline.duration_sec < audio_duration:
                        timeline.duration_sec = audio_duration
                    notes.append(f"已导入配音文件: {audio_duration:.0f}s")

                    # 轮 83 / 本轮修复：上传配音的字幕强制对齐**默认执行**。
                    # 旧实现把整段对齐逻辑缩进到了 SFX 分支里（只有传了音效才跑），
                    # 于是上传配音后字幕仍是 EditAgent 的字数比例估算 → 音画/字幕漂移。
                    await self._align_captions_to_uploaded_dub(
                        timeline, audio_path, audio_duration, notes,
                    )

            # 轮 96（批次 6 余项）：SFX 音效链——audio_config.sfx 列表中的
            # 音效文件按 start_sec 落到独立音效轨（无 sfx 条目零行为变化）。
            # 条目: {"path": "...", "start_sec": 5.0, "volume": 0.6}
            _sfx_list = audio_config.get("sfx") or []
            if isinstance(_sfx_list, list) and _sfx_list:
                sfx_track = next(
                    (t for t in timeline.tracks if t.metadata.get("sfx")),
                    None,
                )
                if sfx_track is None:
                    sfx_track = Track(
                        id="a_sfx",
                        name="音效",
                        kind=ClipKind.AUDIO,
                        index=len(timeline.tracks),
                        metadata={"sfx": True},
                    )
                    timeline.tracks.append(sfx_track)
                _sfx_added = 0
                for i, sfx in enumerate(_sfx_list):
                    if not isinstance(sfx, dict):
                        continue
                    _p = str(sfx.get("path", "")).strip()
                    if not _p or not Path(_p).exists():
                        notes.append(f"SFX 文件不存在，跳过: {_p}")
                        continue
                    sfx_track.clips.append(Clip(
                        id=f"sfx_{i}_{uuid.uuid4().hex[:6]}",
                        kind=ClipKind.AUDIO,
                        asset_id=_p,
                        track_id=sfx_track.id,
                        start_sec=max(0.0, float(sfx.get("start_sec", 0) or 0)),
                        duration_sec=float(sfx.get("duration_sec", 1.0) or 1.0),
                        volume=max(0.0, min(1.0, float(sfx.get("volume", 0.6) or 0.6))),
                        metadata={"sfx": True},
                    ))
                    _sfx_added += 1
                if _sfx_added:
                    notes.append(f"已添加 {_sfx_added} 个音效")


            # 3. BPM 检测延后到 BGM 入轨之后（P2-31：旧实现取 audio_track 第一个
            # clip——那往往是旁白而非 BGM；且失败时残留伪 120 参与节拍对齐）
            bpm: float | None = None

            # 3b. 素材库 BGM 检索（A1）：优先用素材库音频填充 BGM；
            #     无素材源/无音频结果/失败一律回退原有 bgm_slots 规则（零改动）。
            library_bgm: list[dict] = []
            try:
                if MaterialRegistry.list():
                    # 简报 bgm_requirement 真正参与检索（旧实现仅进 notes）
                    library_bgm = await _search_bgm_from_library(bgm_slots, extra_query=brief_bgm)
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
                        scenes_emotions, bgm_slots, context.pipeline_id,
                        getattr(context, "category_plugin_id", "") or "",
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
                    # P2-31: bpm=None 时不写伪 120（旧实现检测失败残留 120 误导下游）
                    "bpm": bpm,
                    "bgm_slot": self._match_bgm_slot(
                        clip.start_sec, timeline.duration_sec, bgm_slots
                    ),
                }
                # 素材库音频 → 填充 bgm_style / bgm_library（无结果时零改动）
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
                    # 预过滤无路径条目（见 _valid_bgm_entries）
                    library_bgm = self._valid_bgm_entries(library_bgm)
                if _tl_dur > 0 and not _has_real_bgm and library_bgm:
                    _bgm_cursor = 0.0
                    _bgm_count = 0
                    _bgm_idx = 0
                    _bgm_iters = 0
                    _bgm_max_iters = len(library_bgm) * 8 + 8
                    while _bgm_cursor < _tl_dur - 0.5 and _bgm_iters < _bgm_max_iters:
                        _bgm_iters += 1
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

            # 3b+. BPM 检测——优先 BGM clip（旧实现取第一个音频 clip，那常是
            # 旁白：旁白的节奏被误用作踩点基准）；无 BGM 或检测失败 → bpm=None
            # （下游节拍对齐自动跳过，不再伪造 120）
            # 能力动态解析：插件贡献的 bpm 实现（capability="bpm_detect"）优先，
            # 没有插件时落到内置 bpm_detect。
            if bpm_mode and bpm is None and self.resolve_tool("bpm_detect") is not None:
                _bgm_clip = next(
                    (c for t in timeline.tracks if t.kind == ClipKind.AUDIO
                     for c in t.clips if (getattr(c, "metadata", {}) or {}).get("bgm") and c.asset_id),
                    None,
                )
                if _bgm_clip is not None:
                    result = await self.call_tool(
                        "bpm_detect",
                        input_path=_bgm_clip.asset_id,
                    )
                    if result.status == "success":
                        detected = result.output.get("bpm")
                        if detected:
                            bpm = detected
                            notes.append(f"检测到 BPM: {bpm}")
                        else:
                            notes.append(
                                f"BPM 自动检测失败（{result.output.get('error', 'unknown')}），"
                                "节拍剪辑跳过；可在需求中手填 beat_bpm"
                            )
                else:
                    notes.append("无 BGM 音轨，跳过 BPM 检测（可在需求中手填 beat_bpm）")

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
                # voiceover 模式优先使用用户确认的逐场景口播稿（规划书
                # raw_scenes），修复「确认的分镜口播 ≠ 实际配音文本」的契约断裂
                _plan = context.extra_params.get("production_plan") or {}
                _raw_scenes = _plan.get("raw_scenes") if isinstance(_plan, dict) else []
                _scene_scripts = [
                    str(s.get("voiceover_script") or "").strip()
                    for s in (_raw_scenes or []) if isinstance(s, dict)
                ]
                _scene_scripts = [t for t in _scene_scripts if t]
                if _scene_scripts:
                    script_text = "\n".join(_scene_scripts)
                    notes.append(f"旁白采用规划书逐场景口播稿（{len(_scene_scripts)} 场景）")
                # 已上传配音（audio_path）时跳过 TTS——原 has_dub（配音插入）
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
                        # 技能动态解析：插件贡献同名/同能力的配音技能即可接管，
                        # 技能卸载后立即消失（resolve_skill 每次实时查注册表）。
                        res = await self.call_skill(
                            "dub",
                            voice_id=voice_id,
                            text=script_text,
                            # 透传会话 split_mode（旧实现硬编码 sentence）
                            split_mode=str(context.extra_params.get("split_mode") or "sentence"),
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
                                # 失败分段（无音频文件/零时长）不入轨——原实现
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
                                            # 渲染端 afftdn 仅对带此标记的音源生效
                                            "denoise": _denoise_on,
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
                                # 修复：_realign_captions_to_narration 已把实测旁白字幕
                                # 重建进 CAPTION 轨；此处再往 TEXT 轨追加同类 clip 会造成
                                # 渲染双轨合并 → 同一句字幕烧两遍。检测到已重建即跳过。
                                if (
                                    bool(audio_config.get("subtitle_enabled", True))
                                    and not audio_path
                                    and not self._captions_realigned(timeline)
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
                            logger.debug("demo 音频提示事件失败", exc_info=True)
            except Exception:
                # 批A(R14)：该块承载"成片将无声音"的用户告警，不能静默
                logger.exception("AudioAgent: 音频兜底/告警块异常")
                notes.append("音频兜底配置处理异常（详见日志）")

            # 时长对账——画面轨总长 vs 音频轨终点。音频超长 → 末个画面
            # clip 冻结帧补齐（旧实现无任何对账，音频 80s 配画面 60s 以
            # 冻结帧 + 静音尾巴交付且无人知晓）
            try:
                # 批A(D3)：只统计主视频轨（PiP 叠加轨同为 VIDEO kind，
                # 旧实现把叠加层计入总长并可能错误延长叠加层片段）
                _video_tracks = [t for t in timeline.tracks if t.kind == ClipKind.VIDEO]
                _main_vt = min(_video_tracks, key=lambda t: t.index) if _video_tracks else None
                _vclips = list(_main_vt.clips) if _main_vt else []
                _video_dur = sum(c.duration_sec for c in _vclips)
                _audio_ends = [c.start_sec + c.duration_sec for t in timeline.tracks
                               if t.kind == ClipKind.AUDIO for c in t.clips
                               if not (getattr(c, "metadata", {}) or {}).get("bgm")]
                _audio_end = max(_audio_ends) if _audio_ends else 0.0
                if _audio_end > _video_dur + 1.0 and _vclips:
                    _extend = _audio_end - _video_dur
                    _last = max(_vclips, key=lambda c: c.start_sec + c.duration_sec)
                    _last.duration_sec = round(_last.duration_sec + _extend, 3)
                    notes.append(
                        f"时长对账：音频比画面长 {_extend:.1f}s，末尾画面冻结帧补齐")
                elif _video_dur > _audio_end + 1.0 and _audio_end > 0:
                    notes.append(
                        f"时长对账：画面比音频长 {_video_dur - _audio_end:.1f}s（片尾静音）")
            except Exception:
                logger.debug("时长对账失败（忽略）", exc_info=True)

            return AudioOutput(
                decision=AgentDecision.PASS,
                timeline=timeline,
                audio_notes=notes,
            )

        except Exception as e:
            return self.build_error_output(str(e), AudioOutput)

    @staticmethod
    def _valid_bgm_entries(entries: list[dict]) -> list[dict]:
        """批1：过滤无 local_path/url 的 BGM 条目（全空时不进入铺轨循环，
        杜绝旧实现 continue 不推进游标的死循环挂死管线）。"""
        return [
            _e for _e in (entries or [])
            if (getattr(_e.get("asset"), "local_path", None)
                or getattr(_e.get("asset"), "url", None))
        ]

    @staticmethod
    def _captions_realigned(timeline) -> bool:
        """字幕轨是否已按实测旁白重建（A8：realign 成功的标志 = source=narration_aligned）。

        为真时 7b 不再向 TEXT 轨追加第二套字幕，避免双轨合并后同句烧两遍。
        """
        try:
            for t in timeline.tracks:
                if t.kind == ClipKind.CAPTION and any(
                    (getattr(c, "metadata", {}) or {}).get("source") == "narration_aligned"
                    for c in t.clips
                ):
                    return True
        except Exception:
            return False
        return False

    #: 重建字幕时从既有 clip 继承的样式字段（时间与文本必须用新的）
    _CAPTION_STYLE_FIELDS = (
        "font", "font_size", "font_color", "text_align",
        "font_weight", "font_italic", "letter_spacing",
        "stroke_width", "stroke_color",
        "shadow_x", "shadow_y", "shadow_color", "shadow_blur",
        "glow_color", "glow_width",
    )

    async def _align_captions_to_uploaded_dub(
        self, timeline, audio_path: str, audio_duration: float, notes: list[str],
    ) -> None:
        """上传配音 → 用配音实测时间重建字幕（**默认行为**，与 SFX 无关）。

        历史缺陷：这段逻辑被缩进在「audio_config.sfx 非空」分支里，于是只有
        传了音效才会跑 —— 上传配音后字幕仍是 EditAgent 的字数比例估算，
        音画/字幕随时间漂移。现在只要 audio_path 存在就执行。

        没有 whisper 时 ``STTService.align`` 会按字数比例把实测总时长摊到各句
        （仍是"与配音匹配"，只是句内不精确）；对齐不可用时明确写进 notes，
        不静默保留估算。
        """
        try:
            from clipwright.services.stt import STTService

            caption_texts: list[str] = []
            for t in timeline.tracks:
                if t.kind != ClipKind.CAPTION:
                    continue
                for c in t.clips:
                    meta = getattr(c, "metadata", {}) or {}
                    txt = (getattr(c, "text", "") or meta.get("text") or "").strip()
                    if txt:
                        caption_texts.append(txt)
            if not caption_texts:
                notes.append("上传配音：时间线尚无字幕，跳过字幕对齐")
                return

            stt = STTService()
            aligned = await stt.align(audio_path, "。".join(caption_texts))
            segs = [
                {
                    "text": s.get("text", ""),
                    "start_sec": float(s.get("start", 0) or 0),
                    "end_sec": float(s.get("end", 0) or 0),
                    "audio_path": audio_path,
                }
                for s in (aligned.segments if aligned.success else [])
                if (s.get("text") or "").strip()
            ]
            if not segs:
                notes.append(
                    "上传配音：字幕对齐不可用"
                    f"（{getattr(aligned, 'error', '') or '无分段'}），保留按字数估算的时间"
                )
                return

            if len(segs) == len(caption_texts):
                # 段数一致：用**实测时间** + **原始切分文本**。
                # whisper 转写可能有错字，字幕文案不该被它改写。
                segs = [
                    {
                        "text": caption_texts[i],
                        "start_sec": segs[i]["start_sec"],
                        "end_sec": segs[i]["end_sec"],
                        "audio_path": audio_path,
                    }
                    for i in range(len(caption_texts))
                ]
            else:
                # 段数不一致（无标点长文最常踩：STT 把整段当一个句子）→
                # 按配音时长等比分给原来的每一段字幕，保证"切几段就几段"。
                notes.append(
                    f"上传配音：对齐得到 {len(segs)} 段、字幕有 {len(caption_texts)} 段，"
                    "已按配音时长等比分段（每段仍对应一句字幕）"
                )
                segs = self._proportional_segments(caption_texts, audio_duration, audio_path)

            self._realign_captions_to_narration(timeline, segs)
            notes.append(
                f"上传配音字幕对齐: {len(segs)} 段"
                f"（{getattr(aligned, 'model', '') or 'estimate'}）"
            )
            # P1-23：NEL 事件线 + 动画对齐同样适用于上传配音（bpm 尚未检测，传 None）
            try:
                from clipwright.services.narration_events import (
                    align_animations_to_nel,
                    attach_nel_to_timeline,
                )

                attach_nel_to_timeline(timeline, segs, bpm=None)
                align_animations_to_nel(timeline)
            except Exception as e:  # noqa: BLE001
                logger.warning("上传配音 NEL 提取/对齐失败（跳过）: %s", e)
        except Exception:  # noqa: BLE001 — 对齐失败不能带崩音频阶段
            logger.exception("上传配音字幕对齐失败（保留估算字幕）")
            notes.append("上传配音：字幕对齐失败，保留按字数估算的时间")

    @staticmethod
    def _proportional_segments(
        texts: list[str], duration: float, audio_path: str,
    ) -> list[dict]:
        """把总时长按字数比例摊到给定的每段文本（末段对齐总时长）。"""
        weights = [max(1, len(t)) for t in texts]
        total = sum(weights) or 1
        segs: list[dict] = []
        cursor = 0.0
        for text, weight in zip(texts, weights):
            span = max(0.0, duration) * weight / total
            segs.append({
                "text": text,
                "start_sec": round(cursor, 3),
                "end_sec": round(cursor + span, 3),
                "audio_path": audio_path,
            })
            cursor += span
        if segs:
            segs[-1]["end_sec"] = round(max(0.0, duration), 3)
        return segs

    @classmethod
    def _caption_style_template(cls, clips) -> dict:
        """取既有字幕 clip 的样式作为重建模板（避免对齐后样式被重置）。"""
        if not clips:
            return {}
        first = clips[0]
        style: dict[str, object] = {}
        for field in cls._CAPTION_STYLE_FIELDS:
            value = getattr(first, field, None)
            if value is not None:
                style[field] = value
        return style

    @classmethod
    def _realign_captions_to_narration(cls, timeline, segments: list[dict]) -> None:
        """生产加固 2.2：用实测句级时间戳重建字幕轨（替换估算比例时间）。

        仅处理带 audio_path+text 的成功段；样式**继承既有字幕 clip**
        （TTS 与上传配音共用同一条重建路径，避免把用户/EditAgent 的样式洗掉）。
        无字幕轨/无成功段时 no-op。
        """
        narr = [s for s in segments if s.get("audio_path") and (s.get("text") or "").strip()]
        if not narr:
            return
        for t in timeline.tracks:
            if t.kind != ClipKind.CAPTION:
                continue
            style = cls._caption_style_template(t.clips)
            meta0 = dict((getattr(t.clips[0], "metadata", None) or {})) if t.clips else {}
            base_meta = {
                "category": "caption",
                "position": meta0.get("position", "bottom"),
                "renderer": meta0.get("renderer", "ass"),
                "source": "narration_aligned",
            }
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
                    # 样式继承；缺的字段补可读默认（白字黑描边 + 轻阴影）
                    **{
                        "font": "sans-serif",
                        "stroke_width": 2.0,
                        "stroke_color": "#000000",
                        "shadow_x": 1.0,
                        "shadow_y": 1.0,
                        "shadow_color": "#80000000",
                        **style,
                    },
                    metadata=base_meta,
                ))
            if new_clips:
                t.clips = new_clips
                logger.info("AudioAgent: 字幕轨按实测旁白时间重建 %d 条", len(new_clips))
        # 重建全部字幕轨（旧实现只处理首轨，多字幕轨时其余轨保留过时的估算时间轴）

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
        """根据时间位置匹配合适的 BGM 槽位。

        轮 84 修复：persona 实际槽位键是 main_argument/reflection 等自定义名，
        旧实现硬编码英文键全部失配 → 永远回退第一个槽位。现在补充常见
        persona 键别名，并在全部失配时按进度线性映射到槽位序号。
        """
        if not bgm_slots:
            return "default"
        progress = clip_start / max(total_duration, 1)
        # 按进度选择：开头、中间、高潮、结尾（英/中/常见 persona 键别名）
        if progress < 0.15:
            for key in ("intro", "opening", "hook", "开场", "引入"):
                if key in bgm_slots:
                    return key
        elif progress < 0.4:
            for key in ("backing", "background", "theory_backing", "main_argument",
                        "论述", "主体", "铺垫"):
                if key in bgm_slots:
                    return key
        elif progress < 0.7:
            for key in ("climax", "build", "intensity", "高潮", "论证加速"):
                if key in bgm_slots:
                    return key
        else:
            for key in ("outro", "resolution", "ending", "reflection", "总结", "收尾"):
                if key in bgm_slots:
                    return key
        # 序号等分回退：按进度线性映射到槽位序（首槽位盖开头，末槽位盖结尾）
        keys = list(bgm_slots.keys())
        idx = min(len(keys) - 1, int(progress * len(keys) * 1.15))
        return keys[max(0, idx)]

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
        category_id: str = "",
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
            # V1/V15：类型身份 + 音频/字幕两阶段规范（音频 Agent 同时管字幕生成）
            from clipwright.services.type_prompts import compose_many as compose_type_prompts

            system_prompt = compose_type_prompts(
                system_prompt, category_id, ("audio", "subtitle")
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
