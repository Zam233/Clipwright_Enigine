"""插件提示词注入注册表 — 让插件可以为中心 Agent 提供提示词扩展。

所有 Agent 在构建 system prompt 时，通过此注册表获取已加载插件
为其准备的提示词片段，并注入到自身的提示词槽位中。

使用方式：
    在插件 initialize() 中调用:
        PluginPromptRegistry.register("my_plugin", "structure",
            "## 我的能力\\n使用方式：...", priority=10)

    Agent 在构建 prompt 时调用:
        prompts = PluginPromptRegistry.get_for_agent("structure")
        system_prompt += "\\n\\n".join(prompts)

设计原则：
    - 文字动画 和 MG 动画 是互不冲突的两种能力，服务于不同的视频需求
    - 插件提示词应明确说明使用场景和标记格式
    - Agent 在提示词槽位中聚合所有已注册插件的提示词
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar


@dataclass
class PluginPrompt:
    """一个插件提示词条目。"""

    plugin_id: str
    agent_name: str
    prompt: str
    priority: int = 0  # 数字越大越靠前
    description: str = ""


class PluginPromptRegistry:
    """中心化插件提示词注册表。

    插件在 initialize() 阶段注册提示词片段，
    Agent 在构建 system prompt 时获取并注入。
    """

    _prompts: ClassVar[dict[str, list[PluginPrompt]]] = {}

    # ── 注册 ──

    @classmethod
    def register(
        cls,
        plugin_id: str,
        agent_name: str,
        prompt: str,
        priority: int = 0,
        description: str = "",
    ) -> None:
        """注册一个插件提示词片段。

        Args:
            plugin_id: 插件唯一标识
            agent_name: 目标 Agent 名称 (structure/material/animation/requirements/quality)
            prompt: 提示词文本
            priority: 优先级，数字越大越靠前显示
            description: 简短说明
        """
        entry = PluginPrompt(
            plugin_id=plugin_id,
            agent_name=agent_name,
            prompt=prompt,
            priority=priority,
            description=description,
        )
        cls._prompts.setdefault(agent_name, []).append(entry)

    @classmethod
    def unregister(cls, plugin_id: str, agent_name: str = "") -> None:
        """移除某个插件注册的提示词。

        Args:
            plugin_id: 插件 ID
            agent_name: 如果为空则移除该插件所有 Agent 的提示词
        """
        if agent_name:
            cls._prompts[agent_name] = [
                p for p in cls._prompts.get(agent_name, [])
                if p.plugin_id != plugin_id
            ]
        else:
            for ag in list(cls._prompts.keys()):
                cls._prompts[ag] = [
                    p for p in cls._prompts[ag]
                    if p.plugin_id != plugin_id
                ]

    # ── 查询 ──

    @classmethod
    def get_for_agent(cls, agent_name: str) -> list[str]:
        """获取指定 Agent 的所有已注册插件提示词（按优先级排序）。

        Returns:
            按优先级降序排列的提示词文本列表
        """
        prompts = cls._prompts.get(agent_name, [])
        prompts.sort(key=lambda p: p.priority, reverse=True)
        return [p.prompt for p in prompts]

    @classmethod
    def get_entries_for_agent(cls, agent_name: str) -> list[PluginPrompt]:
        """获取指定 Agent 的原始条目（含元信息）。"""
        prompts = cls._prompts.get(agent_name, [])
        prompts.sort(key=lambda p: p.priority, reverse=True)
        return list(prompts)

    @classmethod
    def list_registered(cls) -> dict[str, list[dict]]:
        """列出所有已注册的提示词（用于调试）。"""
        result: dict[str, list[dict]] = {}
        for agent_name, prompts in cls._prompts.items():
            result[agent_name] = [
                {
                    "plugin_id": p.plugin_id,
                    "priority": p.priority,
                    "description": p.description,
                    "prompt_preview": p.prompt[:200],
                }
                for p in sorted(prompts, key=lambda x: x.priority, reverse=True)
            ]
        return result

    @classmethod
    def clear(cls) -> None:
        """清除所有注册（用于测试）。"""
        cls._prompts.clear()
