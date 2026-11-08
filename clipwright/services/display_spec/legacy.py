"""老数据键归一：2026-11-03~07 落库的会话带 `genui`，新形状是 `display_specs: {spec_id: spec}`。

**只读归一**：不改入参、不写回 Mongo。新形状优先；老键只有在 `display_specs` 缺失时才用。
真正的清洗仍由 `ensure_display_specs()` 按当前契约执行（老 spec 可能是按旧契约产的，
甚至可能带已被删除的组件——那正好被降级成占位文本）。
"""
from __future__ import annotations

from typing import Any

#: 老键名（只在这里出现一次；engine 其它地方不该再有 `genui` 字面量）。
LEGACY_SPEC_KEY = "genui"


def normalize_legacy(payload: Any, *, surface: str) -> Any:
    """返回带 `display_specs` 的**浅拷贝**；没有老键或已有新形状时原样返回入参。"""
    if not isinstance(payload, dict):
        return payload
    if payload.get("display_specs"):
        return payload  # 新形状优先，老键忽略
    legacy = payload.get(LEGACY_SPEC_KEY)
    if not isinstance(legacy, dict) or not legacy:
        return payload
    out = dict(payload)
    out["display_specs"] = {surface: legacy}
    return out
