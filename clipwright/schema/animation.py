"""动画 JSON 规范 — 定义屏幕上展示的动画与转场动画。

## 动画类型

### 1. 屏幕上展示的动画 (onscreen)

作用于文字/图片/形状等视觉元素的动画，通过关键帧插值驱动：

```json
{
  "animation_id": "fade_in_slide_up",
  "name": "淡入上滑",
  "version": "1.0.0",
  "type": "onscreen",
  "target": "text",
  "duration_sec": 0.6,
  "easing": "ease-out-cubic",
  "keyframes": [
    {"time": 0.0, "properties": {"opacity": 0, "translate_y": 50, "blur": 4}},
    {"time": 1.0, "properties": {"opacity": 1, "translate_y": 0, "blur": 0}}
  ]
}
```

### 2. 转场动画 (transition)

作用于两个视频片段之间的过渡效果：

```json
{
  "animation_id": "push_left",
  "name": "左推",
  "version": "1.0.0",
  "type": "transition",
  "duration_sec": 0.4,
  "easing": "ease-in-out",
  "ffmpeg_filter": "drawtext=text='{text}':fontsize={font_size}:x=(w-text_w)/2:y=(h-text_h)/2:enable='between(t,{start},{end})'"
}
```
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ── 枚举 ──────────────────────────────────────────────

class AnimationType(str, Enum):
    """动画类型。"""
    ONSCREEN = "onscreen"
    TRANSITION = "transition"


class AnimationTarget(str, Enum):
    """动画作用目标。"""
    TEXT = "text"
    IMAGE = "image"
    SHAPE = "shape"
    VIDEO = "video"
    ANY = "any"


class EasingFunction(str, Enum):
    """缓动函数。"""
    LINEAR = "linear"
    EASE_IN = "ease-in"
    EASE_OUT = "ease-out"
    EASE_IN_OUT = "ease-in-out"
    EASE_IN_QUAD = "ease-in-quad"
    EASE_OUT_QUAD = "ease-out-quad"
    EASE_IN_OUT_QUAD = "ease-in-out-quad"
    EASE_IN_CUBIC = "ease-in-cubic"
    EASE_OUT_CUBIC = "ease-out-cubic"
    EASE_IN_OUT_CUBIC = "ease-in-out-cubic"
    EASE_IN_QUART = "ease-in-quart"
    EASE_OUT_QUART = "ease-out-quart"
    EASE_IN_OUT_QUART = "ease-in-out-quart"
    EASE_IN_ELASTIC = "ease-in-elastic"
    EASE_OUT_ELASTIC = "ease-out-elastic"


# ── 属性定义（描述一个可动画属性的元信息）────────────

class PropertyDef(BaseModel):
    """动画属性的定义。"""
    type: str = Field(description="float / int / string / bool / color")
    default: Any = Field(default=None)
    range: Optional[list[float]] = Field(
        default=None, description="数值范围 [min, max]"
    )
    unit: str = Field(default="", description="单位：percent / px / degrees / sec")
    enum: Optional[list[str]] = Field(default=None, description="可选值列表（string 类型时）")
    description: str = Field(default="")

    model_config = {"use_enum_values": True}


# ── 关键帧 ────────────────────────────────────────────

class Keyframe(BaseModel):
    """动画关键帧。

    time: 0.0 ~ 1.0，相对 duration_sec 的比例位置
    properties: 属性名 → 属性值（在 time 时刻的值）
    """
    time: float = Field(ge=0.0, le=1.0, description="关键帧时间位置 (0~1)")
    properties: dict[str, Any] = Field(
        default_factory=dict,
        description="属性名 → 值 {opacity: 1, translate_y: 0, ...}",
    )


# ── 屏幕动画定义 ───────────────────────────────────────

class OnscreenAnimationDef(BaseModel):
    """屏幕上展示的动画定义。

    通过关键帧之间的插值驱动视觉元素的属性变化。
    """
    animation_id: str = Field(description="动画唯一 ID")
    name: str = Field(default="", description="显示名称")
    version: str = Field(default="1.0.0")
    type: AnimationType = Field(default=AnimationType.ONSCREEN)
    target: AnimationTarget = Field(
        default=AnimationTarget.ANY,
        description="适用的视觉元素类型",
    )
    duration_sec: float = Field(
        default=0.5, gt=0, description="动画持续秒数"
    )
    easing: EasingFunction = Field(
        default=EasingFunction.LINEAR,
        description="插值缓动函数",
    )
    easing: EasingFunction = Field(
        default=EasingFunction.LINEAR,
        description="插值缓动函数",
    )
    keyframes: list[Keyframe] = Field(
        default_factory=lambda: [
            Keyframe(time=0.0, properties={"opacity": 0}),
            Keyframe(time=1.0, properties={"opacity": 1}),
        ],
        description="关键帧序列，time 从 0 到 1",
    )
    properties_meta: dict[str, PropertyDef] = Field(
        default_factory=dict,
        description="可动画属性的元信息定义",
    )

    model_config = {"use_enum_values": True}


# ── 转场动画定义 ───────────────────────────────────────

class TransitionDirection(str, Enum):
    """转场方向。"""
    LEFT = "left"
    RIGHT = "right"
    UP = "up"
    DOWN = "down"
    CENTER = "center"


class TransitionAnimationDef(BaseModel):
    """转场动画定义。

    定义两个视频片段之间的过渡效果。
    """
    animation_id: str = Field(description="动画唯一 ID")
    name: str = Field(default="", description="显示名称")
    version: str = Field(default="1.0.0")
    type: AnimationType = Field(default=AnimationType.TRANSITION)
    duration_sec: float = Field(
        default=0.3, gt=0, description="转场持续秒数"
    )
    easing: EasingFunction = Field(default=EasingFunction.LINEAR)

    # FFmpeg 滤镜表达式
    # 可用变量: {duration}, {width}, {height}, {softness}
    ffmpeg_filter: str = Field(
        default="",
        description="FFmpeg filter 表达式，为空表示硬切",
    )

    # 参数定义
    params: dict[str, PropertyDef] = Field(
        default_factory=dict,
        description="转场可调参数",
    )

    model_config = {"use_enum_values": True}


# ── 统一动画定义 ───────────────────────────────────────

class AnimationDef(BaseModel):
    """统一动画定义（onscreen 或 transition）。"""
    animation_id: str
    name: str = ""
    version: str = Field(default="1.0.0")
    type: AnimationType
    description: str = Field(default="")
    author: str = Field(default="")
    tags: list[str] = Field(default_factory=list)

    # onscreen 字段
    target: AnimationTarget = Field(default=AnimationTarget.ANY)
    duration_sec: float = Field(default=0.5, ge=0)
    easing: EasingFunction = Field(default=EasingFunction.LINEAR)
    keyframes: list[Keyframe] = Field(default_factory=list)
    properties_meta: dict[str, PropertyDef] = Field(default_factory=dict)

    # transition 字段
    ffmpeg_filter: str = Field(default="")
    params: dict[str, PropertyDef] = Field(default_factory=dict)

    model_config = {"use_enum_values": True}

    @classmethod
    def from_onscreen(cls, defn: OnscreenAnimationDef) -> AnimationDef:
        """从 onscreen 定义创建。"""
        return cls(
            animation_id=defn.animation_id,
            name=defn.name,
            version=defn.version,
            type=defn.type,
            target=defn.target,
            duration_sec=defn.duration_sec,
            easing=defn.easing,
            keyframes=defn.keyframes,
            properties_meta=defn.properties_meta,
        )

    @classmethod
    def from_transition(cls, defn: TransitionAnimationDef) -> AnimationDef:
        """从 transition 定义创建。"""
        return cls(
            animation_id=defn.animation_id,
            name=defn.name,
            version=defn.version,
            type=defn.type,
            duration_sec=defn.duration_sec,
            easing=defn.easing,
            ffmpeg_filter=defn.ffmpeg_filter,
            params=defn.params,
        )


# ── 运行时：动画实例 ───────────────────────────────────

class AnimationInstance(BaseModel):
    """动画在时间线上的具体实例。

    由 AnimationAgent 从 AnimationDef 生成，绑定到具体时间点。
    """
    animation_id: str = Field(description="使用的动画定义 ID")
    instance_id: str = Field(default="", description="实例唯一 ID")
    type: AnimationType

    # 时间位置
    start_sec: float = Field(ge=0, description="开始时间（秒）")
    duration_sec: float = Field(gt=0, description="持续秒数")

    # 参数覆盖（可覆盖动画定义的默认参数）
    params: dict[str, Any] = Field(default_factory=dict)

    # 作用对象
    target_clip_id: str = Field(default="", description="作用的目标 clip ID")
    target_clip_b_id: str = Field(default="", description="转场的第二段 clip ID")


class AnimationSequence(BaseModel):
    """动画编排序列 — 描述整条时间线上的动画计划。"""
    onscreen_animations: list[AnimationInstance] = Field(
        default_factory=list,
        description="屏幕动画序列",
    )
    transition_animations: list[AnimationInstance] = Field(
        default_factory=list,
        description="转场动画序列",
    )
