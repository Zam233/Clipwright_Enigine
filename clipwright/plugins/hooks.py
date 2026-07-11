"""插件 Hook 系统 — 支持插件在管线关键点注入行为。"""

from __future__ import annotations

from enum import Enum
from typing import Any, Callable


class HookPoint(str, Enum):
    """系统定义的 Hook 注入点。"""
    PRE_PIPELINE = "pre_pipeline"
    POST_PIPELINE = "post_pipeline"
    PRE_AGENT = "pre_agent"
    POST_AGENT = "post_agent"
    PRE_RENDER = "pre_render"
    POST_RENDER = "post_render"
    ON_ERROR = "on_error"


class HookRegistry:
    """Hook 注册表，管理所有注册的 Hook。"""

    _hooks: dict[HookPoint, list[Callable]] = {
        hp: [] for hp in HookPoint
    }

    @classmethod
    def register(cls, point: HookPoint, fn: Callable) -> None:
        cls._hooks.setdefault(point, []).append(fn)

    @classmethod
    def execute(cls, point: HookPoint, context: dict[str, Any]) -> dict[str, Any]:
        for hook in cls._hooks.get(point, []):
            result = hook(context)
            if result:
                context.update(result)
        return context

    @classmethod
    def clear(cls) -> None:
        for hp in HookPoint:
            cls._hooks[hp] = []
