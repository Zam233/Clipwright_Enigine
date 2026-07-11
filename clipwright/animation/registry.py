"""AnimationRegistry — 全局动画定义注册表。

管理所有注册的 AnimationDef（onscreen + transition）。
第三方插件通过 register() 添加新的动画定义。
"""

from __future__ import annotations

from typing import Optional

from clipwright.schema.animation import (
    AnimationDef,
    AnimationType,
)


class AnimationRegistry:
    """全局动画定义注册表。"""

    _instance: AnimationRegistry | None = None
    _animations: dict[str, AnimationDef] = {}
    _plugin_map: dict[str, str] = {}  # animation_id → plugin_id

    def __new__(cls) -> AnimationRegistry:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def register(cls, defn: AnimationDef, plugin_id: str = "") -> None:
        """注册一个动画定义。"""
        if not defn.animation_id:
            raise ValueError("AnimationDef must have a non-empty animation_id")
        cls._animations[defn.animation_id] = defn
        if plugin_id:
            cls._plugin_map[defn.animation_id] = plugin_id

    @classmethod
    def get(cls, animation_id: str) -> Optional[AnimationDef]:
        return cls._animations.get(animation_id)

    @classmethod
    def list(
        cls,
        anim_type: Optional[AnimationType] = None,
    ) -> list[AnimationDef]:
        """列出所有或指定类型的动画定义。"""
        if anim_type:
            return [
                a for a in cls._animations.values()
                if a.type == anim_type
            ]
        return list(cls._animations.values())

    @classmethod
    def list_ids(
        cls,
        anim_type: Optional[AnimationType] = None,
    ) -> list[str]:
        """列出所有或指定类型的动画 ID。"""
        if anim_type:
            return [
                aid for aid, a in cls._animations.items()
                if a.type == anim_type
            ]
        return list(cls._animations.keys())

    @classmethod
    def list_by_plugin(cls, plugin_id: str) -> list[AnimationDef]:
        """列出指定插件注册的所有动画。"""
        aids = [
            aid for aid, pid in cls._plugin_map.items()
            if pid == plugin_id
        ]
        return [cls._animations[aid] for aid in aids if aid in cls._animations]

    @classmethod
    def clear(cls) -> None:
        cls._animations.clear()
        cls._plugin_map.clear()
