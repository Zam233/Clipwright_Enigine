"""插件 Hook 系统 — 支持插件在管线关键点注入行为。"""

from __future__ import annotations

from enum import Enum
from typing import Any, Callable

from clipwright.config import logger


class HookPoint(str, Enum):
    """系统定义的 Hook 注入点。"""
    PRE_PIPELINE = "pre_pipeline"
    POST_PIPELINE = "post_pipeline"
    PRE_AGENT = "pre_agent"
    POST_AGENT = "post_agent"
    PRE_RENDER = "pre_render"
    POST_RENDER = "post_render"
    ON_ERROR = "on_error"
    ANIMATION_CATALOG_EXTEND = "animation_catalog_extend"
    DIAGRAM_STYLE_PRESET = "diagram_style_preset"
    DIAGRAM_RENDERER_EXTEND = "diagram_renderer_extend"


class HookRegistry:
    """Hook 注册表，管理所有注册的 Hook。"""

    _hooks: dict[HookPoint, list[Callable]] = {
        hp: [] for hp in HookPoint
    }

    @classmethod
    def register(cls, point: HookPoint, fn: Callable, plugin_id: str = "", **kwargs: Any) -> None:
        # P4: wrapper 闭包携带 plugin_id（绑定方法安全），unregister 时按 ID 精确清理
        if plugin_id:
            def _wrapped(ctx: Any, _fn: Callable = fn) -> Any:
                return _fn(ctx)
            _wrapped.__plugin_id__ = plugin_id
            fn = _wrapped
        cls._hooks.setdefault(point, []).append(fn)
        logger.debug("Hook registered: %s -> %s", point.value, getattr(fn, "__name__", str(fn)))

    @classmethod
    def unregister_plugin(cls, plugin_id: str) -> int:
        """P4: 按插件 ID 注销全部 Hook。"""
        removed = 0
        for point in HookPoint:
            hooks = cls._hooks.get(point, [])
            filtered = [
                fn for fn in hooks
                if getattr(fn, "__plugin_id__", None) != plugin_id
            ]
            removed += len(hooks) - len(filtered)
            cls._hooks[point] = filtered
        if removed:
            logger.info("HookRegistry: 注销插件 %s 的 %d 个 Hook", plugin_id, removed)
        return removed

    @classmethod
    def execute(cls, point: HookPoint, context: dict[str, Any]) -> dict[str, Any]:
        hooks = cls._hooks.get(point, [])
        logger.debug("Executing %d hooks for point %s", len(hooks), point.value)
        for hook in hooks:
            try:
                result = hook(context)
                if result:
                    context.update(result)
            except Exception as e:
                # P8: 单钩子异常不阻塞同点其余钩子
                logger.warning("Hook 异常 (%s): %s", point.value, e)
        return context

    @classmethod
    def clear(cls) -> None:
        for hp in HookPoint:
            cls._hooks[hp] = []
