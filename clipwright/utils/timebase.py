"""整数时间基座（O1 时间模型整数化的第一阶段）。

## 为什么需要它（实测证据）

时间线目前用**浮点秒**表示时间（`schema/timeline.py` 的 `start_sec`/`duration_sec`，
`Timeline.fps: float`）。同一时间点若经两条路径算出——例如「上一段的
`start + duration`」与「下一段的 `start`」——在 `30000/1001` 帧率下**不保证严格相等**：
`scripts/verify_timebase.py` 实测 200000 个帧边界中有 **41202 个（20.6%）**
满足 `frame*(k-1) + frame != frame*k`。

后果是整个代码库里散落的容差：

- `services/render.py` 的 `_apply_default_cut_fades` 用 `eps = 0.05`（50ms）判「相邻」，
  而它要检测的正是 30ms 的切点淡化——容差比被检测对象还大；
- 漂移校正、字幕裁剪、成片时长校验各用各的 `1e-6` / `0.3s` / `1%` 容差；
- 「相邻 / 有间隙 / 有重叠」只能近似判定，无法精确。

整数 tick 让「同一时间点」有**唯一表示**：相等判定就是 `==`，相邻判定就是
`end_ticks == next_start_ticks`，不需要任何 eps。

## 常量选型

``TICKS_PER_SECOND = 120000``：所有常用帧率的**帧周期都是整数 tick**——

===========================  ==============  =============
帧率                          帧周期(tick)     说明
===========================  ==============  =============
24 / 25 / 30 / 50 / 60        5000/4800/4000/2400/2000
23.976 = 24000/1001           5005
29.97  = 30000/1001           4004
59.94  = 60000/1001           2002
119.88 = 120000/1001          1001
100 / 120                     1200 / 1000
===========================  ==============  =============

选 120000 而不是 1e9 或 90000：`1e9`/`90000` 都无法被 1001 系帧率整除
（`90000*1001/30000 = 3003` 恰好可以，但 `90000/1001` 不是整数，
反过来 120000 能让 1001 系帧率得到整数帧周期）。48/1.001（47.952）是唯一例外
（2502.5 tick），它在标准表里标记为非精确。

## 边界与纪律

- 浮点秒只允许出现在**边界**：API 出入参、ffmpeg 过滤器参数、用户可见文本。
  内部相邻性/相等性判定一律用 tick 整数。
- tick 粒度 `1/120000 s ≈ 8.33µs`，比现存的部分容差（`1e-6`）**粗**。
  **不要把 tick 与 `1e-6` 容差混用**：一旦量化为 tick，就按 tick 相等判定，
  不要再加 eps，否则等于把刚消掉的近似又请回来。
- 秒→tick 用 `Decimal(repr(sec))` 四舍五入（默认 half-even），避免二进制噪声
  （直接 `int(sec * 120000)` 会在 `7.007` 这类值上得到 `840839.9999999999 → 840839`）。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from fractions import Fraction
from typing import Any

__all__ = [
    "TICKS_PER_SECOND",
    "TimebaseError",
    "FrameRate",
    "STANDARD_FRAME_RATES",
    "seconds_to_ticks",
    "ticks_to_seconds",
    "frame_to_ticks",
    "ticks_to_frame",
    "snap_ticks",
    "snap_seconds_to_frame",
    "is_frame_aligned",
    "ticks_to_timecode",
    "timecode_to_ticks",
    "format_ticks",
    "as_frame_rate",
]

#: 每秒 tick 数（整数时间的最小单位）
TICKS_PER_SECOND = 120_000

#: 吸附容差（相对）：把 `29.97`、`23.976` 这类习惯写法归到精确有理数帧率
_SNAP_REL_TOL = 1e-3


class TimebaseError(ValueError):
    """时间换算失败（非法帧率、非数值、越界等）。"""


# ── 帧率 ────────────────────────────────────────────────


@dataclass(frozen=True, order=True)
class FrameRate:
    """有理数帧率（分子/分母）。

    `FrameRate(30000, 1001)` 是 29.97；`FrameRate(30)` 是 30。分母为 1001 的
    即 NTSC 系（非整数帧率），时间码默认按 drop-frame 处理。
    """

    numerator: int
    denominator: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.numerator, int) or not isinstance(self.denominator, int):
            raise TimebaseError("FrameRate 分子/分母必须是整数")
        if self.numerator <= 0 or self.denominator <= 0:
            raise TimebaseError("FrameRate 分子/分母必须为正")

    # ── 构造 ──

    @classmethod
    def parse(cls, spec: Any) -> "FrameRate":
        """解析帧率：`30000/1001` / `29.97` / `30` / `Fraction` / `FrameRate`。

        小数写法会吸附到最近的精确标准帧率（容差 0.1%，且整数优先）——
        因此 `30` 仍是 30，不会变成 30000/1001。
        """
        if isinstance(spec, FrameRate):
            return spec
        if isinstance(spec, Fraction):
            return cls(spec.numerator, spec.denominator)
        if isinstance(spec, bool):
            raise TimebaseError("布尔不是合法帧率")
        if isinstance(spec, int):
            return cls(spec)
        if isinstance(spec, str):
            text = spec.strip()
            if not text:
                raise TimebaseError("空帧率字符串")
            if "/" in text:
                num, _, den = text.partition("/")
                try:
                    return cls(int(num.strip()), int(den.strip()))
                except ValueError as exc:  # pragma: no cover - 明确报错
                    raise TimebaseError(f"非法分数帧率: {spec!r}") from exc
            try:
                return cls.from_fps(float(text))
            except ValueError as exc:
                raise TimebaseError(f"非法帧率: {spec!r}") from exc
        if isinstance(spec, float):
            return cls.from_fps(spec)
        raise TimebaseError(f"不支持的帧率类型: {type(spec).__name__}")

    @classmethod
    def from_fps(cls, fps: float) -> "FrameRate":
        """浮点帧率 → 有理数帧率（标准帧率吸附，否则 `limit_denominator`）。"""
        if fps != fps or fps in (float("inf"), float("-inf")):
            raise TimebaseError(f"非法帧率: {fps!r}")
        if fps <= 0:
            raise TimebaseError(f"帧率必须为正: {fps!r}")
        # 整数优先——否则 30.0 会被相对容差吸附到 30000/1001（相对差 0.0999% < 0.1%）
        nearest_int = round(fps)
        if nearest_int > 0 and abs(fps - nearest_int) < 1e-9:
            return cls(int(nearest_int))
        best: FrameRate | None = None
        best_diff = float("inf")
        for candidate in STANDARD_FRAME_RATES:
            diff = abs(candidate.fps - fps) / max(1e-9, candidate.fps)
            if diff < best_diff:
                best, best_diff = candidate, diff
        if best is not None and best_diff <= _SNAP_REL_TOL:
            return best
        frac = Fraction(fps).limit_denominator(1_000_000)
        return cls(frac.numerator, frac.denominator)

    # ── 属性 ──

    @property
    def fps(self) -> float:
        """浮点帧率（仅用于展示/对外兼容；内部换算请用 tick）。"""
        return self.numerator / self.denominator

    @property
    def name(self) -> str:
        """紧凑名：`30000/1001` 或 `30`。"""
        return f"{self.numerator}/{self.denominator}" if self.denominator != 1 else str(self.numerator)

    @property
    def is_ntsc(self) -> bool:
        """是否为 1001 系（非整数）帧率。"""
        return self.denominator != 1 and self.numerator % 1000 == 0

    @property
    def nominal_fps(self) -> int:
        """时间码标称帧率（29.97 → 30），drop-frame 算法用。"""
        return int(round(self.fps))

    @property
    def drop_frame(self) -> bool:
        """是否应默认使用 drop-frame 时间码（NTSC 系为真）。"""
        return self.is_ntsc

    @property
    def exact_ticks_per_frame(self) -> int | None:
        """帧周期的精确 tick 数；无法整除时返回 None（如 48000/1001）。"""
        total = TICKS_PER_SECOND * self.denominator
        if total % self.numerator:
            return None
        return total // self.numerator

    @property
    def ticks_per_frame(self) -> int:
        """帧周期 tick（不可整除时四舍五入，配合 `exact_ticks_per_frame` 判精确性）。"""
        exact = self.exact_ticks_per_frame
        if exact is not None:
            return exact
        return int(
            (Decimal(TICKS_PER_SECOND) * Decimal(self.denominator) / Decimal(self.numerator))
            .to_integral_value(rounding=ROUND_HALF_EVEN)
        )

    @property
    def frame_duration_sec(self) -> float:
        """帧周期秒（浮点，仅在需要展示/兼容时使用）。"""
        return ticks_to_seconds(self.ticks_per_frame)

    # ── 换算 ──

    def frame_index(self, ticks: int) -> int:
        """tick → 帧号（向下取整：落在某帧内的任意 tick 都属于该帧）。"""
        return int(ticks) // self.ticks_per_frame

    def frame_start_ticks(self, frame: int) -> int:
        """帧号 → 该帧起点的 tick。"""
        return int(frame) * self.ticks_per_frame

    def seconds_to_ticks(self, sec: Any) -> int:
        return seconds_to_ticks(sec)

    def ticks_to_seconds(self, ticks: int) -> float:
        return ticks_to_seconds(ticks)

    def snap(self, ticks: int, mode: str = "nearest") -> int:
        return snap_ticks(ticks, self, mode=mode)

    def is_aligned(self, ticks: int) -> bool:
        """tick 是否正好落在帧边界上。"""
        tpf = self.ticks_per_frame
        return tpf > 0 and int(ticks) % tpf == 0

    def __str__(self) -> str:  # pragma: no cover - 展示
        return self.name


#: 标准帧率表（吸附目标）。顺序无关——`from_fps` 取相对差最小者。
STANDARD_FRAME_RATES: tuple[FrameRate, ...] = (
    FrameRate(24000, 1001),   # 23.976
    FrameRate(24),
    FrameRate(25),
    FrameRate(30000, 1001),   # 29.97
    FrameRate(30),
    FrameRate(48000, 1001),   # 47.952（帧周期非整数 tick，唯一例外）
    FrameRate(50),
    FrameRate(60000, 1001),   # 59.94
    FrameRate(60),
    FrameRate(120000, 1001),  # 119.88
    FrameRate(100),
    FrameRate(120),
    FrameRate(15),
    FrameRate(12),
    FrameRate(10),
)


def as_frame_rate(value: Any) -> FrameRate:
    """任意帧率表示（float / str / FrameRate / None）→ `FrameRate`；None → 30fps。"""
    if value is None:
        return FrameRate(30)
    return FrameRate.parse(value)


# ── tick 换算 ───────────────────────────────────────────


def _to_decimal(value: Any) -> Decimal:
    """数值 → Decimal（float 走 `repr` 取最短往返表示，避免二进制噪声）。"""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise TimebaseError("布尔不是合法时间")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, Fraction):
        return Decimal(value.numerator) / Decimal(value.denominator)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise TimebaseError(f"非法时间值: {value!r}")
        return Decimal(repr(value))
    if isinstance(value, str):
        try:
            return Decimal(value.strip())
        except (InvalidOperation, ValueError) as exc:
            raise TimebaseError(f"非法时间字符串: {value!r}") from exc
    raise TimebaseError(f"不支持的时间类型: {type(value).__name__}")


def seconds_to_ticks(sec: Any) -> int:
    """秒 → 整数 tick（四舍五入，默认 half-even）。

    `seconds_to_ticks(1/30)` → 4000（30fps 恰好一帧）；`interval` 累加请用 tick 整数
    相加，不要再经过浮点。

    非法输入（NaN/inf/非数值）抛 `TimebaseError`；调用方若需要「容错取 0」，
    请自行 try/except——静默吞掉非法时间会让错误藏到渲染后期。
    """
    return int(
        (_to_decimal(sec) * TICKS_PER_SECOND).to_integral_value(rounding=ROUND_HALF_EVEN)
    )


def ticks_to_seconds(ticks: int) -> float:
    """整数 tick → 秒（有理数精确求值后转 float，跨平台确定性）。"""
    if isinstance(ticks, float) and ticks == int(ticks):
        ticks = int(ticks)
    if not isinstance(ticks, int):
        raise TimebaseError(f"tick 必须是整数: {ticks!r}")
    return float(Fraction(int(ticks), TICKS_PER_SECOND))


def frame_to_ticks(frame: int, frame_rate: Any) -> int:
    """帧号 → tick。"""
    return as_frame_rate(frame_rate).frame_start_ticks(int(frame))


def ticks_to_frame(ticks: int, frame_rate: Any) -> int:
    """tick → 帧号（向下取整）。"""
    return as_frame_rate(frame_rate).frame_index(int(ticks))


def snap_ticks(ticks: int, frame_rate: Any, mode: str = "nearest") -> int:
    """把 tick 吸附到帧边界（`floor` / `ceil` / `nearest`）。"""
    fr = as_frame_rate(frame_rate)
    tpf = fr.ticks_per_frame
    value = int(ticks)
    quotient, remainder = divmod(value, tpf)  # 负数也被正确处理
    if remainder == 0:
        return value
    if mode == "floor":
        return quotient * tpf
    if mode == "ceil":
        return (quotient + 1) * tpf
    if mode == "nearest":
        return quotient * tpf if remainder * 2 <= tpf else (quotient + 1) * tpf
    raise TimebaseError(f"未知吸附模式: {mode!r}")


def snap_seconds_to_frame(sec: Any, frame_rate: Any, mode: str = "nearest") -> float:
    """秒 → 最近的帧边界（秒，float）。切点对齐用。"""
    return ticks_to_seconds(snap_ticks(seconds_to_ticks(sec), frame_rate, mode=mode))


def is_frame_aligned(sec: Any, frame_rate: Any) -> bool:
    """秒是否正好落在帧边界上（按 tick 判定，不用 eps）。"""
    try:
        return as_frame_rate(frame_rate).is_aligned(seconds_to_ticks(sec))
    except TimebaseError:
        return False


# ── 时间码（EDL / 交付 / 复核图标注） ─────────────────────


def ticks_to_timecode(ticks: int, frame_rate: Any, drop_frame: bool | None = None) -> str:
    """tick → 时间码 `HH:MM:SS:FF`（drop-frame 用 `;` 分隔最后一组）。

    drop-frame 按 SMPTE 规则：每分钟丢 2 帧标号（59.94 丢 4），但每 10 分钟不丢。
    `drop_frame=None` 时对 1001 系帧率自动启用。
    """
    fr = as_frame_rate(frame_rate)
    frame = fr.frame_index(int(ticks))
    nominal = fr.nominal_fps
    use_df = fr.drop_frame if drop_frame is None else bool(drop_frame)
    if use_df and not fr.is_ntsc:
        raise TimebaseError(f"{fr.name} 不是 NTSC 帧率，无法使用 drop-frame 时间码")

    if use_df:
        drop = int(round(fr.fps * 0.066666)) or 2  # 29.97→2, 59.94→4
        frames_per_min = int(round(fr.fps * 60))
        frames_per_10min = int(round(fr.fps * 600))
        ten_min, rest = divmod(frame, frames_per_10min)
        frame += drop * 9 * ten_min
        if rest > drop:
            frame += drop * ((rest - drop) // frames_per_min)

    total_seconds, ff = divmod(frame, nominal)
    hh, rem = divmod(total_seconds, 3600)
    mm, ss = divmod(rem, 60)
    sep = ";" if use_df else ":"
    return f"{hh:02d}:{mm:02d}:{ss:02d}{sep}{ff:02d}"


def timecode_to_ticks(timecode: str, frame_rate: Any, drop_frame: bool | None = None) -> int:
    """时间码 → tick（`ticks_to_timecode` 的逆）。"""
    fr = as_frame_rate(frame_rate)
    if not isinstance(timecode, str):
        raise TimebaseError("时间码必须是字符串")
    text = timecode.strip()
    if not text:
        raise TimebaseError("空时间码")
    parts = text.replace(";", ":").split(":")
    if len(parts) != 4:
        raise TimebaseError(f"非法时间码（应为 HH:MM:SS:FF）: {timecode!r}")
    try:
        hh, mm, ss, ff = (int(p) for p in parts)
    except ValueError as exc:
        raise TimebaseError(f"非法时间码: {timecode!r}") from exc
    if min(hh, mm, ss, ff) < 0 or ss > 59 or mm > 59 or ff >= fr.nominal_fps:
        raise TimebaseError(f"时间码越界: {timecode!r}")
    use_df = fr.drop_frame if drop_frame is None else bool(drop_frame)
    if ";" in text:
        use_df = True
    nominal = fr.nominal_fps
    frame = ((hh * 60 + mm) * 60 + ss) * nominal + ff
    if use_df:
        drop = int(round(fr.fps * 0.066666)) or 2
        total_minutes = hh * 60 + mm
        frame -= drop * (total_minutes - total_minutes // 10)
        if frame < 0:
            raise TimebaseError(f"drop-frame 时间码越界: {timecode!r}")
    return frame_to_ticks(frame, fr)


# ── 格式化（ffmpeg 参数 / 标注文本） ──────────────────────


def format_ticks(ticks: int, precision: int | None = None) -> str:
    """tick → 紧凑秒字符串（供 ffmpeg 过滤器参数使用）。

    默认按 `%g`（6 位有效数字）输出，与 `render._fmt_sec` 一致；指定 `precision`
    时按定点输出（如 `precision=3` → `12.500`）。
    """
    sec = ticks_to_seconds(int(ticks))
    if precision is not None:
        return f"{sec:.{int(precision)}f}"
    return f"{sec:g}"
