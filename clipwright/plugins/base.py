"""第三方插件基类 — 支持注册 Tool 和 Skill。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from clipwright.schema.plugin import PluginManifest


class BasePlugin(ABC):
    """所有第三方插件的基类。

    插件可注册的内容（在 initialize() 中完成）：
    - Tool: 通过 ToolRegistry.register(MyTool()) 注册
    - Skill: 通过 SkillRegistry.register(MySkill()) 注册
    """

    manifest: PluginManifest

    @abstractmethod
    def initialize(self) -> None:
        """插件初始化时调用。

        在此方法中注册插件提供的 Tool 和 Skill：
            from clipwright.tool import ToolRegistry
            ToolRegistry.register(MyTool())

            from clipwright.skill import SkillRegistry
            SkillRegistry.register(MySkill())
        """
        ...

    @abstractmethod
    def shutdown(self) -> None:
        """插件卸载时调用。

        在此方法中清理已注册的 Tool 和 Skill。
        """
        ...

    def plugin_info(self) -> dict[str, Any]:
        """返回插件提供的 capabilities 概览。

        子类可覆盖此方法返回更详细的信息。
        """
        from clipwright.skill.registry import SkillRegistry
        from clipwright.tool.registry import ToolRegistry

        tools = [t.name for t in ToolRegistry._tools.values()
                 if getattr(t, "_plugin_id", None) == self.manifest.id]
        skills = [s.name for s in SkillRegistry._skills.values()
                  if getattr(s, "_plugin_id", None) == self.manifest.id]
        return {
            "id": self.manifest.id,
            "tools": tools,
            "skills": skills,
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
