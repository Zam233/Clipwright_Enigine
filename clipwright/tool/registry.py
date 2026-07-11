"""ToolRegistry — 集中式工具注册与分发。

职责：
1. 注册所有 BaseTool 子类
2. 提供按名称查询/调用的统一接口
3. 集成到 Pipeline Agent 中，供各 Agent 调用
"""

from __future__ import annotations

from typing import Any, Optional

from clipwright.schema.tool import ToolExecResult, ToolInfo, ToolStatus
from clipwright.tool.base import BaseTool


class ToolRegistry:
    """全局工具注册表。"""

    _instance: ToolRegistry | None = None
    _tools: dict[str, BaseTool] = {}

    def __new__(cls) -> ToolRegistry:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def register(cls, tool: BaseTool) -> None:
        """注册一个工具。"""
        if not tool.name:
            raise ValueError(f"Tool must have a non-empty name: {type(tool).__name__}")
        cls._tools[tool.name] = tool

    @classmethod
    def get(cls, name: str) -> Optional[BaseTool]:
        """按名称获取工具。"""
        return cls._tools.get(name)

    @classmethod
    def list(cls) -> list[ToolInfo]:
        """列出所有已注册的工具及其元信息。"""
        return [cls._get_tool_info(t) for t in cls._tools.values()]

    @classmethod
    def list_available(cls) -> list[ToolInfo]:
        """列出当前环境下可用的工具（依赖满足）。"""
        return [info for info in cls.list() if info.available]

    @classmethod
    def list_available_names(cls) -> list[str]:
        """列出当前环境下可用的工具名列表。"""
        return [t.name for t in cls._tools.values() if t.is_available()]

    @classmethod
    async def execute(
        cls, name: str, **kwargs: Any
    ) -> ToolExecResult:
        """按名称执行工具，返回标准化结果。"""
        tool = cls._tools.get(name)
        if tool is None:
            return ToolExecResult(
                status=ToolStatus.NOT_FOUND,
                tool_name=name,
                error=f"Tool '{name}' not registered",
            )
        try:
            raw = await tool.execute(**kwargs)
            # 如果工具本身已返回 ToolExecResult，直接复用
            if isinstance(raw, ToolExecResult):
                return raw
            # 否则包装为 ToolExecResult
            return ToolExecResult(
                status=ToolStatus.SUCCESS,
                tool_name=name,
                output=raw if isinstance(raw, dict) else {"result": raw},
            )
        except Exception as e:
            return ToolExecResult(
                status=ToolStatus.ERROR,
                tool_name=name,
                error=str(e),
            )

    @classmethod
    def clear(cls) -> None:
        cls._tools.clear()

    @classmethod
    def _get_tool_info(cls, tool: BaseTool) -> ToolInfo:
        return ToolInfo(
            name=tool.name,
            description=tool.description,
            available=tool.is_available(),
        )

    @classmethod
    async def execute_batch(
        cls, calls: list[dict[str, Any]]
    ) -> list[ToolExecResult]:
        """批量执行工具调用 —— 按顺序执行，一个失败不影响后续。"""
        results: list[ToolExecResult] = []
        for call in calls:
            name = call.pop("tool", "")
            result = await cls.execute(name, **call)
            results.append(result)
        return results
