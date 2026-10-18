"""镜头下限策略（D23 的三个候选，供拍板后一键切换）。

背景：`min_shot_ms` 在类型规范里是**节奏参数**，此前只钳 LLM 剪辑档案的
`base_shot_ms`，**不夹逐段时长**——真机因此跑出过低于下限的片段（见 PRD D23）。
本模块把"低于下限怎么办"做成一个显式策略，默认 `off`（不改变现有行为）：

- ``off``：什么都不做（默认，零行为变化）；
- ``record``：片段照旧，但**逐条记 C2 冲突**——"允许低于下限，但必须留痕"；
- ``extend``：把场景时长抬到下限（画面被拉长，成片总时长随之增长）；
- ``loop``：同样抬到下限，但标记 ``fill=loop``（**渲染层尚未消费这个标记**，
  只是把意图写进元数据；真要循环填充需要渲染侧支持，已单列为待办）。

为什么要有 `record`：D23 里"允许低于下限但记 C2"这条最诚实——它不假装参数生效，
而是把"类型要求"和"实际产出"的缝隙显式记录下来，供后续重做/人工确认。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

POLICIES: tuple[str, ...] = ("off", "record", "extend", "loop")


@dataclass
class ShotFloorOutcome:
    durations: list[float]
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    changed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "durations": [round(value, 3) for value in self.durations],
            "conflicts": self.conflicts,
            "changed": self.changed,
        }


def normalize_policy(policy: str | None) -> str:
    value = str(policy or "off").strip().lower()
    return value if value in POLICIES else "off"


def apply_scene_floor(
    durations: Iterable[float],
    min_shot_sec: float,
    *,
    policy: str = "off",
) -> ShotFloorOutcome:
    """按策略处理"场景时长低于镜头下限"的情况。

    ``durations`` 是**秒**（与时间线同一单位），``min_shot_sec`` 也是秒。
    """
    values = [max(0.0, float(value)) for value in durations]
    chosen = normalize_policy(policy)
    floor = max(0.0, float(min_shot_sec or 0.0))
    if chosen == "off" or floor <= 0:
        return ShotFloorOutcome(durations=values, changed=False)

    conflicts: list[dict[str, Any]] = []
    adjusted: list[float] = []
    changed = False
    for index, value in enumerate(values):
        if value + 1e-9 >= floor:
            adjusted.append(value)
            continue
        conflicts.append({
            "code": "C2",
            "key": "min_shot_ms",
            "index": index,
            "value_sec": round(value, 3),
            "floor_sec": round(floor, 3),
            "policy": chosen,
            "message": (
                f"第 {index} 段时长 {value:.2f}s 低于类型下限 {floor:.2f}s"
                + ("，已抬到下限" if chosen in ("extend", "loop") else "，按策略仅记录")
            ),
        })
        if chosen in ("extend", "loop"):
            adjusted.append(floor)
            changed = True
        else:  # record
            adjusted.append(value)
    return ShotFloorOutcome(durations=adjusted, conflicts=conflicts, changed=changed)


def fill_mode(policy: str) -> str:
    """扩展时的填充方式标记（渲染层目前只认 stretch，loop 待支持）。"""
    chosen = normalize_policy(policy)
    if chosen == "loop":
        return "loop"
    if chosen == "extend":
        return "stretch"
    return ""


__all__ = [
    "POLICIES",
    "ShotFloorOutcome",
    "apply_scene_floor",
    "fill_mode",
    "normalize_policy",
]
