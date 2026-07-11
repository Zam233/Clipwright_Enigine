"""动画 Agent（AnimationAgent）— 文字动画与视觉包装。

输入：粗剪时间线 + Persona 视觉参数
输出：带动画编排的时间线

工作方式：
1. 从 Persona 视觉配置中读取动画偏好
2. 从 AnimationRegistry 获取匹配的动画定义
3. 为时间线上每个 clip 编排 onscreen 动画
4. 在 clip 之间编排 transition 动画
5. 通过 AnimationRenderer 生成时间线操作
"""

from __future__ import annotations

import uuid

from clipwright.agents.base import BaseAgent
from clipwright.animation import AnimationRegistry, AnimationRenderer
from clipwright.schema.agent import (
    AgentContext,
    AgentDecision,
    AnimationInput,
    AnimationOutput,
)
from clipwright.schema.animation import (
    AnimationInstance,
    AnimationSequence,
    AnimationType,
)


class AnimationAgent(BaseAgent[AnimationInput, AnimationOutput]):
    """动画 Agent：根据 Persona 视觉参数为时间线编排动画。"""

    agent_name = "animation_agent"

    def __init__(self) -> None:
        super().__init__()
        self._renderer = AnimationRenderer()

    async def execute(
        self, input_data: AnimationInput, context: AgentContext
    ) -> AnimationOutput:
        try:
            timeline = input_data.timeline
            visual_config = input_data.visual_config or {}

            if timeline is None or not timeline.tracks:
                return AnimationOutput(
                    decision=AgentDecision.PASS,
                    timeline=timeline,
                )

            # 1. 解析 Persona 视觉参数
            persona_style = self._resolve_visual_style(visual_config)

            # 2. 构建动画编排序列
            sequence = self._plan_animations(
                timeline=timeline,
                style=persona_style,
            )

            # 3. 渲染为时间线操作
            ops = self._renderer.render_sequence(
                defs={  # 当前所有注册的动画定义
                    a.animation_id: a
                    for a in AnimationRegistry.list()
                },
                sequence=sequence,
            )

            # 4. 序列化为字典附加到输出
            animation_data = {
                "plan": sequence.model_dump(mode="json"),
                "operations": [op.to_dict() for op in ops],
                "style": persona_style,
            }

            return AnimationOutput(
                decision=AgentDecision.PASS,
                timeline=timeline,
                animation_plan=animation_data,
            )

        except Exception as e:
            return self.build_error_output(str(e), AnimationOutput)

    def _resolve_visual_style(
        self, visual_config: dict
    ) -> dict:
        """从 Persona 视觉参数解析动画风格偏好。"""
        return {
            "animation_style": visual_config.get(
                "text_intro", "fade_in"
            ),
            "color_palette": visual_config.get("palette", "default"),
            "transition_weights": visual_config.get(
                "transition", {}
            ),
            "animation_density": visual_config.get(
                "animation_density", "medium"
            ),
        }

    def _plan_animations(
        self,
        timeline: object,
        style: dict,
    ) -> AnimationSequence:
        """为时间线编排动画序列。

        策略：
        - 对每个轨道中的每个 clip，匹配适用的入场/出场动画
        - 在 clip 之间插入转场动画
        - 根据 animation_density 控制动画密度
        """
        import json

        onscreen_anims: list[AnimationInstance] = []
        transition_anims: list[AnimationInstance] = []

        # 读取可用动画 ID
        onscreen_ids = AnimationRegistry.list_ids(AnimationType.ONSCREEN)
        transition_ids = AnimationRegistry.list_ids(AnimationType.TRANSITION)

        if not onscreen_ids and not transition_ids:
            return AnimationSequence()

        # 从视觉风格确定首选动画
        pref_intro = self._match_animation(
            style.get("animation_style", "fade_in"),
            onscreen_ids,
            default="fade_in",
        )
        pref_transition = self._match_animation(
            self._preferred_transition(style),
            transition_ids,
            default="crossfade",
        )

        timeline_json = (
            timeline.model_dump(mode="json")
            if hasattr(timeline, "model_dump")
            else {}
        )
        tracks = timeline_json.get("tracks", [])

        clip_count = 0
        for track in tracks:
            clips = track.get("clips", [])
            for i, clip in enumerate(clips):
                clip_id = clip.get("id", f"clip_{clip_count}")
                clip_start = clip.get("start_sec", 0)
                clip_dur = clip.get("duration_sec", 5)
                clip_kind = clip.get("kind", "")

                # 文本轨道 → 入场动画
                if clip_kind in ("text", "caption"):
                    anim_id = pref_intro
                    if anim_id and anim_id in onscreen_ids:
                        onscreen_anims.append(AnimationInstance(
                            animation_id=anim_id,
                            instance_id=f"anim_{uuid.uuid4().hex[:8]}",
                            type=AnimationType.ONSCREEN,
                            start_sec=clip_start,
                            duration_sec=min(
                                self._anim_duration(anim_id),
                                clip_dur * 0.3,
                            ),
                            target_clip_id=clip_id,
                        ))

                # 片段之间 → 转场
                if i > 0 and transition_ids:
                    prev_clip = clips[i - 1]
                    prev_end = prev_clip.get("start_sec", 0) + prev_clip.get("duration_sec", 0)
                    trans_dur = self._transition_duration(
                        pref_transition, transition_ids
                    )
                    if trans_dur > 0 and pref_transition:
                        transition_anims.append(AnimationInstance(
                            animation_id=pref_transition,
                            instance_id=f"trans_{uuid.uuid4().hex[:8]}",
                            type=AnimationType.TRANSITION,
                            start_sec=max(0, prev_end - trans_dur * 0.5),
                            duration_sec=trans_dur,
                            target_clip_id=prev_clip.get("id", ""),
                            target_clip_b_id=clip_id,
                        ))

                clip_count += 1

        return AnimationSequence(
            onscreen_animations=onscreen_anims,
            transition_animations=transition_anims,
        )

    @staticmethod
    def _match_animation(
        style_name: str, available: list[str], default: str
    ) -> str:
        """将风格名称匹配到实际注册的动画 ID。"""
        name_lower = style_name.lower()
        for aid in available:
            if name_lower in aid or aid in name_lower:
                return aid
        # 模糊匹配
        for aid in available:
            for token in name_lower.split("_"):
                if token and token in aid:
                    return aid
        return default if default in available else (available[0] if available else "")

    @staticmethod
    def _preferred_transition(style: dict) -> str:
        """从视觉配置中获取首选转场类型。"""
        weights = style.get("transition_weights", {})
        if weights:
            return max(weights, key=weights.get)  # type: ignore[arg-type]
        return "crossfade"

    @staticmethod
    def _anim_duration(anim_id: str) -> float:
        """获取动画的默认持续时长。"""
        defn = AnimationRegistry.get(anim_id)
        return defn.duration_sec if defn else 0.5

    @staticmethod
    def _transition_duration(
        trans_id: str, available: list[str]
    ) -> float:
        """获取转场动画的持续时长。"""
        if trans_id == "cut" or not trans_id:
            return 0.0
        defn = AnimationRegistry.get(trans_id)
        return defn.duration_sec if defn else 0.3
