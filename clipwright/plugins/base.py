"""第三方插件基类 — 支持注册 Tool 和 Skill。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from clipwright.config import logger as _clipwright_logger
from clipwright.schema.plugin import PluginManifest


#: 走新契约（门面注册）的插件 id —— shutdown 时由桥接层整体回收
_FACADE_MANAGED: set[str] = set()
#: 已记录过「旧契约」提示的插件（每个插件只记一次，避免刷屏）
_OLD_TRACK_LOGGED: set[str] = set()


def _record_old_track_hint(plugin_id: str) -> None:
    if not plugin_id or plugin_id in _OLD_TRACK_LOGGED:
        return
    _OLD_TRACK_LOGGED.add(plugin_id)
    try:
        from clipwright.plugins.error_bus import get_error_bus
        get_error_bus().record(
            plugin_id, "config",
            "旧契约 initialize()：建议迁移到 contributions()（M3 结束后旧轨停用）",
        )
    except Exception:
        pass


def run_contributions(plugin: Any) -> bool:
    """A-4 桥接核心：新契约注册（声明即授权 + 门面转调）。

    返回 True 表示该插件走新轨（已由门面注册，不应再执行 initialize 主体）；
    返回 False 表示无贡献声明（旧轨，调用方继续原流程）。

    B1 修复：`contributions()` 抛异常不能再当「无贡献」静默吞掉——那会让插件
    「加载成功但零贡献」（实测踩到：示例插件把 shortcut 误写成不存在的 kind）。
    异常写入插件错误通道后向上抛出，让加载器把本次加载判为失败。
    """
    plugin_id = str(getattr(getattr(plugin, "manifest", None), "id", "") or "")
    try:
        items = list(plugin.contributions() or [])
    except Exception as e:
        _clipwright_logger.error("插件 %s contributions() 异常: %s", plugin_id, e)
        try:
            from clipwright.plugins.error_bus import get_error_bus
            get_error_bus().record(plugin_id, "initialize", f"contributions() 异常: {e}")
        except Exception:  # noqa: BLE001 — 错误通道不可用也必须抛
            pass
        raise RuntimeError(f"插件 {plugin_id} 的 contributions() 执行失败: {e}") from e
    if not items:
        return False

    from clipwright.plugins.contribution import (
        ConflictPolicy,
        ContributionRegistry,
    )

    manifest = getattr(plugin, "manifest", None)
    declared = {
        f"{d.kind}:{d.id}"
        for d in list(getattr(manifest, "contributions", []) or [])
    }
    api_version = str(getattr(manifest, "api_version", "1") or "1")
    if api_version == "2" or declared:
        ContributionRegistry.declared_scope(plugin_id, declared)
    policy = (
        ConflictPolicy.OVERRIDE
        if getattr(manifest, "override_builtin", False)
        else ConflictPolicy.REJECT
    )
    report = ContributionRegistry.register(plugin_id, items, policy)
    if report.rejected:
        try:
            from clipwright.plugins.error_bus import get_error_bus
            for item, reason in report.rejected:
                get_error_bus().record(
                    plugin_id, "initialize", f"贡献被拒 {item.key}: {reason}",
                )
        except Exception:
            pass
    _FACADE_MANAGED.add(plugin_id)
    return True


def _install_dual_track_bridge(cls: type) -> None:
    """包装子类自有的 initialize/shutdown（幂等，不重复包装）。

    - initialize：contributions() 非空 → 门面注册并短路；空 → 原逻辑 + 迁移提示；
    - shutdown：原逻辑（资源释放）后追加门面回收（覆盖 teardown 清单遗漏的
      MaterialSource / StyleInterpreter —— §13.4 缺陷 1）。
    """
    orig_init = cls.__dict__.get("initialize")
    if orig_init is not None and not getattr(orig_init, "_cw_bridge", False):
        def _bridged_initialize(self, _orig=orig_init):
            if run_contributions(self):
                return None
            _record_old_track_hint(
                str(getattr(getattr(self, "manifest", None), "id", "") or ""),
            )
            return _orig(self)

        _bridged_initialize._cw_bridge = True  # type: ignore[attr-defined]
        cls.initialize = _bridged_initialize  # type: ignore[method-assign]

    orig_shutdown = cls.__dict__.get("shutdown")
    if orig_shutdown is None or not getattr(orig_shutdown, "_cw_bridge", False):
        def _bridged_shutdown(self, _orig=orig_shutdown):
            if _orig is not None:
                _orig(self)
            else:
                BasePlugin.shutdown(self)
            self._facade_shutdown()

        _bridged_shutdown._cw_bridge = True  # type: ignore[attr-defined]
        cls.shutdown = _bridged_shutdown  # type: ignore[method-assign]


class BasePlugin(ABC):
    """所有第三方插件的基类（A-2 双轨契约）。

    注册方式（二选一，加载器自动判别）：
    - **新契约（推荐）**：实现 `contributions()` 返回 Contribution 列表，
      注册由门面统一完成——不再 import 具体注册表；
    - **旧契约（兼容期保留）**：在 `initialize()` 内手写注册。加载器在
      `contributions()` 为空时继续调用它，并记一条 deprecation 提示，
      便于统计迁移进度（M3 结束时未迁移数应清零）。
    """

    manifest: PluginManifest
    config: dict[str, Any] = {}  # 从 config.yaml 加载，插件内通过 self.config 访问

    @property
    def logger(self):
        """插件可用的日志记录器，绑定插件 ID。"""
        import logging
        return logging.getLogger(f"clipwright.plugin.{self.manifest.id}")

    def contributions(self) -> list[Any]:
        """新契约：声明本插件贡献的全部扩展点（值对象，不含注册行为）。

        推荐用法：
            from clipwright.plugins.contribution import Contribution, ContributionKind

            def contributions(self):
                return [Contribution(ContributionKind.TOOL, "my_tool", MyTool())]

        加载器策略：非空 → 走门面注册，不再调用 initialize()。
        """
        return []

    def initialize(self) -> None:
        """桥接入口（A-4）：contributions() 非空时由门面注册；否则空实现。

        旧契约插件覆盖本方法——`__init_subclass__` 已包装：先尝试新轨注册，
        成功则**跳过**原 initialize 主体；无贡献声明时原样执行（含迁移提示）。
        """
        run_contributions(self)

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """A-4 双轨桥接：包装子类的 initialize/shutdown。"""
        super().__init_subclass__(**kwargs)
        _install_dual_track_bridge(cls)

    def shutdown(self) -> None:
        """插件卸载时调用。

        新契约下无需手动清理注册物（门面按 owner 回收）；本方法只用于
        释放插件自己的外部资源（连接池/子进程/临时目录）。
        旧契约插件仍需在此清理其手工注册物（兼容期）。
        """

    def _facade_shutdown(self) -> None:
        """A-4：门面回收（由 __init_subclass__ 包装进 shutdown 尾部）。"""
        plugin_id = str(getattr(getattr(self, "manifest", None), "id", "") or "")
        if plugin_id and plugin_id in _FACADE_MANAGED:
            _FACADE_MANAGED.discard(plugin_id)
            try:
                from clipwright.plugins.contribution import ContributionRegistry
                ContributionRegistry.unregister_plugin(plugin_id)
            except Exception as e:  # noqa: BLE001
                _clipwright_logger.warning("插件 %s 门面回收失败: %s", plugin_id, e)

    def plugin_info(self) -> dict[str, Any]:
        """返回插件的贡献概览（A-4：统一形态 —— 两轨都带 `contributions`）。

        D4 修复：旧轨此前返回 `{tools, skills, agents}`、新轨返回
        `{contributions}`，消费方得写两套解析。现在统一为：
        `{id, contributions: {kind: [id...]}, tools, skills, agents}`——
        `contributions` 恒存在（新轨来自门面，旧轨由注册表直查补齐），
        三个历史键保留供既有消费方继续使用。
        """
        from clipwright.agents.registry import AgentRegistry
        from clipwright.plugins.contribution import ContributionRegistry
        from clipwright.skill.registry import SkillRegistry
        from clipwright.tool.registry import ToolRegistry

        tools = ToolRegistry.list_by_plugin(self.manifest.id)
        skills = SkillRegistry.list_by_plugin(self.manifest.id)
        agents = AgentRegistry.list_by_plugin(self.manifest.id)

        by_kind: dict[str, list[str]] = {}
        for c in ContributionRegistry.list_by_owner(self.manifest.id):
            by_kind.setdefault(c.kind.value, []).append(c.id)
        if not by_kind:  # 旧轨插件未走门面 → 用注册表直查结果补齐同一形态
            by_kind = {
                k: sorted(v) for k, v in (
                    ("tool", tools), ("skill", skills), ("agent", agents),
                ) if v
            }
        return {
            "id": self.manifest.id,
            "contributions": by_kind,
            "tools": tools,
            "skills": skills,
            "agents": agents,
        }


class MaterialSourcePlugin(BasePlugin):
    """素材源插件 — 扩展素材库来源。"""

    @abstractmethod
    async def search(
        self, query: str, **kwargs: Any
    ) -> list[dict[str, Any]]:
        ...


class AgentStrategyPlugin(BasePlugin):
    """Agent 策略插件 — 替换或增强 Agent 的执行策略。"""

    @abstractmethod
    async def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        ...


class CapabilityPlugin(BasePlugin):
    """能力/工具插件 — 封装外部工具或服务的调用。"""
    pass


class StyleInterpreterPlugin(BasePlugin):
    """风格解释器插件 — 将 Persona 视觉参数和语境转为图解样式。

    创作者可通过实现此接口自定义风格逻辑，
    例如根据 Persona 的 tone/identity/rhythm 动态计算配色和字体。
    """

    @abstractmethod
    async def interpret(
        self,
        visual_config: dict,
        persona_context: dict,
    ) -> dict:
        """将 Persona 视觉/语境参数转为 DiagramStyle 兼容的参数字典。

        Args:
            visual_config: Persona 的 visual 层参数
                (含 palette, font, style_description, primary_color 等)
            persona_context: 完整 Persona 上下文
                (含 identity, language, rhythm 等)

        Returns:
            dict 包含 DiagramStyle 兼容的字段:
                primary_color, secondary_color, accent_color, text_color,
                font_size, title_font_size, stagger_delay, font, etc.
        """
        ...

