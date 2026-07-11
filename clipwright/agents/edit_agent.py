"""剪辑 Agent（EditAgent）— 时间线生成。

核心职责：
1. 接收 MaterialAgent 的候选素材 + StructureAgent 的脚本骨架
2. 从 category_plugin.translate_persona() 获取节奏参数
3. 为每个场景选取最佳素材 → 裁剪 → 放置到时间线
4. 输出粗剪 Timeline 供 AnimationAgent 加工
"""

from __future__ import annotations

import uuid
from typing import Any

from clipwright.agents.base import BaseAgent
from clipwright.schema.agent import (
    AgentContext,
    AgentDecision,
    EditInput,
    EditOutput,
)
from clipwright.schema.timeline import Clip, ClipKind, ImageFit, Timeline, Track
from clipwright.config import logger
from clipwright.services.trace import add_event
from clipwright.tool.registry import ToolRegistry


class EditAgent(BaseAgent[EditInput, EditOutput]):
    """剪辑 Agent：从脚本骨架和素材生成粗剪时间线。"""

    agent_name = "edit_agent"

    async def execute(
        self, input_data: EditInput, context: AgentContext
    ) -> EditOutput:
        notes: list[str] = []
        try:
            # 1. 解析输入
            scenes = input_data.script_skeleton.get("scenes", [])
            candidate_clips = input_data.candidate_clips or []

            # 2. 获取 Persona 剪辑参数（经 category_plugin 翻译）
            shot_params = context.extra_params.get("shot_params", {})
            cut_profile = context.extra_params.get("cut_profile", "even_flow")
            transition_weights = context.extra_params.get("transition_weights", {})

            base_shot_ms = shot_params.get("base_shot_ms", 5000)
            base_shot_sec = base_shot_ms / 1000.0

            # 3. 构建轨道
            vid_track = Track(id=_uid("t"), name="视频轨", kind=ClipKind.VIDEO, index=0)
            text_track = Track(id=_uid("t"), name="文字轨", kind=ClipKind.TEXT, index=1)
            audio_track = Track(id=_uid("t"), name="音频轨", kind=ClipKind.AUDIO, index=2)

            # 4. 标准化场景时长：如果提供了音频总时长，按比例缩放
            scene_count = len(scenes)
            total_scene_duration = sum(
                s.get("duration_sec", base_shot_sec) for s in scenes
            )
            target_duration = context.extra_params.get(
                "audio_duration_sec", total_scene_duration
            )
            if target_duration > 0 and total_scene_duration > 0:
                duration_scale = target_duration / total_scene_duration
                notes.append(f"时长缩放: {total_scene_duration:.0f}s → {target_duration:.0f}s (x{duration_scale:.2f})")
            else:
                duration_scale = 1.0

            # 5. 对每个场景取最佳素材
            current_time = 0.0
            scene_asset_map: dict[str, dict] = {}

            for i, scene in enumerate(scenes):
                scene_title = scene.get("title", f"场景{i+1}")
                scene_duration = scene.get("duration_sec", base_shot_sec) * duration_scale

                # 找此场景的最佳候选
                scene_candidates = [
                    c for c in candidate_clips
                    if c.get("scene_index") == i
                ]
                best_asset = self._pick_best(scene_candidates)

                # 素材时长不能超过场景时长
                asset_dur = scene_duration
                processed_path = ""
                if best_asset:
                    ad = best_asset.get("duration_sec")
                    if ad and ad < asset_dur:
                        asset_dur = ad
                    scene_asset_map[str(i)] = best_asset

                    # 尝试裁剪素材到实际时长
                    source_path = best_asset.get("local_path") or best_asset.get("url", "")
                    if source_path:
                        add_event(context.pipeline_id, "edit", "tool",
                                  f"video_trim({source_path.split('/')[-1][:30]}, dur={asset_dur:.1f}s)")
                        trim_result = await ToolRegistry.execute(
                            "video_trim",
                            input_path=source_path,
                            start_sec=0,
                            duration_sec=asset_dur,
                        )
                        if trim_result.status == "success" and trim_result.output_path:
                            processed_path = trim_result.output_path
                            notes.append(f"场景{i}: 裁剪 {source_path}")
                        else:
                            logger.debug("场景 %s 裁剪失败: %s", i, trim_result.error)
                            processed_path = source_path
                    else:
                        processed_path = best_asset.get("asset_id", "")
                else:
                    # 无素材占位：用场景标题生成文字视频
                    add_event(context.pipeline_id, "edit", "tool",
                              f"generate_text_video({scene_title}, dur={asset_dur:.1f}s)")
                    text_result = await ToolRegistry.execute(
                        "generate_text_video",
                        text=scene_title,
                        duration_sec=asset_dur,
                    )
                    if text_result.status == "success" and text_result.output_path:
                        processed_path = text_result.output_path
                        notes.append(f"场景{i}: 文字占位 → {scene_title}")
                    else:
                        logger.debug("文字视频生成失败: %s", text_result.error)

                # 视频 clip
                vid_clip = self._make_clip(
                    kind=ClipKind.VIDEO,
                    track_id=vid_track.id,
                    start_sec=current_time,
                    duration_sec=asset_dur,
                    asset=best_asset,
                    clip_label=f"v_{i}",
                    processed_path=processed_path,
                )
                vid_track.clips.append(vid_clip)

                # 文字 clip（场景标题字幕）
                text_clip = Clip(
                    id=_uid("c"),
                    kind=ClipKind.TEXT,
                    asset_id="",
                    track_id=text_track.id,
                    start_sec=current_time,
                    duration_sec=min(3.0, asset_dur * 0.3),
                    text=scene_title,
                    font="sans-serif",
                    font_size=48,
                    font_color="#ffffff",
                )
                text_track.clips.append(text_clip)

                # 音频轨占位
                audio_clip = Clip(
                    id=_uid("c"),
                    kind=ClipKind.AUDIO,
                    asset_id="",
                    track_id=audio_track.id,
                    start_sec=current_time,
                    duration_sec=asset_dur,
                    volume=1.0,
                )
                audio_track.clips.append(audio_clip)

                # 为下一场景留出位置
                current_time += asset_dur

            # 5. 选择转场风格
            pref_transition = max(
                transition_weights,
                key=transition_weights.get,
            ) if transition_weights else "hard_cut"
            notes.append(f"转场偏好: {pref_transition}")
            notes.append(f"基准镜头时长: {base_shot_ms}ms")
            notes.append(f"剪辑节奏: {cut_profile}")
            notes.append(f"共 {len(scenes)} 场景, {len(candidate_clips)} 候选素材, "
                         f"{len(scene_asset_map)} 场景有匹配素材")

            # 6. 构建 Timeline
            timeline = Timeline(
                id=_uid("tl"),
                width=1920,
                height=1080,
                fps=30,
                tracks=[vid_track, text_track, audio_track],
            )
            timeline.duration_sec = timeline.total_duration_sec

            return EditOutput(
                decision=AgentDecision.PASS,
                timeline=timeline,
                edit_notes=notes,
            )

        except Exception as e:
            return self.build_error_output(str(e), EditOutput)

    # ── 工具方法 ──

    def _pick_best(
        self,
        candidates: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """从候选素材中取最佳匹配。"""
        if not candidates:
            return None
        # 按 score 降序
        sorted_c = sorted(
            candidates,
            key=lambda c: c.get("score", 0) if isinstance(c.get("score"), (int, float)) else 0,
            reverse=True,
        )
        best = sorted_c[0]
        suggested = best.get("suggested_assets", [])
        if suggested:
            return max(suggested, key=lambda a: a.get("score", 0))
        return None

    def _make_clip(
        self,
        kind: ClipKind,
        track_id: str,
        start_sec: float,
        duration_sec: float,
        asset: dict[str, Any] | None,
        clip_label: str,
        processed_path: str = "",
    ) -> Clip:
        """构造一个 Clip，填充素材信息和已处理的媒体路径。"""
        clip = Clip(
            id=_uid("c"),
            kind=kind,
            asset_id=processed_path or (asset.get("asset_id", "") if asset else ""),
            track_id=track_id,
            start_sec=start_sec,
            duration_sec=duration_sec,
        )
        # 媒体路径存到 asset_id（给 Render 用），显示用自定义标签
        clip.metadata["label"] = clip_label
        if asset:
            clip.metadata["source_title"] = asset.get("title", "")

        if kind in (ClipKind.VIDEO, ClipKind.IMAGE):
            clip.image_fit = ImageFit.COVER

        return clip


def _uid(prefix: str) -> str:
    """生成短唯一 ID。"""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"
