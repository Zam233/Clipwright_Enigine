"""类型切换（PRD F13 / V19）。

`type_deformation` 时不允许"用豁免改类型"，出路只有两条：回落到类型合法域，或**显式切换类型**。
本模块负责第二条：

- ``plan_switch(from_type, to_type)``：校验两边都是已知类型，产出可留痕的切换记录；
- ``append_switch(path, record)``：追加 JSONL，便于事后回答"这片子换过类型吗、为什么"。

纯函数 + 一个小追加器，不调 LLM、不改注册表（切换是"换一套规范重新出片"，
不是把 A 类型的定义改成 B）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


class TypeSwitchError(ValueError):
    """切换请求非法（类型不存在 / 同类型 / 缺少理由）。"""


@dataclass
class TypeSwitch:
    from_type: str
    to_type: str
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "event": "type_switch",
            "from_type": self.from_type,
            "to_type": self.to_type,
            "reason": self.reason,
        }


def _known(type_id: str) -> bool:
    from clipwright.category.registry import CategoryRegistry

    return CategoryRegistry.get(type_id) is not None


def plan_switch(
    from_type: str,
    to_type: str,
    *,
    reason: str = "",
    require_known: bool = True,
) -> TypeSwitch:
    """构造切换记录；非法切换抛 ``TypeSwitchError``（带可读原因）。"""
    src = str(from_type or "").strip()
    dst = str(to_type or "").strip()
    if not src or not dst:
        raise TypeSwitchError("from_type / to_type 都不能为空")
    if src == dst:
        raise TypeSwitchError("from_type 与 to_type 相同，不构成切换")
    if require_known:
        missing = [t for t in (src, dst) if not _known(t)]
        if missing:
            raise TypeSwitchError(f"未知类型：{missing}")
    return TypeSwitch(from_type=src, to_type=dst, reason=str(reason or "").strip())


def append_switch(path: str | Path, record: TypeSwitch) -> Path:
    """把切换记录追加到 JSONL。"""
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record.as_dict(), ensure_ascii=False, sort_keys=True) + "\n")
    return file_path


def switch_note(record: TypeSwitch) -> str:
    """给前端/管线事件用的一句话说明。"""
    tail = f"（原因：{record.reason}）" if record.reason else ""
    return f"类型切换：{record.from_type} → {record.to_type}{tail}"


__all__ = [
    "TypeSwitch",
    "TypeSwitchError",
    "append_switch",
    "plan_switch",
    "switch_note",
]
