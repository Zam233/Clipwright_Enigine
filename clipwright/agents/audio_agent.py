"""音效 Agent（AudioAgent）— BGM 匹配与混音。

输入：带动画的时间线 + Persona 音频参数
输出：混音后的时间线（含 BGM 参考 + 音量包络）
"""

from __future__ import annotations

import uuid

from clipwright.agents.base import BaseAgent, uid as _uid
from clipwright.schema.agent import (
    AgentContext,
    AgentDecision,
    AudioInput,
    AudioOutput,
)
from clipwright.schema.timeline import Clip, ClipKind, Track
from clipwright.tool.registry import ToolRegistry


class AudioAgent(BaseAgent[AudioInput, AudioOutput]):
    """音效 Agent：BGM 匹配、混音编排。"""

    agent_name = "audio_agent"

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

            # 4. 标记 BGM 建议到音频 clip 的 metadata
            for clip in audio_track.clips:
                clip.volume = clip.volume if clip.volume is not None else 0.7
                clip.metadata = {
                    **getattr(clip, "metadata", {}),
                    "bpm": bpm,
                    "bgm_slot": self._match_bgm_slot(clip.start_sec, timeline.duration_sec, bgm_slots),
                }

            # 5. 设置淡入淡出
            if audio_track.clips:
                first = audio_track.clips[0]
                first.volume = 0.3  # 淡入起点

            # 6. 如果没有音频 clip，添加一个占位 BGM 建议
            if not audio_track.clips:
                first_bgm = next(iter(bgm_slots.values()), [""])[0] if bgm_slots else ""
                notes.append(f"建议 BGM: {first_bgm or '未配置'}" if first_bgm else "无 BGM 配置")

            notes.append(f"音频配置: voice={voice_model or '默认'}, BPM模式={bpm_mode}")
            notes.append(f"BGM 槽位: {len(bgm_slots)} 个")

            # ── 7. 自动配音（无人声配音时触发）──
            try:
                voice_id = audio_config.get("voice_id", "")
                auto_dub = bool(audio_config.get("auto_dub", True))
                script_text = context.extra_params.get("script_text", "")
                video_mode = context.extra_params.get("video_mode", "")

                # 门控：voiceover 模式 + auto_dub + 有 voice_id + 有文案
                if (
                    auto_dub
                    and voice_id
                    and script_text.strip()
                    and video_mode == "voiceover"
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
                                cursor = 0.0
                                for idx, seg in enumerate(segments):
                                    dur = float(seg.get("duration_sec", 0))
                                    clip = Clip(
                                        id=f"narr_{idx}_{uuid.uuid4().hex[:6]}",
                                        kind=ClipKind.AUDIO,
                                        asset_id=seg.get("audio_path", ""),
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

                                # 更新时间线总时长（如需）
                                if timeline.duration_sec < cursor:
                                    timeline.duration_sec = cursor

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

            return AudioOutput(
                decision=AgentDecision.PASS,
                timeline=timeline,
                audio_notes=notes,
            )

        except Exception as e:
            return self.build_error_output(str(e), AudioOutput)

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
