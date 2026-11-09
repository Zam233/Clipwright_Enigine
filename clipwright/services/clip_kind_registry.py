"""ClipKind 注册表（M4）—— 插件可声明新的轨道/片段类型。

边界：
- 内置 kind 仍来自 `schema.timeline.ClipKind`，行为不变；
- 插件 kind 是**字符串 + 声明式定义**（展示信息、metadata schema、渲染契约名），
  engine 不理解 schema 里的领域字段；
- 注册表只决定“这个字符串是否被承认、归属于谁、渲染/预览契约叫什么”，
  并不执行插件代码（渲染回调在 M5）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from clipwright.config import logger
from clipwright.schema.timeline import ClipKind, register_plugin_clip_kind, unregister_plugin_clip_kind

#: 允许的渲染/预览契约名（engine 只认这些通用契约）
RENDER_CONTRACTS = {"media_overlay.v1"}
PREVIEW_CONTRACTS = {"artifact.v1"}


@dataclass(frozen=True)
class ClipKindDefinition:
    kind_id: str
    display_name: str = ""
    track_label: str = ""
    icon: str = ""
    metadata_schema: dict[str, Any] = field(default_factory=dict)
    render_contract: str = ""
    preview_contract: str = ""
    #: 插件内负责渲染该 kind 的工具 id（engine 通过 tool.call 调用，不直接执行代码）
    renderer_tool: str = ""
    capabilities: tuple[str, ...] = ()
    owner_plugin: str = ""
    builtin: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind_id": self.kind_id,
            "display_name": self.display_name or self.kind_id,
            "track_label": self.track_label or self.display_name or self.kind_id,
            "icon": self.icon,
            "metadata_schema": dict(self.metadata_schema),
            "render_contract": self.render_contract,
            "preview_contract": self.preview_contract,
            "renderer_tool": self.renderer_tool,
            "capabilities": list(self.capabilities),
            "owner_plugin": self.owner_plugin,
            "builtin": self.builtin,
        }


class ClipKindError(ValueError):
    pass


class ClipKindRegistry:
    """kind_id → ClipKindDefinition（内置 + 插件）。"""

    _defs: dict[str, ClipKindDefinition] = {}

    @classmethod
    def _ensure_builtins(cls) -> None:
        if cls._defs:
            return
        for member in ClipKind:
            cls._defs[member.value] = ClipKindDefinition(
                kind_id=member.value,
                display_name=member.value,
                track_label=member.value,
                builtin=True,
                capabilities=("builtin",),
            )

    @classmethod
    def register(cls, definition: ClipKindDefinition) -> ClipKindDefinition:
        cls._ensure_builtins()
        kind_id = str(definition.kind_id or "").strip()
        if not kind_id:
            raise ClipKindError("kind_id 不能为空")
        existing = cls._defs.get(kind_id)
        if existing is not None and existing.builtin:
            raise ClipKindError(f"不能覆盖内置 kind: {kind_id}")
        if existing is not None and existing.owner_plugin != definition.owner_plugin:
            raise ClipKindError(f"kind 已被 {existing.owner_plugin or '其他插件'} 注册: {kind_id}")
        if definition.render_contract and definition.render_contract not in RENDER_CONTRACTS:
            raise ClipKindError(f"未知 render_contract: {definition.render_contract}")
        if definition.preview_contract and definition.preview_contract not in PREVIEW_CONTRACTS:
            raise ClipKindError(f"未知 preview_contract: {definition.preview_contract}")
        cls._defs[kind_id] = definition
        register_plugin_clip_kind(kind_id)
        logger.info("ClipKind 注册: %s（owner=%s）", kind_id, definition.owner_plugin or "-")
        return definition

    @classmethod
    def unregister_plugin(cls, plugin_id: str) -> list[str]:
        cls._ensure_builtins()
        removed = [k for k, d in cls._defs.items() if d.owner_plugin == plugin_id and not d.builtin]
        for kind_id in removed:
            cls._defs.pop(kind_id, None)
            unregister_plugin_clip_kind(kind_id)
        return removed

    @classmethod
    def get(cls, kind_id: str) -> ClipKindDefinition | None:
        cls._ensure_builtins()
        return cls._defs.get(str(kind_id or ""))

    @classmethod
    def is_registered(cls, kind_id: str) -> bool:
        return cls.get(kind_id) is not None

    @classmethod
    def is_builtin(cls, kind_id: str) -> bool:
        definition = cls.get(kind_id)
        return bool(definition and definition.builtin)

    @classmethod
    def is_plugin_kind(cls, kind_id: str) -> bool:
        definition = cls.get(kind_id)
        return bool(definition and not definition.builtin)

    @classmethod
    def list(cls) -> list[dict[str, Any]]:
        cls._ensure_builtins()
        return [d.to_dict() for d in cls._defs.values()]

    @classmethod
    def clear(cls) -> None:
        # 同步收回 schema 的插件 kind 白名单，否则 clear 后旧值仍被接受
        for definition in list(cls._defs.values()):
            if not definition.builtin:
                unregister_plugin_clip_kind(definition.kind_id)
        cls._defs.clear()


__all__ = [
    "ClipKindDefinition",
    "ClipKindError",
    "ClipKindRegistry",
    "PREVIEW_CONTRACTS",
    "RENDER_CONTRACTS",
]
