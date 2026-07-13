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
            logger.info("EditAgent: %d 个场景, %d 候选素材", len(scenes), len(candidate_clips))
            if not scenes:
                logger.warning("EditAgent: 场景列表为空，无法生成时间线")
                return EditOutput(
                    decision=AgentDecision.FAIL,
                    timeline=Timeline(id=_uid("tl"), width=1920, height=1080, fps=30),
                    edit_notes=["场景列表为空"],
                )

            # 2. 获取 Persona 剪辑参数（经 category_plugin 翻译）
            shot_params = context.extra_params.get("shot_params", {})
            cut_profile = context.extra_params.get("cut_profile", "even_flow")
            transition_weights = context.extra_params.get("transition_weights", {})

            base_shot_ms = shot_params.get("base_shot_ms", 5000)
            base_shot_sec = base_shot_ms / 1000.0

            # 3. 构建轨道（检测是否需要 PiP 画中画叠加轨）
            vid_track = Track(id=_uid("t"), name="视频轨", kind=ClipKind.VIDEO, index=0)
            text_track = Track(id=_uid("t"), name="文字轨", kind=ClipKind.TEXT, index=1)
            audio_track = Track(id=_uid("t"), name="音频轨", kind=ClipKind.AUDIO, index=2)
            pip_track = None  # 画中画轨，按需创建

            # 检查场景描述中是否有 PiP 需求
            has_pip = any(
                "画中画" in (s.get("description", "") or "")
                or "叠加" in (s.get("description", "") or "")
                or "PiP" in (s.get("description", "") or "")
                for s in scenes
            )
            if has_pip:
                pip_track = Track(id=_uid("t"), name="画中画", kind=ClipKind.VIDEO, index=3)

            # 4. 标准化场景时长：如果提供了音频总时长，按比例缩放
            scene_count = len(scenes)
            total_scene_duration = sum(
                s.get("duration_sec", base_shot_sec) for s in scenes
            )
            target_duration = context.extra_params.get(
                "audio_duration_sec", total_scene_duration
            )
            logger.info("EditAgent 时长: scenes=%d, sum=%.0fs, target=%.0fs (extra audio_duration_sec=%s)",
                        scene_count, total_scene_duration, target_duration,
                        context.extra_params.get("audio_duration_sec"))
            if target_duration > 0 and total_scene_duration > 0:
                duration_scale = target_duration / total_scene_duration
                notes.append(f"时长缩放: {total_scene_duration:.0f}s → {target_duration:.0f}s (x{duration_scale:.2f})")
            else:
                duration_scale = 1.0
                notes.append(f"无时长缩放: total={total_scene_duration:.0f}s, target={target_duration:.0f}s")

            # 5. 对每个场景取素材（多段素材拼接，填满场景时长）
            current_time = 0.0
            scene_asset_map: dict[str, dict] = {}
            clip_index = 0

            for i, scene in enumerate(scenes):
                scene_title = scene.get("title", f"场景{i+1}")
                scene_duration = scene.get("duration_sec", base_shot_sec) * duration_scale

                # 获取此场景的全部候选素材（按分数排序）
                scene_candidates = [
                    c for c in candidate_clips
                    if c.get("scene_index") == i
                ]
                sorted_assets = []
                for c in scene_candidates:
                    suggested = c.get("suggested_assets", [])
                    sorted_assets.extend(sorted(suggested, key=lambda a: a.get("score", 0), reverse=True))

                # 循环取素材填充场景，直到用完场景时长或素材耗尽
                remaining = scene_duration
                failed_assets = set()
                while remaining > 1.0:
                    seg_dur = remaining
                    processed_path = ""

                    # 取下一个有效素材（跳过已确认失败的）
                    asset = None
                    source_path = ""
                    if sorted_assets:
                        for _ in range(len(sorted_assets) * 2):
                            candidate = sorted_assets[clip_index % len(sorted_assets)]
                            clip_index += 1
                            sp = candidate.get("local_path") or candidate.get("url", "")
                            if sp and sp not in failed_assets:
                                asset = candidate
                                source_path = sp
                                break
                            elif not sp:
                                failed_assets.add(sp or candidate.get("asset_id", ""))

                    if asset and source_path:
                        ad = asset.get("duration_sec", seg_dur)
                        if ad and ad < seg_dur:
                            seg_dur = ad
                        add_event(context.pipeline_id, "edit", "tool",
                                  f"video_trim({source_path.split('/')[-1][:30]}, dur={seg_dur:.1f}s)")
                        trim_result = await ToolRegistry.execute(
                            "video_trim",
                            input_path=source_path,
                            start_sec=0,
                            duration_sec=seg_dur,
                        )
                        if trim_result.status == "success" and trim_result.output_path:
                            processed_path = trim_result.output_path
                        else:
                            # 素材不可用 → 加入失败集，重试下一个
                            logger.warning("EditAgent: 素材不可用 %s, 尝试下一个",
                                           source_path.split('/')[-1][:30])
                            failed_assets.add(source_path)
                            continue
                    else:
                        # 无可用素材 → 文字占位视频填满剩余时长
                        add_event(context.pipeline_id, "edit", "tool",
                                  f"generate_text_video({scene_title}, dur={seg_dur:.1f}s)")
                        text_result = await ToolRegistry.execute(
                            "generate_text_video",
                            text=scene_title,
                            duration_sec=seg_dur,
                        )
                        if text_result.status == "success" and text_result.output_path:
                            processed_path = text_result.output_path
                            notes.append(f"场景{i}: 文字占位 → {scene_title}")

                    # 添加视频 clip（注入场景描述供 Animation Agent 检测 [文字动画]/[逻辑动画] 标记）
                    vid_clip = self._make_clip(
                        kind=ClipKind.VIDEO,
                        track_id=vid_track.id,
                        start_sec=current_time,
                        duration_sec=seg_dur,
                        asset=asset,
                        clip_label=f"v_{i}_{len(vid_track.clips)}",
                        processed_path=processed_path,
                    )
                    # 注入描述信息到 metadata
                    scene_desc = scene.get("description", "") if i < len(scenes) else ""
                    if any(m in scene_desc for m in ["[动画]", "[转场]", "[文字动画]", "[逻辑动画]"]):
                        if not vid_clip.metadata:
                            vid_clip.metadata = {}
                        vid_clip.metadata["description"] = scene_desc
                    vid_track.clips.append(vid_clip)

                    # 画中画：如果场景描述含 PiP 标记，在主视频 clip 之后添加 PiP clip
                    if pip_track and ("[PiP]" in scene_desc or "画中画" in scene_desc):
                        pip_clip = self._make_clip(
                            kind=ClipKind.VIDEO,
                            track_id=pip_track.id,
                            start_sec=current_time,
                            duration_sec=seg_dur,
                            asset=asset,
                            clip_label=f"pip_{i}",
                            processed_path=processed_path,
                        )
                        # 设置 PiP 位置（右下角，占画面 30%）
                        pip_clip.image_rect = {"x": 0.65, "y": 0.55, "w": 0.3, "h": 0.3}
                        pip_track.clips.append(pip_clip)

                    # 文字 clip（由 AnimationAgent 管理，EditAgent 不预填充）
                    pass

                    # 音频轨占位
                    audio_clip = Clip(
                        id=_uid("c"),
                        kind=ClipKind.AUDIO,
                        asset_id="",
                        track_id=audio_track.id,
                        start_sec=current_time,
                        duration_sec=seg_dur,
                        volume=1.0,
                    )
                    audio_track.clips.append(audio_clip)

                    current_time += seg_dur
                    remaining -= seg_dur
                    if asset:
                        scene_asset_map[f"{i}_{len(vid_track.clips)}"] = asset

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
                tracks=[vid_track, text_track, audio_track] + ([pip_track] if (pip_track and pip_track.clips) else []),
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
