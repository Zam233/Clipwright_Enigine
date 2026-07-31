"""第三方插件系统 — 素材源、Agent 策略、能力工具的扩展入口。

使用方式：
    from clipwright.plugins import PluginLoader
    loader = PluginLoader(plugin_dir=Path("plugins"))
    loader.load_all()  # 发现并加载所有第三方插件
"""

from clipwright.plugins.base import (
    AgentStrategyPlugin,
    BasePlugin,
    CapabilityPlugin,
    MaterialSourcePlugin,
)
from clipwright.plugins.config_types import (
    TYPED_CONFIG_TYPES,
    typed_config_to_values,
    validate_typed_config,
)
from clipwright.plugins.hooks import HookPoint, HookRegistry
from clipwright.plugins.loader import PluginLoadError, PluginLoader
from clipwright.plugins.prompt_registry import PluginPrompt, PluginPromptRegistry

__all__ = [
    "BasePlugin",
    "MaterialSourcePlugin",
    "AgentStrategyPlugin",
    "CapabilityPlugin",
    "PluginLoader",
    "PluginLoadError",
    "HookRegistry",
    "HookPoint",
    "PluginPromptRegistry",
    "PluginPrompt",
]
