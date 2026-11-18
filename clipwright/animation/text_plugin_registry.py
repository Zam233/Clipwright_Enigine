"""文字动画插件数据注册表（prompt / style / asset pack）。

Engine 只持有事实：这些数据由插件声明，engine 在生成/预览/质量时消费。
不允许插件把可执行对象塞进 prompt/style/asset 数据；运行期代码走 TEXT_MOTION。
"""

from __future__ import annotations

from typing import Any


class TextPromptPackRegistry:
    _items: dict[str, dict[str, Any]] = {}

    @classmethod
    def register(cls, item_id: str, meta: dict[str, Any], *, plugin_id: str = "") -> str | None:
        if not item_id:
            return "text_prompt_pack 缺少 id"
        if item_id in cls._items:
            owner = cls._items[item_id].get("plugin_id", "")
            if owner != plugin_id:
                return f"text_prompt_pack:{item_id} 已被 {owner} 注册"
        cls._items[item_id] = {**dict(meta), "id": item_id, "plugin_id": plugin_id}
        return None

    @classmethod
    def unregister_plugin(cls, plugin_id: str) -> list[str]:
        ids = [k for k, v in cls._items.items() if v.get("plugin_id") == plugin_id]
        for i in ids:
            cls._items.pop(i, None)
        return ids

    @classmethod
    def get(cls, item_id: str) -> dict[str, Any] | None:
        item = cls._items.get(str(item_id))
        return dict(item) if item else None

    @classmethod
    def list(cls) -> list[dict[str, Any]]:
        return list(cls._items.values())


class TextStylePackRegistry:
    _items: dict[str, dict[str, Any]] = {}

    @classmethod
    def register(cls, item_id: str, meta: dict[str, Any], *, plugin_id: str = "") -> str | None:
        style = meta.get("style")
        if not item_id or not isinstance(style, dict) or not style:
            return f"text_style_pack:{item_id} 缺少 style 对象"
        if item_id in cls._items:
            owner = cls._items[item_id].get("plugin_id", "")
            if owner != plugin_id:
                return f"text_style_pack:{item_id} 已被 {owner} 注册"
        cls._items[item_id] = {**dict(meta), "id": item_id, "plugin_id": plugin_id}
        return None

    @classmethod
    def unregister_plugin(cls, plugin_id: str) -> list[str]:
        ids = [k for k, v in cls._items.items() if v.get("plugin_id") == plugin_id]
        for i in ids:
            cls._items.pop(i, None)
        return ids

    @classmethod
    def get(cls, item_id: str) -> dict[str, Any] | None:
        item = cls._items.get(str(item_id))
        return dict(item) if item else None

    @classmethod
    def list(cls) -> list[dict[str, Any]]:
        return list(cls._items.values())


class TextAssetPackRegistry:
    _items: dict[str, dict[str, Any]] = {}

    @classmethod
    def register(cls, item_id: str, meta: dict[str, Any], *, plugin_id: str = "") -> str | None:
        assets = meta.get("assets")
        if not item_id or not isinstance(assets, list) or not assets:
            return f"text_asset_pack:{item_id} 缺少 assets"
        cls._items[item_id] = {**dict(meta), "id": item_id, "plugin_id": plugin_id}
        return None

    @classmethod
    def unregister_plugin(cls, plugin_id: str) -> list[str]:
        ids = [k for k, v in cls._items.items() if v.get("plugin_id") == plugin_id]
        for i in ids:
            cls._items.pop(i, None)
        return ids

    @classmethod
    def get(cls, item_id: str) -> dict[str, Any] | None:
        item = cls._items.get(str(item_id))
        return dict(item) if item else None

    @classmethod
    def list(cls) -> list[dict[str, Any]]:
        return list(cls._items.values())
