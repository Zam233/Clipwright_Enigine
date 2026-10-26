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

        M3/P1-2: 注册冲突检测 — 同名工具被覆盖时告警（记录来源），不静默吞掉。
        """
        if not tool.name:
            raise ValueError(f"Tool must have a non-empty name: {type(tool).__name__}")
        existing = cls._tools.get(tool.name)
        if existing is not None:
            old_src = getattr(existing, "_plugin_id", "") or "builtin"
            new_src = plugin_id or "builtin"
            if old_src == "builtin" and new_src != "builtin":
                # P1-13: 插件不得覆盖内置——旧实现静默顶掉，插件卸载后内置
                # 永久消失（直到进程重启）
                logger.warning(
                    "拒绝插件 %s 覆盖内置工具 %s（内置优先）", new_src, tool.name,
                )
                return
            if old_src == new_src and old_src != "builtin":
                # 同一 owner 的重复注册 = 重载/重复加载，不是「覆盖」：
                # 压影子表会让卸载后 P1-13 的恢复逻辑把**陈旧代理**放回来
                # （已实测：多 loader 实例加载同一插件后 unload，工具复活）。
                cls._shadowed.pop(tool.name, None)
                logger.debug("同源重复注册工具 %s（%s），直接替换", tool.name, new_src)
            else:
                logger.warning(
                    "工具注册冲突: %s 已被 %s 注册，现被 %s 覆盖",
                    tool.name, old_src, new_src,
                )
                # 被覆盖的旧实现保存到影子表，注销时恢复
                cls._shadowed[tool.name] = existing
        tool._plugin_id = plugin_id  # type: ignore[attr-defined]
        cls._tools[tool.name] = tool

    @classmethod
    def list_by_plugin(cls, plugin_id: str) -> list[str]:
        """列出指定插件注册的所有工具名。"""
        return [
            t.name for t in cls._tools.values()
            if getattr(t, "_plugin_id", "") == plugin_id
        ]

    #: P1-13: 被插件覆盖的旧工具（name → 旧实例），注销覆盖者时恢复
    _shadowed: dict[str, "BaseTool"] = {}

    @classmethod
    def unregister(cls, name: str) -> None:
        """批7：按名注销工具（插件 disable 时清理注册物）；P1-13: 恢复被覆盖的旧项。"""
        cls._tools.pop(name, None)
        restored = cls._shadowed.pop(name, None)
        if restored is not None:
            cls._tools[name] = restored

    @classmethod
    def unregister_by_plugin(cls, plugin_id: str) -> list[str]:
        """批7：注销插件注册的全部工具，返回被移除的工具名。

        影子表处理（P1-13 完整化）：
        - 被本插件覆盖的**他人**实现 → 卸载时恢复（此前只有 `unregister(name)`
          会恢复，按插件批量注销时被覆盖项永久消失）；
        - **同源**陈旧代理 → 直接丢弃（它是重载/重复加载的残留，恢复它等于
          让已卸载插件的工具复活）。
        """
        doomed = [
            t.name for t in cls._tools.values()
            if getattr(t, "_plugin_id", "") == plugin_id
        ]
        for n in doomed:
            cls._tools.pop(n, None)
            shadow = cls._shadowed.pop(n, None)
            if shadow is None:
                continue
            if getattr(shadow, "_plugin_id", "") == plugin_id:
                continue  # 同源残留：丢弃
            cls._tools[n] = shadow  # 恢复被本插件覆盖的他人实现
            logger.info("工具 %s 已恢复为被覆盖的实现（卸载 %s）", n, plugin_id)
        # 兜底：同源影子项即使不在 doomed 里也要清掉
        for name, shadow in list(cls._shadowed.items()):
            if getattr(shadow, "_plugin_id", "") == plugin_id:
                cls._shadowed.pop(name, None)
        return doomed

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

    @classmethod
    def list_agent_callable(cls) -> list[BaseTool]:
        """列出所有声明可被 Agent LLM tool-use 调用且当前可用的工具。

        条件：
        - `agent_callable = True`（内置/插件工具显式声明）
        - `is_available() = True`（外部依赖，如 ffmpeg）
        - **契约新增**：若工具声明了 `is_configured()` 且返回 False，则排除——
          这条服务对象是**阶段 Agent**（未配置联网时不该把 web 工具塞给
          StructureAgent 白烧 token）。主 Agent 的 Kernel 工具面走
          `tool/surface.py`，仍能看见这些工具，调用时如实得到「未配置」结果。
        """
        out: list[BaseTool] = []
        for tool in cls._tools.values():
            if not getattr(tool, "agent_callable", False):
                continue
            if not tool.is_available():
                continue
            checker = getattr(tool, "is_configured", None)
            if callable(checker):
                try:
                    if not checker():
                        continue
                except Exception:  # noqa: BLE001 — 配置检查失败按未配置处理
                    continue
            out.append(tool)
        return out

    @classmethod
    def list_by_capability(cls, capability: str, *, usable_only: bool = True) -> list[BaseTool]:
        """列出声明了某语义能力的工具（实时）。

        Agent 不再写死工具名，而是按能力取实现——插件声明
        ``capability = "frame_check"`` 即可接管该能力（见 ``tool.capabilities``）。
        """
        from clipwright.tool.capabilities import is_usable

        out: list[BaseTool] = []
        for tool in cls._tools.values():
            if str(getattr(tool, "capability", "") or "") != capability:
                continue
            if usable_only and not is_usable(tool):
                continue
            out.append(tool)
        out.sort(key=lambda t: -int(getattr(t, "capability_priority", 0) or 0))
        return out

    @classmethod
    def resolve_capability(cls, capability: str, *, prefer: list[str] | None = None) -> Optional[BaseTool]:
        """按能力名解析到当前可用工具（None = 该能力当前没有实现）。"""
        from clipwright.tool.capabilities import resolve

        return resolve(capability, prefer=prefer or ())

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
    def call_with_input(cls, name: str, tool_input: dict[str, Any]) -> Any:
        """轮 90：以 dict 形参发起工具调用（返回协程，供 Agent 回调 await）。"""
        input_dict = dict(tool_input or {})
        runner = cls.execute
        return runner(name, **input_dict)

    @classmethod
    async def execute(
        cls, name: str, **kwargs: Any
    ) -> ToolExecResult:
        """按名称执行工具，失败时自动尝试 fallback 链。

        T9: 对含路径/URL 参数的工具调用统一安全校验。
        """
        # 路径/URL 参数安全校验（agent/LLM 驱动的调用可能注入任意路径）
        from clipwright.security import (
            assert_allowed_path, assert_public_url, SecurityViolation,
        )
        for key in ("input_path", "video_path", "audio_path", "file_path", "path"):
            val = kwargs.get(key)
            if val and isinstance(val, str) and not val.startswith(("http://", "https://")):
                try:
                    assert_allowed_path(val)
                except SecurityViolation as e:
                    return ToolExecResult(
                        status=ToolStatus.ERROR, tool_name=name,
                        error=f"路径安全校验失败: {e}",
                    )
        for key in ("url", "video_url", "audio_url", "input_url"):
            val = kwargs.get(key)
            if val and isinstance(val, str) and val.startswith(("http://", "https://")):
                try:
                    assert_public_url(val)
                except SecurityViolation as e:
                    return ToolExecResult(
                        status=ToolStatus.ERROR, tool_name=name,
                        error=f"URL 安全校验失败: {e}",
                    )

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
            # 复制后再取参数，避免修改调用方传入的 dict；过滤非字符串键防止 **kwargs TypeError
            name = str(call.get("tool", ""))
            params = {k: v for k, v in call.items() if isinstance(k, str) and k != "tool"}
            result = await cls.execute(name, **params)
            results.append(result)
        return results
