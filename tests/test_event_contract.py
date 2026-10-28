"""PRD B5（2026-10-29 / 缺口 G10）：事件契约跨仓一致性。

为什么要有这条测试：前端把 `detail` 当无结构字典猜字段，后端改一个键名（`stage`→`phase`、
`trimmed` 从 bool 变 list）就会让 UI 静默变空白，而两边测试各自都是绿的。这里让
**后端 `EVENT_TYPES`** 与**前端 `EVENT_TYPE_KEYS`** 直接对账：集合必须相等、数量必须一致。

前端仓库不在本机时 `skip`（CI 单仓 clone 场景），不会把缺失当成通过。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from clipwright.schema.task import EVENT_TYPES

FRONTEND_SCHEMA = Path(r"J:\Clipweight-Client\src\features\task\eventSchema.ts")


def _frontend_keys() -> list[str]:
    text = FRONTEND_SCHEMA.read_text(encoding="utf-8")
    m = re.search(
        r"EVENT_TYPE_KEYS\s*=\s*\[(.*?)\]\s*as const", text, re.S,
    )
    assert m, "前端 eventSchema.ts 里找不到 EVENT_TYPE_KEYS 数组"
    return re.findall(r"'([a-z_]+)'", m.group(1))


def test_frontend_schema_exists() -> None:
    if not FRONTEND_SCHEMA.exists():
        pytest.skip("前端仓库不在本机（CI 单仓场景）——契约需在两仓同机时校验")
    assert _frontend_keys(), "EVENT_TYPE_KEYS 解析为空"


def test_event_types_match_frontend_parser() -> None:
    """后端 24 类事件必须与前端 parser 键集合完全相等（多一个少一个都算漂移）。"""
    if not FRONTEND_SCHEMA.exists():
        pytest.skip("前端仓库不在本机（CI 单仓场景）")
    backend = list(EVENT_TYPES)
    frontend = _frontend_keys()

    assert len(frontend) == len(set(frontend)), f"前端 EVENT_TYPE_KEYS 有重复：{frontend}"
    assert set(backend) == set(frontend), (
        "事件契约漂移：\n"
        f"  仅后端有：{sorted(set(backend) - set(frontend))}\n"
        f"  仅前端有：{sorted(set(frontend) - set(backend))}"
    )
    # 顺序对齐便于 diff（前端数组按后端顺序维护）
    assert backend == frontend, (
        "EVENT_TYPE_KEYS 顺序与后端不一致（便于 review 请保持同序）\n"
        f"  backend={backend}\n  frontend={frontend}"
    )


def test_event_types_are_unique_and_non_empty() -> None:
    assert len(EVENT_TYPES) == len(set(EVENT_TYPES))
    assert all(EVENT_TYPES)
    # 新增事件类型必须同步前端（本测试就是那个闸）
    assert len(EVENT_TYPES) == 24, (
        f"EVENT_TYPES 数量变化（{len(EVENT_TYPES)}）——请同步更新前端 "
        "src/features/task/eventSchema.ts 的 EVENT_TYPE_KEYS 与本断言"
    )
