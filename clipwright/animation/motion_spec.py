"""LLM MotionSpec schema（声明式，不允许 HTML/JS）。"""

from __future__ import annotations

from typing import Any, Literal

from dataclasses import dataclass, field
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Easing(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["linear", "named", "cubic-bezier", "steps", "hold", "spring"] = "linear"
    name: str | None = None
    x1: float = 0.25
    y1: float = 0.1
    x2: float = 0.25
    y2: float = 1.0
    count: int = 1
    position: Literal["start", "end", "both"] = "end"
    stiffness: float = 170.0
    damping: float = 18.0
    mass: float = 1.0
    velocity: float = 0.0

    @model_validator(mode="after")
    def _check(self):
        if self.type == "cubic-bezier":
            if not (0 <= self.x1 <= 1 and 0 <= self.x2 <= 1):
                raise ValueError("cubic-bezier x1/x2 must be 0..1")
        if self.type == "steps" and not (1 <= self.count <= 60):
            raise ValueError("steps.count must be 1..60")
        if self.type == "spring":
            if not (20 <= self.stiffness <= 1000):
                raise ValueError("spring.stiffness must be 20..1000")
            if not (2 <= self.damping <= 100):
                raise ValueError("spring.damping must be 2..100")
            if not (0.2 <= self.mass <= 10):
                raise ValueError("spring.mass must be 0.2..10")
        return self


class Keyframe(BaseModel):
    """keyframe 允许动态播放属性；未知属性由 validator 按白名单拒绝。"""

    model_config = ConfigDict(extra="allow")
    time: float = Field(ge=0)
    easing: Easing | str | None = None


class MotionPath(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["line", "polyline", "cubic-bezier", "arc", "circle"] = "line"
    points: list[dict[str, float]] = Field(default_factory=list)
    orient: bool = False
    progress: list[dict[str, Any]] = Field(default_factory=list)


class TextMotionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unit: Literal["char", "word", "line", "range"] = "char"
    order: Literal["forward", "reverse", "center", "random"] = "forward"
    stagger: float = 0.0
    unit_duration: float = 0.3
    seed: int = 0
    cursor: bool = False


class StyleSpec(BaseModel):
    model_config = ConfigDict(extra="allow")


class Layer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    type: Literal["text", "shape", "line", "image", "group"] = "text"
    parent: str | None = None
    z_index: int = 0
    content: str = ""
    layout: dict[str, Any] = Field(default_factory=dict)
    style: dict[str, Any] = Field(default_factory=dict)
    text_motion: TextMotionSpec | None = None
    keyframes: list[Keyframe] = Field(default_factory=list)
    tracks: list[dict[str, Any]] = Field(default_factory=list)
    motion_path: MotionPath | None = None
    effects: list[dict[str, Any]] = Field(default_factory=list)
    blend_mode: str = "normal"


class MotionSpec(BaseModel):
    schema_: Literal["motion_spec/1"] = Field(default="motion_spec/1", alias="schema")
    spec_id: str = ""
    duration_sec: float = Field(gt=0)
    canvas: dict[str, Any] = Field(default_factory=lambda: {"width": 1920, "height": 1080, "fps": 30})
    requires: list[str] = Field(default_factory=list)
    layers: list[Layer] = Field(default_factory=list, min_length=1, max_length=8)
    camera: dict[str, Any] | None = None
    meta: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


@dataclass
class MotionAuthorRequest:
    text: str
    style: dict[str, Any]
    intent: dict[str, Any]
    width: int
    height: int
    duration_sec: float
    allowed_features: set[str] = field(default_factory=set)
    caption_band_px: int = 160
    seed: int = 0
    capabilities: dict[str, Any] = field(default_factory=dict)
    scene_context: dict[str, Any] = field(default_factory=dict)
