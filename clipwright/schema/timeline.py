"""时间线数据模型 — 前后端统一的 JSON 格式。

这是整个系统最核心的数据契约：Agent 输出时间线、编辑器读取时间线、
用户修改时间线，都在同一个数据模型上操作。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator

from clipwright.utils.timebase import (
    TICKS_PER_SECOND,
    FrameRate,
    seconds_to_ticks,
    snap_ticks,
    ticks_to_seconds,
    ticks_to_timecode,
)


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
    eq_preset: Optional[str] = Field(default=None, description="音频 EQ 预设名称。【预留未实现】混音端无 EQ 滤镜，设置无效果")

    # 音频淡入淡出（M6：仅 audio / waveform 类型，渲染时 afade 应用）
    audio_fade_in_sec: Optional[float] = Field(default=None, ge=0, description="音频淡入时长（秒）")
    audio_fade_out_sec: Optional[float] = Field(default=None, ge=0, description="音频淡出时长（秒）")

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

    # 蒙版（M4：仅 video / image 类型，渲染时裁剪到形状内）
    mask_type: Optional[str] = Field(
        default=None, pattern="^(none|rect|rectangle|ellipse|polygon|star|heart)?$",
        description=("蒙版类型 none/rect/rectangle/ellipse/polygon/star/heart"
                     "（ellipse/polygon 导出经 geq alpha 遮罩；star/heart 目前仅预览生效，"
                     "导出会记警告并跳过蒙版）"),
    )
    mask_rect: Optional[dict] = Field(
        default=None,
        description="归一化蒙版矩形 {x, y, w, h}，各值范围 0-1；polygon/star/heart 用中心与半径（w/h 视为宽高占比）",
    )
    # O-遮罩升级：形状参数与边缘处理
    mask_points: Optional[int] = Field(
        default=None, ge=3, le=12,
        description="polygon 边数 / star 角数（默认 5）",
    )
    mask_rotation: Optional[float] = Field(
        default=None, ge=-180, le=180,
        description="蒙版旋转角度（度，正为顺时针）",
    )
    mask_feather: Optional[float] = Field(
        default=None, ge=0, le=50,
        description="边缘羽化宽度（像素，0=硬边）；导出用 geq 在隐函数上做线性过渡",
    )
    mask_inverted: Optional[bool] = Field(
        default=None,
        description="反相蒙版（保留形状之外，形状内部透明）",
    )

    # 嵌套序列（C3：片段内嵌子时间线，预览递归合成）
    nested_timeline: Optional[dict] = Field(
        default=None,
        description="内嵌子时间线（完整 Timeline 结构）。【预留未实现】渲染端不递归合成，设置无效果",
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

    # 编组（M2：同组片段一起移动/删除）
    group_id: Optional[str] = Field(default=None, max_length=64, description="编组 ID")

    # 扩展元数据
    metadata: dict[str, Any] = Field(default_factory=dict, description="扩展元数据")

    model_config = {"use_enum_values": True, "extra": "allow"}

    @model_validator(mode="before")
    @classmethod
    def _accept_tick_input(cls, data: Any) -> Any:
        """接受 tick 原生输入（`start_ticks` / `duration_ticks` / `source_offset_ticks`）。

        O1 第二阶段：客户端可以直接用整数 tick 提交时间，服务端折算为浮点秒。
        **浮点秒仍是唯一序列化真值**——tick 输入不落库、不回写 JSON，避免同一段时间
        出现两个真值来源（改一个忘另一个 = 静默漂移）。显式给出秒字段时秒优先。
        """
        if not isinstance(data, dict):
            return data
        data = dict(data)
        for tick_key, sec_key in (
            ("start_ticks", "start_sec"),
            ("duration_ticks", "duration_sec"),
            ("source_offset_ticks", "source_offset_sec"),
        ):
            ticks = data.pop(tick_key, None)
            if ticks is None or data.get(sec_key) is not None:
                continue
            if isinstance(ticks, bool) or not isinstance(ticks, int):
                raise ValueError(f"{tick_key} 必须是整数 tick（收到 {type(ticks).__name__}）")
            data[sec_key] = ticks_to_seconds(ticks)
        return data

    # ── 整数 tick 访问器（O1） ─────────────────────────────
    # tick 与帧率无关，所以 Clip 能独立换算；帧对齐判定需要 fps，放在 Timeline 上。

    @property
    def start_ticks(self) -> int:
        """起始时间的整数 tick。"""
        return seconds_to_ticks(self.start_sec)

    @property
    def duration_ticks(self) -> int:
        """持续时长的整数 tick。"""
        return seconds_to_ticks(self.duration_sec)

    @property
    def source_offset_ticks(self) -> int:
        """素材内偏移的整数 tick。"""
        return seconds_to_ticks(self.source_offset_sec)

    @property
    def end_sec(self) -> float:
        """结束时间（秒）。"""
        return self.start_sec + self.duration_sec

    @property
    def end_ticks(self) -> int:
        """结束时间的整数 tick（= start + duration，恒精确）。"""
        return self.start_ticks + self.duration_ticks


class Track(BaseModel):
    """时间轴轨道。"""
    id: str = Field(description="全局唯一轨道 ID")
    name: str = Field(default="", description="轨道名称")
    kind: ClipKind = Field(description="轨道类型（限制其子片段类型）")
    index: int = Field(ge=0, description="轨道在时间轴中的顺序索引")
    clips: list[Clip] = Field(default_factory=list)
    locked: bool = Field(default=False)
    muted: bool = Field(default=False)
    # Phase 2.3: 轨道级元数据（NEL 配音事件线、BPM 等，供动画/渲染消费）
    metadata: dict[str, Any] = Field(default_factory=dict, description="轨道扩展元数据")

    model_config = {"use_enum_values": True, "extra": "allow"}


class TimelineMarker(BaseModel):
    """时间轴标记（M8：持久化 + 命名）。"""
    time: float = Field(ge=0, description="标记时间点（秒）")
    name: str = Field(default="", max_length=64, description="标记名称（可选）")


class Timeline(BaseModel):
    """完整时间线 — Agent 输出与编辑器加载的统一格式。"""
    id: str = Field(default="", description="时间线 ID")
    width: int = Field(default=1920, ge=1, description="视频宽度（像素）")
    height: int = Field(default=1080, ge=1, description="视频高度（像素）")
    fps: float = Field(default=30.0, gt=0, description="帧率")
    duration_sec: float = Field(default=0, ge=0, description="总时长（秒）")
    tracks: list[Track] = Field(default_factory=list)
    markers: list[TimelineMarker] = Field(default_factory=list, description="时间轴标记列表（M8）")
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="时间线扩展元数据（如 target_loudness_lufs → 渲染响度归一目标）")

    model_config = {"use_enum_values": True, "extra": "allow"}

    @model_validator(mode="before")
    @classmethod
    def _accept_rate_input(cls, data: Any) -> Any:
        """接受有理数帧率输入（`fps_num` / `fps_den`）。

        O1 第二阶段：客户端可用分数帧率精确表达 29.97（`30000/1001`），服务端折算成
        `fps` 浮点值；`frame_rate` 属性再吸附回精确有理数（吸附是幂等的），因此不丢精度。
        显式给出 `fps` 时 `fps` 优先；非整数输入直接拒绝（避免 `fps_num=30000.5` 这类静默丢精度）。
        """
        if not isinstance(data, dict):
            return data
        num = data.pop("fps_num", None)
        den = data.pop("fps_den", None)
        if num is None and den is None:
            return data
        data = dict(data)
        if data.get("fps") is not None:
            return data
        if den is None:
            den = 1
        for label, value in (("fps_num", num), ("fps_den", den)):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{label} 必须是整数（收到 {type(value).__name__}）")
        if num is None or num <= 0 or den <= 0:
            raise ValueError("fps_num/fps_den 必须为正整数")
        data["fps"] = num / den
        return data

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

    # ── 整数时间基（O1 第一阶段） ───────────────────────────
    # 字段仍是浮点秒（API/前端兼容），但**判定**（相等/相邻/帧对齐）请走 tick：
    # `start_sec + duration_sec == next.start_sec` 在 30000/1001 下约 20% 概率不成立
    # （见 utils/timebase.py 的实测说明）。

    @property
    def frame_rate(self) -> FrameRate:
        """有理数帧率（29.97 → 30000/1001）。"""
        return FrameRate.from_fps(float(self.fps))

    @property
    def ticks_per_second(self) -> int:
        return TICKS_PER_SECOND

    def to_ticks(self, sec: float) -> int:
        """秒（浮点）→ 整数 tick。"""
        return seconds_to_ticks(sec)

    def to_seconds(self, ticks: int) -> float:
        """整数 tick → 秒（浮点）。"""
        return ticks_to_seconds(ticks)

    @property
    def total_duration_ticks(self) -> int:
        """总时长（tick 整数，按浮点总时长量化）。"""
        return seconds_to_ticks(self.total_duration_sec)

    @property
    def frame_count(self) -> int:
        """总帧数（按实际时长向下取整）。"""
        return self.frame_rate.frame_index(seconds_to_ticks(self.total_duration_sec))

    def frame_aligned_duration_sec(self) -> float:
        """把总时长对齐到帧边界（向上取整，避免最后一帧被截掉）。"""
        return ticks_to_seconds(
            snap_ticks(seconds_to_ticks(self.total_duration_sec), self.frame_rate, mode="ceil")
        )

    def timecode(self, sec: float, *, drop_frame: bool | None = None) -> str:
        """秒 → 时间码（`HH:MM:SS:FF`；NTSC 系默认 drop-frame）。"""
        return ticks_to_timecode(seconds_to_ticks(sec), self.frame_rate, drop_frame=drop_frame)

    # ── 轨道相邻性（整数 tick 精确判定，O1 第二阶段） ─────────

    def adjacency_issues(self, *, tolerance_ticks: int = 0, limit: int = 20) -> list[dict[str, Any]]:
        """逐轨检查片段首尾相接情况，返回 `[{track_id, kind, prev, next, delta_ticks, delta_sec}]`。

        `kind`：
        - ``overlap``：下一段起点早于上一段终点（剪辑顺序/裁剪会被打乱）
        - ``gap``：下一段起点晚于上一段终点（渲染端是顺序拼接，间隙**不会**渲染成黑帧）
        - （相接不产出条目）

        判定在整数 tick 上做（`tolerance_ticks=0` 即严格相接），不再用浮点 eps——
        同一时间点经 `start + duration` 与下一段 `start` 两条路径算出时，浮点秒在
        29.97fps 下约 20% 概率不严格相等（见 `utils/timebase.py`）。
        `tolerance_ticks` 供调用方显式放宽（例如容忍 1 帧内的排布抖动）。
        """
        issues: list[dict[str, Any]] = []
        for track in self.tracks:
            clips = sorted(
                (c for c in track.clips if getattr(c, "enabled", True)),
                key=lambda c: c.start_ticks,
            )
            for prev, cur in zip(clips, clips[1:]):
                delta = cur.start_ticks - prev.end_ticks
                if abs(delta) <= tolerance_ticks:
                    continue
                issues.append({
                    "track_id": track.id,
                    "kind": "overlap" if delta < 0 else "gap",
                    "prev": prev.id,
                    "next": cur.id,
                    "delta_ticks": delta,
                    "delta_sec": ticks_to_seconds(delta),
                })
                if len(issues) >= limit:
                    return issues
        return issues

