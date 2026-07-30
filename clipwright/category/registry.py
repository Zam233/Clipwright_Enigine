"""视频类型插件注册表。"""

from __future__ import annotations

from typing import Optional

from clipwright.category.base import BaseCategoryPlugin


class CategoryRegistry:
    """全局类型插件注册表。"""

    _plugins: dict[str, BaseCategoryPlugin] = {}

    @classmethod
    def register(cls, plugin: BaseCategoryPlugin, **kwargs) -> None:
        if plugin.plugin_id in cls._plugins:
            raise ValueError(f"Plugin already registered: {plugin.plugin_id}")
        cls._plugins[plugin.plugin_id] = plugin

    @classmethod
    def get(cls, plugin_id: str) -> Optional[BaseCategoryPlugin]:
        return cls._plugins.get(plugin_id)

    @classmethod
    def list(cls) -> list[dict[str, str]]:
        return [
            {
                "id": p.plugin_id,
                "name": p.display_name,
                "description": p.description,
            }
            for p in cls._plugins.values()
        ]

    @classmethod
    def unregister(cls, plugin_id: str) -> None:
        cls._plugins.pop(plugin_id, None)

    @classmethod
    def clear(cls) -> None:
        cls._plugins.clear()
