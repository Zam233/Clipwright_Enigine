"""动画渲染器 — 将 AnimationDef + AnimationInstance 转为具体的时间线操作。

输出格式：TimelineOperation 列表，供 AnimationAgent 在时间线上执行。

渲染策略：
- onscreen: 用 FFmpeg drawtext / overlay filter 在指定时间叠加
- transition: 用 FFmpeg crossfade / overlay 等 filter 实现过渡
"""

from __future__ import annotations

from typing import Any

from clipwright.config import logger
from clipwright.schema.animation import (
    AnimationDef,
    AnimationInstance,
    AnimationSequence,
    AnimationType,
    EasingFunction,
    Keyframe,
)


class TimelineAnimationOp:
    """时间线上的动画操作（中间表示，不直接对应 FFmpeg）。"""

    def __init__(
        self,
        animation_id: str,
        instance_id: str,
        op_type: str,
        start_sec: float,
        duration_sec: float,
        params: dict[str, Any],
        target_clip_id: str = "",
    ) -> None:
        self.animation_id = animation_id
        self.instance_id = instance_id
        self.op_type = op_type  # "onscreen_overlay" / "transition_overlay"
        self.start_sec = start_sec
        self.duration_sec = duration_sec
        self.params = params
        self.target_clip_id = target_clip_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "animation_id": self.animation_id,
            "instance_id": self.instance_id,
            "op_type": self.op_type,
            "start_sec": self.start_sec,
            "end_sec": self.start_sec + self.duration_sec,
            "duration_sec": self.duration_sec,
            "params": self.params,
            "target_clip_id": self.target_clip_id,
        }


class AnimationRenderer:
    """将 AnimationDef 渲染为时间线操作。"""

    @staticmethod
    def render_sequence(
        defs: dict[str, AnimationDef],
        sequence: AnimationSequence,
    ) -> list[TimelineAnimationOp]:
        """将动画编排序列渲染为时间线操作列表。"""
        ops: list[TimelineAnimationOp] = []

        for inst in sequence.onscreen_animations:
            defn = defs.get(inst.animation_id)
            if defn is None:
                logger.warning("onscreen animation %s not found in defs, skipping", inst.animation_id)
                continue
            op = AnimationRenderer._render_onscreen(defn, inst)
            ops.append(op)

        for inst in sequence.text_animations:
            defn = defs.get(inst.animation_id)
            if defn is None:
                logger.warning("text animation %s not found in defs, skipping", inst.animation_id)
                continue
            op = AnimationRenderer._render_text(defn, inst)
            ops.append(op)

        for inst in sequence.transition_animations:
            defn = defs.get(inst.animation_id)
            if defn is None:
                logger.warning("transition animation %s not found in defs, skipping", inst.animation_id)
                continue
            ops.append(AnimationRenderer._render_transition(defn, inst))

        logger.info("Rendered %d animation ops (onscreen=%d, text=%d, transition=%d)",
                     len(ops), len(sequence.onscreen_animations),
                     len(sequence.text_animations), len(sequence.transition_animations))
        return ops

    @staticmethod
    def _render_onscreen(
        defn: AnimationDef,
        inst: AnimationInstance,
    ) -> TimelineAnimationOp:
        """渲染屏幕上展示的动画。"""
        interpolated = AnimationRenderer._interpolate_keyframes(
            defn.keyframes,
            easing=_easing_value(defn.easing),
        )

        return TimelineAnimationOp(
            animation_id=inst.animation_id,
            instance_id=inst.instance_id,
            op_type="onscreen_overlay",
            start_sec=inst.start_sec,
            duration_sec=inst.duration_sec,
            params={
                "defn": defn.model_dump(mode="json"),
                "interpolated": interpolated,
                "overrides": inst.params,
            },
            target_clip_id=inst.target_clip_id,
        )

    @staticmethod
    def _render_text(
        defn: AnimationDef,
        inst: AnimationInstance,
    ) -> TimelineAnimationOp:
        """渲染文字动画。"""
        interpolated = AnimationRenderer._interpolate_keyframes(
            defn.keyframes,
            easing=_easing_value(defn.easing),
        )

        return TimelineAnimationOp(
            animation_id=inst.animation_id,
            instance_id=inst.instance_id,
            op_type="text_overlay",
            start_sec=inst.start_sec,
            duration_sec=inst.duration_sec,
            params={
                "defn": defn.model_dump(mode="json"),
                "interpolated": interpolated,
                "overrides": inst.params,
            },
            target_clip_id=inst.target_clip_id,
        )

    @staticmethod
    def _render_transition(
        defn: AnimationDef,
        inst: AnimationInstance,
    ) -> TimelineAnimationOp:
        """渲染转场动画。"""
        filter_expr = defn.ffmpeg_filter.format(
            duration=inst.duration_sec,
            **inst.params,
        )

        return TimelineAnimationOp(
            animation_id=inst.animation_id,
            instance_id=inst.instance_id,
            op_type="transition_overlay",
            start_sec=inst.start_sec,
            duration_sec=inst.duration_sec,
            params={
                "ffmpeg_filter": filter_expr,
                "overrides": inst.params,
                "clip_a": inst.target_clip_id,
                "clip_b": inst.target_clip_b_id,
            },
            target_clip_id=inst.target_clip_id,
        )

    @staticmethod
    def _interpolate_keyframes(
        keyframes: list[Keyframe],
        steps: int = 30,
        easing: Any = None,
    ) -> list[dict[str, Any]]:
        """在关键帧之间插值，生成平滑的关键帧序列。"""
        if not keyframes:
            return []

        if len(keyframes) == 1:
            return [keyframes[0].model_dump(mode="json")]

        result: list[dict[str, Any]] = []
        for i in range(len(keyframes) - 1):
            kf_a = keyframes[i]
            kf_b = keyframes[i + 1]
            dur = kf_b.time - kf_a.time
            if dur <= 0:
                result.append(kf_a.model_dump(mode="json"))
                continue

            seg_steps = max(2, int(steps * dur))
            all_props = set(list(kf_a.properties.keys()) + list(kf_b.properties.keys()))

            for j in range(seg_steps):
                t = j / (seg_steps - 1) if seg_steps > 1 else 0
                local_t = kf_a.time + t * dur
                interpolated: dict[str, float] = {}
                for prop in all_props:
                    va = kf_a.properties.get(prop, 0)
                    vb = kf_b.properties.get(prop, 0)
                    if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
                        et = _apply_easing(t, easing)
                        interpolated[prop] = va + (vb - va) * et
                    else:
                        interpolated[prop] = vb if local_t >= (kf_a.time + kf_b.time) / 2 else va
                result.append({
                    "time": round(local_t, 4),
                    "properties": interpolated,
                })

        return result


