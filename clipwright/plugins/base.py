"""第三方插件基类 — 支持注册 Tool 和 Skill。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from clipwright.config import logger as _clipwright_logger
from clipwright.schema.plugin import PluginManifest


class BasePlugin(ABC):
    """所有第三方插件的基类。

    插件可注册的内容（在 initialize() 中完成）：
    - Tool: 通过 ToolRegistry.register(MyTool()) 注册
    - Skill: 通过 SkillRegistry.register(MySkill()) 注册
    """

    manifest: PluginManifest
    config: dict[str, Any] = {}  # 从 config.yaml 加载，插件内通过 self.config 访问

    @property
    def logger(self):
        """插件可用的日志记录器，绑定插件 ID。"""
        import logging
        return logging.getLogger(f"clipwright.plugin.{self.manifest.id}")

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

    def shutdown(self) -> None:
        """插件卸载时调用。

        在此方法中清理已注册的 Tool 和 Skill。
        子类可覆盖，基类提供空实现。
        """

    def plugin_info(self) -> dict[str, Any]:
        """返回插件提供的 capabilities 概览。"""
        from clipwright.skill.registry import SkillRegistry
        from clipwright.tool.registry import ToolRegistry

        return {
            "id": self.manifest.id,
            "tools": ToolRegistry.list_by_plugin(self.manifest.id),
            "skills": SkillRegistry.list_by_plugin(self.manifest.id),
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
