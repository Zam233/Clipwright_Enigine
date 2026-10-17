"""视频类型插件注册表。

轮 87（三系统治理）：
- 记录归属（`_owner_plugin`）：loader 注册的第三方类型可整体注销；
- 幂等语义：同一 loader 插件重复注册（禁用→再启用/热重载）允许替换，
  内置类型（无归属）被重复注册仍然拒绝——修复"类型插件禁用后再启用必 502"。
"""

from __future__ import annotations

from typing import Optional

from clipwright.category.base import BaseCategoryPlugin


class CategoryRegistry:
    """全局类型插件注册表。"""

    _plugins: dict[str, BaseCategoryPlugin] = {}
    # category_id → loader 插件 ID（内置类型无归属，不在此表）
    _owner_plugin: dict[str, str] = {}

    @classmethod
    def register(cls, plugin: BaseCategoryPlugin, **kwargs) -> None:
        cid = plugin.plugin_id
        existing = cls._plugins.get(cid)
        if existing is not None:
            owner = cls._owner_plugin.get(cid, "")
            if not owner:
                # 内置类型（或未走 loader 的注册）：重复注册拒绝，
                # 防止第三方插件覆盖内置行为
                raise ValueError(f"Plugin already registered: {cid}")
            # 归属同一 loader 插件 → 热重载/再启用，替换实例
        cls._plugins[cid] = plugin

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
    def set_owner(cls, category_id: str, plugin_id: str) -> None:
        """记录类型的 loader 归属（loader 在 initialize 后按快照差集调用）。"""
        cls._owner_plugin[category_id] = plugin_id

    @classmethod
    def unregister_plugin(cls, plugin_id: str) -> list[str]:
        """注销某 loader 插件注册的全部类型（禁用/卸载时）；返回被注销的 ID。"""
        removed = [cid for cid, pid in cls._owner_plugin.items() if pid == plugin_id]
        for cid in removed:
            cls._plugins.pop(cid, None)
            cls._owner_plugin.pop(cid, None)
        return removed

    @classmethod
    def clear(cls) -> None:
        cls._plugins.clear()
        cls._owner_plugin.clear()