# ── 缓动函数实现 ─────────────────────────────────────

def _easing_value(name: str) -> str:
    """将 EasingFunction 枚举值转为内部名称。"""
    return name.replace("-", "_")


def _apply_easing(t: float, easing: Any) -> float:
    """对 0-1 范围内的 t 应用缓动函数。"""
    if easing is None:
        return t

    name = easing if isinstance(easing, str) else ""
    if not name or name == "linear":
        return t
    if name == "ease_in" or name == "ease_in_quad":
        return t * t
    if name == "ease_out" or name == "ease_out_quad":
        return t * (2 - t)
    if name == "ease_in_out" or name == "ease_in_out_quad":
        return 2 * t * t if t < 0.5 else -1 + (4 - 2 * t) * t
    if name == "ease_in_cubic":
        return t * t * t
    if name == "ease_out_cubic":
        return (t - 1) ** 3 + 1
    if name == "ease_in_out_cubic":
        return 4 * t * t * t if t < 0.5 else (t - 1) * (2 * t - 2) * (2 * t - 2) + 1
    if name == "ease_in_quart":
        return t * t * t * t
    if name == "ease_out_quart":
        return 1 - (t - 1) ** 4
    if name == "ease_in_out_quart":
        return 8 * t * t * t * t if t < 0.5 else 1 - 8 * (t - 1) * t * t * t
    if name == "ease_out_elastic":
        if t == 0 or t == 1:
            return t
        return 2 ** (-10 * t) * __import__("math").sin((t * 2 * 3.14159) / 3) + 1
    if name == "ease_in_elastic":
        if t == 0 or t == 1:
            return t
        return -(2 ** (10 * (t - 1))) * __import__("math").sin(((t - 1) * 2 * 3.14159) / 3)

    return t
