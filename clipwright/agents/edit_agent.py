"""剪辑 Agent（EditAgent）— 时间线生成。

核心逻辑：Persona 参数注入点。
接收 Persona 配置 → 经类型插件翻译 → 生成粗剪时间线。
"""

from __future__ import annotations

from clipwright.agents.base import BaseAgent
from clipwright.schema.agent import (
    AgentContext,
    AgentDecision,
    EditInput,
    EditOutput,
)
from clipwright.schema.timeline import Clip, ClipKind, Timeline, Track


class EditAgent(BaseAgent[EditInput, EditOutput]):
    """剪辑 Agent：根据 Persona 参数和素材生成粗剪时间线。"""

    agent_name = "edit_agent"

    async def execute(
        self, input_data: EditInput, context: AgentContext
    ) -> EditOutput:
        try:
            # 从 context 获取视频类型插件翻译后的参数
            # 实际会调用 category_plugin.translate_persona()
            shot_params = context.extra_params.get("shot_params", {})
            base_shot_ms = shot_params.get("base_shot_ms", 5000)

            # ==== Phase 1 占位实现 ====
            timeline = Timeline(
                width=1920,
                height=1080,
                fps=30,
            )

            # 创建基础轨道
            video_track = Track(
                id="v1",
                name="视频轨",
                kind=ClipKind.VIDEO,
                index=0,
            )
            text_track = Track(
                id="t1",
                name="文字轨",
                kind=ClipKind.TEXT,
                index=1,
            )
            audio_track = Track(
                id="a1",
                name="音频轨",
                kind=ClipKind.AUDIO,
                index=2,
            )

            # 为每个场景生成占位片段
            scenes = input_data.script_skeleton.get("scenes", [])
            current_time = 0.0
            for i, scene in enumerate(scenes):
                scene_duration = scene.get("duration_sec", 30)
                clip = Clip(
                    id=f"v_clip_{i}",
                    kind=ClipKind.VIDEO,
                    asset_id=f"asset_{i}",
                    track_id="v1",
                    start_sec=current_time,
                    duration_sec=scene_duration,
                )
                video_track.clips.append(clip)
                current_time += scene_duration

            timeline.tracks = [video_track, text_track, audio_track]
            timeline.duration_sec = timeline.total_duration_sec

            return EditOutput(
                decision=AgentDecision.PASS,
                timeline=timeline,
                edit_notes=[
                    f"使用 {base_shot_ms}ms 基准镜头时长",
                    f"共 {len(scenes)} 个场景",
                ],
            )

        except Exception as e:
            return self.build_error_output(str(e), EditOutput)
