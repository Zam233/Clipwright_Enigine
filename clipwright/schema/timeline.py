"""时间线数据模型 — 前后端统一的 JSON 格式。

这是整个系统最核心的数据契约：Agent 输出时间线、编辑器读取时间线、
用户修改时间线，都在同一个数据模型上操作。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class ClipKind(str, Enum):
    """剪辑片段类型。"""
    VIDEO = "video"
    AUDIO = "audio"
    TEXT = "text"
    IMAGE = "image"
    CAPTION = "caption"
    SHAPE = "shape"
    WAVEFORM = "waveform"
    ANIMATION = "animation"


class TransitionType(str, Enum):
    HARD_CUT = "hard_cut"
    FADE = "fade"
    DISSOLVE = "dissolve"
    GLITCH = "glitch"
    PIXEL_DISSOLVE = "pixel_dissolve"
    SLIDE = "slide"
    WIPE = "wipe"


class ImageFit(str, Enum):
    COVER = "cover"
    CONTAIN = "contain"
    FREE = "free"


class TextAlign(str, Enum):
    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"


class Clip(BaseModel):
    """时间轴上的单个剪辑片段。"""
    id: str = Field(description="全局唯一 ID")
    kind: ClipKind = Field(description="片段类型")
    asset_id: str = Field(description="引用的素材 ID")
    track_id: str = Field(description="所属轨道 ID")

    # 时间
    start_sec: float = Field(ge=0, description="在时间轴上的起始时间（秒）")
    duration_sec: float = Field(gt=0, description="持续时长（秒）")
    source_offset_sec: float = Field(default=0, ge=0, description="素材内的起始偏移（秒）")

    # 速度 / 音量
    speed: float = Field(default=1.0, gt=0, description="播放速度倍率")
    volume: float = Field(default=1.0, ge=0, description="音量 0-1")
    opacity: float = Field(default=1.0, ge=0, le=1, description="不透明度 0-1")

    # 混合 / 启用 / 标签
    blend_mode: Optional[str] = Field(default=None, description="混合模式 normal/multiply/screen/overlay 等")
    enabled: bool = Field(default=True, description="片段是否启用（禁用的片段不参与渲染）")
    label_color: Optional[str] = Field(default=None, description="片段标签颜色（hex）")
    notes: Optional[str] = Field(default=None, description="片段备注")

    # 音频 EQ 预设
    eq_preset: Optional[str] = Field(default=None, description="音频 EQ 预设名称")

    # 视频特效滤镜（仅 video / image 类型）
    fx_brightness: Optional[float] = Field(default=None, ge=0, le=2, description="亮度 0-2，默认 1")
    fx_contrast: Optional[float] = Field(default=None, ge=0, le=2, description="对比度 0-2，默认 1")
    fx_saturation: Optional[float] = Field(default=None, ge=0, le=2, description="饱和度 0-2，默认 1")
    fx_blur: Optional[float] = Field(default=None, ge=0, le=10, description="模糊半径 px")
    fx_hue: Optional[float] = Field(default=None, ge=0, le=360, description="色相旋转角度")

    # 画面布局（仅 video / image 类型）
    image_fit: Optional[ImageFit] = Field(default=None)
    image_rect: Optional[dict] = Field(
        default=None,
        description="归一化矩形 {x, y, w, h}，各值范围 0-1",
    )

    # 文字内容（仅 text / caption 类型）
    text: Optional[str] = Field(default=None)
    font: Optional[str] = Field(default=None)
    font_size: Optional[float] = Field(default=None, gt=0)
    font_color: Optional[str] = Field(default=None, pattern="^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$")
    text_align: Optional[TextAlign] = Field(default=None)

    # 字幕样式（仅 text / caption 类型，任务 28 新增，可选字段向后兼容）
    font_weight: Optional[str] = Field(default=None, description="字重 normal/bold 等")
    font_italic: Optional[bool] = Field(default=None, description="是否斜体")
    letter_spacing: Optional[float] = Field(default=None, description="字间距 px")
    stroke_width: Optional[float] = Field(default=None, ge=0, description="描边宽度 px")
    stroke_color: Optional[str] = Field(default=None, pattern="^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$", description="描边颜色（hex）")
    shadow_x: Optional[float] = Field(default=None, description="阴影水平偏移 px")
    shadow_y: Optional[float] = Field(default=None, description="阴影垂直偏移 px")
    shadow_color: Optional[str] = Field(default=None, pattern="^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$", description="阴影颜色（hex）")
    shadow_blur: Optional[float] = Field(default=None, ge=0, description="阴影模糊半径 px")
    glow_color: Optional[str] = Field(default=None, pattern="^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$", description="发光颜色（hex）")
    glow_width: Optional[float] = Field(default=None, ge=0, description="发光宽度 px")

    # 转场（P0-3: 白名单模式 — ffmpeg xfade 过渡名均为小写标识符，禁止 filter 语法字符注入）
    transition_in: Optional[str] = Field(
        default=None, pattern="^[a-z][a-z0-9_]{0,31}$", description="入场转场类型 TransitionType"
    )
    transition_out: Optional[str] = Field(default=None, pattern="^[a-z][a-z0-9_]{0,31}$")
    transition_duration_sec: Optional[float] = Field(default=None, ge=0)

    # 形状（仅 shape 类型）
    shape: Optional[str] = Field(default=None, description="形状 rect / ellipse")
    fill: Optional[str] = Field(default=None, pattern="^#[0-9a-fA-F]{6}$")

    # 波形（仅 waveform 类型）
    bar_count: Optional[int] = Field(default=None, ge=8, le=256)
    bar_width: Optional[float] = Field(default=None, ge=0.1, le=1)

    # 关键帧动画（per-clip 属性随时间变化）
    keyframes: list[dict[str, Any]] = Field(
        default_factory=list,
        description="关键帧序列 [{time: 0.0, properties: {opacity: 0, scale_x: 0.5, ...}}, ...]",
    )

    # 扩展元数据
    metadata: dict[str, Any] = Field(default_factory=dict, description="扩展元数据")

    model_config = {"use_enum_values": True, "extra": "allow"}


class Track(BaseModel):
    """时间轴轨道。"""
    id: str = Field(description="全局唯一轨道 ID")
    name: str = Field(default="", description="轨道名称")
    kind: ClipKind = Field(description="轨道类型（限制其子片段类型）")
    index: int = Field(ge=0, description="轨道在时间轴中的顺序索引")
    clips: list[Clip] = Field(default_factory=list)
    locked: bool = Field(default=False)
    muted: bool = Field(default=False)

    model_config = {"use_enum_values": True}


class Timeline(BaseModel):
    """完整时间线 — Agent 输出与编辑器加载的统一格式。"""
    id: str = Field(default="", description="时间线 ID")
    width: int = Field(default=1920, ge=1, description="视频宽度（像素）")
    height: int = Field(default=1080, ge=1, description="视频高度（像素）")
    fps: float = Field(default=30.0, gt=0, description="帧率")
    duration_sec: float = Field(default=0, ge=0, description="总时长（秒）")
    tracks: list[Track] = Field(default_factory=list)

    @property
    def total_duration_sec(self) -> float:
        """计算所有轨道中最长的结束时间。"""
        if not self.tracks:
            return 0.0
        max_end = 0.0
        for track in self.tracks:
            for clip in track.clips:
                end = clip.start_sec + clip.duration_sec
                if end > max_end:
                    max_end = end
        return max_end
