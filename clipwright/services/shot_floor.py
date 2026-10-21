"""镜头节奏策略（D23 拍板后的默认实现 + 三个历史候选）。

背景：`min_shot_ms`/`max_shot_ms` 在类型规范里是**节奏参数**，此前只钳 LLM 剪辑
档案的 `base_shot_ms`，**不夹逐段时长**——真机因此跑出过低于下限的片段（PRD D23），
也让「歌词 MV」类型跑出 6.1s 的平均镜头（V35 不达标）。

D23 拍板（2026-10-18）：**保总时长**优先，两个方向都不许把成片悄悄拉长——
- 低于下限 → ``merge``：把相邻场景**合并**到下限以上（镜头变少变长，总时长不变）；
- 高于上限 → ``split``：把超长场景**切分**成多镜（镜头变多变短，总时长不变）。

历史候选仍保留可切换（``record`` 只记 C2、``extend`` 拉长画面、``loop`` 写循环标记
但**渲染层尚未消费**）：

- ``off``：什么都不做（服务层默认，零行为变化）；
- ``record``：片段照旧，但**逐条记 C2 冲突**——"允许低于下限，但必须留痕"；
- ``extend``：把场景时长抬到下限（画面被拉长，成片总时长随之增长）；
- ``loop``：同样抬到下限，但标记 ``fill=loop``（渲染侧待支持）；
- ``merge``：合并相邻场景到下限以上，**总时长不变**（管线默认）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable

POLICIES: tuple[str, ...] = ("off", "record", "extend", "loop", "merge")

#: 上限策略：off=不管；record=只记 C3；split=切分超长场景（总时长不变，管线默认）。
CAP_POLICIES: tuple[str, ...] = ("off", "record", "split")


@dataclass
class ShotFloorOutcome:
    durations: list[float]
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    changed: bool = False
    #: merge/split 时每个新段对应的原始下标（merge）或切分份数（split）。
    groups: list[Any] = field(default_factory=list)

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


def normalize_cap_policy(policy: str | None) -> str:
    value = str(policy or "off").strip().lower()
    return value if value in CAP_POLICIES else "off"


def merge_to_floor(
    durations: Iterable[float],
    min_shot_sec: float,
) -> ShotFloorOutcome:
    """把低于下限的相邻段**合并**到下限以上，总时长不变。

    返回的 ``durations`` 长度可能小于输入；``groups[i]`` 是第 i 段由哪些原始
    下标合并而成（供调用方合并场景对象）。若整片总时长都不足一个下限，则只剩
    一段（= 全片合并）并记 C2 冲突，因为此时无解，只能诚实留痕。
    """
    values = [max(0.0, float(value)) for value in durations]
    floor = max(0.0, float(min_shot_sec or 0.0))
    if not values or floor <= 0:
        return ShotFloorOutcome(durations=values, changed=False)

    conflicts: list[dict[str, Any]] = []
    merged: list[float] = []
    groups: list[list[int]] = []
    current = 0.0
    current_group: list[int] = []
    for index, value in enumerate(values):
        if not current_group:
            current = value
            current_group = [index]
        else:
            current += value
            current_group.append(index)
        if current + 1e-9 >= floor:
            merged.append(round(current, 6))
            groups.append(current_group)
            conflicts.append(
                {
                    "code": "C2",
                    "key": "min_shot_ms",
                    "index": len(merged) - 1,
                    "merged_from": list(current_group),
                    "value_sec": round(current, 3),
                    "floor_sec": round(floor, 3),
                    "policy": "merge",
                    "message": (
                        f"第 {current_group} 段合并为 {current:.2f}s"
                        + ("" if len(current_group) > 1 else "（单段已达标）")
                        + f"，下限 {floor:.2f}s"
                    ),
                }
            )
            current = 0.0
            current_group = []
    if current_group:  # 尾部落单：并进上一段（无上一段则自成一段）
        if merged:
            merged[-1] = round(merged[-1] + current, 6)
            groups[-1] = groups[-1] + current_group
            conflicts[-1]["value_sec"] = round(merged[-1], 3)
            conflicts[-1]["merged_from"] = list(groups[-1])
            conflicts[-1]["message"] = (
                f"第 {groups[-1]} 段合并为 {merged[-1]:.2f}s，下限 {floor:.2f}s"
            )
        else:
            merged.append(round(current, 6))
            groups.append(current_group)
            conflicts.append(
                {
                    "code": "C2",
                    "key": "min_shot_ms",
                    "index": 0,
                    "merged_from": list(current_group),
                    "value_sec": round(current, 3),
                    "floor_sec": round(floor, 3),
                    "policy": "merge",
                    "message": f"全片仅 {current:.2f}s，不足一个下限 {floor:.2f}s（无解，已整体保留）",
                }
            )

    only_single = len(groups) == len(values) and all(len(g) == 1 for g in groups)
    if only_single and all(v + 1e-9 >= floor for v in values):
        # 全部本来达标 → 不算改动，也不留冲突噪音
        return ShotFloorOutcome(durations=values, changed=False)
    real_conflicts = [
        c for c in conflicts
        if len(c.get("merged_from") or []) > 1 or float(c.get("value_sec", 0)) + 1e-9 < floor
    ]
    return ShotFloorOutcome(
        durations=merged,
        conflicts=real_conflicts,
        changed=len(merged) != len(values),
        groups=groups,
    )


def split_to_ceiling(
    durations: Iterable[float],
    max_shot_sec: float,
    *,
    max_parts: int = 8,
) -> ShotFloorOutcome:
    """把超长场景**切分**成多镜（每镜 ≤ 上限），总时长不变。

    ``groups[i]`` 为第 i 个原始段被切成几镜（供调用方复制场景对象）。
    上限 ≤0 或本来就达标 → 原样返回（``changed=False``），不产生噪音。

    ``max_parts`` 是**安全阀**：一段被要求切成几十镜（例如 21s 的设计场景被配音
    拉到 250s）时，切碎会产出上百个同素材片段，既没有剪辑意义也拖垮渲染。此时
    只切到 ``max_parts`` 镜、每镜仍超上限，并记一条 C3 说明"截断"，不假装合规。
    """
    values = [max(0.0, float(value)) for value in durations]
    ceiling = max(0.0, float(max_shot_sec or 0.0))
    if not values or ceiling <= 0:
        return ShotFloorOutcome(durations=values, changed=False)

    parts_cap = max(2, int(max_parts))
    out: list[float] = []
    groups: list[int] = []
    conflicts: list[dict[str, Any]] = []
    changed = False
    for index, value in enumerate(values):
        if value + 1e-9 <= ceiling:
            out.append(value)
            groups.append(1)
            continue
        wanted = max(2, int(math.ceil(value / ceiling - 1e-9)))
        count = min(wanted, parts_cap)
        piece = round(value / count, 6)
        out.extend([piece] * count)
        groups.append(count)
        changed = True
        truncated = wanted > count
        conflicts.append(
            {
                "code": "C3",
                "key": "max_shot_ms",
                "index": index,
                "value_sec": round(value, 3),
                "ceiling_sec": round(ceiling, 3),
                "split_into": count,
                "split_wanted": wanted,
                "truncated": truncated,
                "policy": "split",
                "message": (
                    f"第 {index} 段 {value:.2f}s 超过上限 {ceiling:.2f}s，"
                    f"切分为 {count} 镜（每镜 {piece:.2f}s）"
                    + (
                        f"；应切 {wanted} 镜但受 max_parts={parts_cap} 截断，"
                        "每镜仍超上限（已留痕，不假装合规）"
                        if truncated
                        else ""
                    )
                ),
            }
        )
    return ShotFloorOutcome(
        durations=out, conflicts=conflicts, changed=changed, groups=groups
    )


__all__ = [
    "CAP_POLICIES",
    "POLICIES",
    "ShotFloorOutcome",
    "apply_scene_floor",
    "fill_mode",
    "merge_to_floor",
    "normalize_cap_policy",
    "normalize_policy",
    "split_to_ceiling",
]
