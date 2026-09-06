"""AgentRegistry — 插件自定义 Agent 注册表（SA-2）。

插件在 initialize() 中注册自定义 Agent（BaseAgent 子类实例）；主管线
PipelineOrchestratorV2 运行时动态读取合并进执行 DAG；按 plugin_id 归属，
卸载/禁用时精确注销（对齐 HookRegistry 语义）。

约束：Agent 名匹配 ^[a-z][a-z0-9_]{0,31}$ 且不得与核心六 Agent 冲突；
deps 引用核心或已注册 Agent；manifest 须声明 orchestrate 权限（加载期校验）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from clipwright.config import logger

CORE_AGENT_NAMES = frozenset({
    "structure", "material", "edit", "animation", "audio", "quality",
})
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


@dataclass
class RegisteredAgent:
    """一个插件 Agent 的注册条目。"""

    agent: Any
    name: str
    plugin_id: str
    deps: list[str] = field(default_factory=list)
    description: str = ""


class AgentRegistry:
    """插件 Agent 注册表（classmethod 单例，模式对齐 ToolRegistry）。"""

    _agents: dict[str, RegisteredAgent] = {}

    @classmethod
    def register(cls, agent: Any, name: str = "", plugin_id: str = "",
                 deps: Optional[list[str]] = None, description: str = "") -> str:
        """注册插件 Agent，返回生效名。

        name 缺省取 agent.agent_name；deps 为上游 Agent 名列表。
        非法名 / 核心冲突 / 重复注册抛 ValueError。
        """
        agent_name = name or getattr(agent, "agent_name", "")
        if not _NAME_RE.fullmatch(agent_name or ""):
            raise ValueError("非法 Agent 名: " + str(agent_name))
        if agent_name in CORE_AGENT_NAMES:
            raise ValueError("Agent 名与核心冲突: " + str(agent_name))
        if agent_name in cls._agents:
            raise ValueError("Agent 重复注册: " + agent_name)
        clean_deps = [str(d) for d in (deps or []) if str(d)]
        cls._agents[agent_name] = RegisteredAgent(
            agent=agent, name=agent_name, plugin_id=plugin_id,
            deps=clean_deps, description=description,
        )
        logger.info("AgentRegistry 注册: %s 插件=%s 依赖=%s",
                    agent_name, plugin_id, clean_deps)
        return agent_name

    @classmethod
    def get(cls, name: str) -> Optional[Any]:
        """按名取插件 Agent 实例；未注册返回 None。"""
        entry = cls._agents.get(name)
        return entry.agent if entry else None

    @classmethod
    def get_entry(cls, name: str) -> Optional[RegisteredAgent]:
        """按名取完整注册条目（含 deps 与说明）。"""
        return cls._agents.get(name)

    @classmethod
    def list_all(cls) -> list[RegisteredAgent]:
        """列出全部插件 Agent（注册序）。"""
        return list(cls._agents.values())

    @classmethod
    def list_names(cls) -> list[str]:
        """列出全部插件 Agent 名。"""
        return list(cls._agents.keys())

    @classmethod
    def list_by_plugin(cls, plugin_id: str) -> list[str]:
        """列出指定插件注册的全部 Agent 名。"""
        return [n for n, e in cls._agents.items() if e.plugin_id == plugin_id]

    @classmethod
    def unregister_plugin(cls, plugin_id: str) -> int:
        """按插件 ID 注销其全部 Agent（卸载/禁用路径）。返回注销数。"""
        doomed = [n for n, e in cls._agents.items() if e.plugin_id == plugin_id]
        for n in doomed:
            del cls._agents[n]
        if doomed:
            logger.info("AgentRegistry 注销插件 %s 的 Agent: %s",
                        plugin_id, doomed)
        return len(doomed)

    @classmethod
    def unregister(cls, name: str) -> bool:
        """按名注销单个 Agent，返回是否实际删除。"""
        return cls._agents.pop(name, None) is not None

    @classmethod
    def clear(cls) -> None:
        """清空注册表（测试用）。"""
        cls._agents.clear()
