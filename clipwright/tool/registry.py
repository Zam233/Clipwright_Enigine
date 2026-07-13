"""ToolRegistry — 集中式工具注册与分发。

职责：
1. 注册所有 BaseTool 子类
2. 提供按名称查询/调用的统一接口
3. 集成到 Pipeline Agent 中，供各 Agent 调用
"""

from __future__ import annotations

from typing import Any, Optional

from clipwright.config import logger
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
    def register(cls, tool: BaseTool, plugin_id: str = "") -> None:
        """注册一个工具。

        Args:
            tool: 工具实例
            plugin_id: 注册插件的 ID（用于反向查询），空字符串表示内置
        """
        if not tool.name:
            raise ValueError(f"Tool must have a non-empty name: {type(tool).__name__}")
        tool._plugin_id = plugin_id  # type: ignore[attr-defined]
        cls._tools[tool.name] = tool

    @classmethod
    def list_by_plugin(cls, plugin_id: str) -> list[str]:
        """列出指定插件注册的所有工具名。"""
        return [
            t.name for t in cls._tools.values()
            if getattr(t, "_plugin_id", "") == plugin_id
        ]

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
        return [
            cls._get_tool_info(t)
            for t in cls._tools.values()
            if t.is_available()
        ]

    @classmethod
    def list_available_names(cls) -> list[str]:
        """列出当前环境下可用的工具名列表。"""
        return [t.name for t in cls._tools.values() if t.is_available()]

    # 工具回退链：主工具失败时尝试的替代工具
    _FALLBACK_CHAINS: dict[str, list[str]] = {
        "video_trim": [],  # 签名不兼容 video_concat，不设 fallback
        "generate_text_video": [],
        "video_concat": [],
        "audio_extract": [],
        "bpm_detect": [],
        "audio_replace": [],
        "scene_detect": [],
        "semantic_match": ["material_filter"],
        "video_download": [],
    }

    @classmethod
    def set_fallback(cls, tool_name: str, fallbacks: list[str]) -> None:
        """设置工具的 fallback 链。"""
        cls._FALLBACK_CHAINS[tool_name] = fallbacks

    @classmethod
    async def execute(
        cls, name: str, **kwargs: Any
    ) -> ToolExecResult:
        """按名称执行工具，失败时自动尝试 fallback 链。"""
        tool = cls._tools.get(name)
        if tool is None:
            logger.error("ToolRegistry: 工具未注册: %s", name)
            return ToolExecResult(
                status=ToolStatus.NOT_FOUND,
                tool_name=name,
                error=f"Tool '{name}' not registered",
            )
        logger.info("ToolRegistry: 执行 %s(%s)", name,
                    ", ".join(f"{k}={v}" for k, v in list(kwargs.items())[:3]))
        # 推送工具调用事件到所有活跃的 SSE 流
        try:
            from clipwright.services.trace import add_tool_event
            add_tool_event(name, kwargs)
        except Exception:
            pass
        try:
            raw = await tool.execute(**kwargs)
            if isinstance(raw, ToolExecResult):
                if raw.status in ("success", "dependency_missing"):
                    return raw
                # 失败 → 尝试 fallback
                fallbacks = cls._FALLBACK_CHAINS.get(name, [])
                for fb_name in fallbacks:
                    fb_tool = cls._tools.get(fb_name)
                    if fb_tool is None:
                        continue
                    logger.info("ToolRegistry: %s 失败, 尝试 fallback %s", name, fb_name)
                    fb_raw = await fb_tool.execute(**kwargs)
                    if isinstance(fb_raw, ToolExecResult) and fb_raw.status == "success":
                        fb_raw.tool_name = name  # 保持原工具名
                        return fb_raw
                return raw
            return ToolExecResult(
                status=ToolStatus.SUCCESS,
                tool_name=name,
                output=raw if isinstance(raw, dict) else {"result": raw},
            )
        except Exception as e:
            logger.error("ToolRegistry: %s 执行异常: %s", name, str(e)[:200])
            # 异常时也尝试 fallback
            fallbacks = cls._FALLBACK_CHAINS.get(name, [])
            for fb_name in fallbacks:
                fb_tool = cls._tools.get(fb_name)
                if fb_tool is None:
                    continue
                try:
                    logger.info("ToolRegistry: %s 异常，尝试 fallback %s", name, fb_name)
                    fb_raw = await fb_tool.execute(**kwargs)
                    if isinstance(fb_raw, ToolExecResult) and fb_raw.status == "success":
                        fb_raw.tool_name = name
                        return fb_raw
                except Exception:
                    continue
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
