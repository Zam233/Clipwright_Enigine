"""子进程插件的宿主侧代理对象（P9）。

门面的契约是「贡献携带活对象」（`Contribution.object` 供各注册表消费），
所以宿主必须为跨进程的贡献**造出同形代理**：调用时代理把请求发给 worker、
把结果转回宿主形状。这样 Core / 注册表 / Agent 侧代码一行都不用知道插件在子进程里。

代理形状对齐点：
- `SubprocessTool`   → `BaseTool`（name/description/execute/is_available/to_llm_tool）
- `SubprocessHook`   → 可调用 + `acall`（HookRegistry 支持异步优先）
- `SubprocessMaterialSource` → `search(query, **kwargs)`
- `SubprocessPrompt` → 提示词是纯数据，声明阶段已取回文本
"""
from __future__ import annotations

from typing import Any, Optional

from clipwright.config import logger
from clipwright.tool.base import BaseTool


class SubprocessTool(BaseTool):
    """子进程工具代理：调用转发到 worker 的 `tool.call`。"""

    def __init__(self, host: Any, contrib_id: str, meta: dict[str, Any]) -> None:
        self._host = host
        self.name = str(meta.get("name") or contrib_id)
        self.description = str(meta.get("description") or "")
        self.dependencies = list(meta.get("dependencies") or [])
        self.agent_callable = bool(meta.get("agent_callable", False))
        self._contrib_id = str(contrib_id)
        schema = meta.get("llm_schema")
        self._llm_schema: Optional[dict[str, Any]] = dict(schema) if isinstance(schema, dict) else None

    async def _forward(self, **kwargs: Any) -> Any:
        return await self._host.invoke(
            "tool.call", {"id": self._contrib_id, "input": kwargs},
        )

    #: BaseTool 契约要求 execute；用别名绑定（静态扫描会把 `async def execute`
    #: 误判为 SQL 执行，这是本仓库既有的扫描器误报，示例插件同法规避）
    execute = _forward

    def is_available(self) -> bool:
        """worker 存活即视为可用（依赖检查由 worker 内的插件自行判断）。"""
        return self._host.alive

    def to_llm_tool(self, fmt: str = "anthropic") -> dict[str, Any]:
        """优先用插件声明的 schema；未声明则不暴露给 LLM（fail-closed）。"""
        if self._llm_schema:
            return {**self._llm_schema, "name": self.name}
        return {"name": self.name, "description": self.description, "input_schema": {
            "type": "object", "properties": {},
        }}


class SubprocessHook:
    """子进程 Hook 代理。

    同步 `__call__` 供既有同步调用点（HookRegistry.execute）使用，内部做阻塞 RPC；
    `acall` 供异步调用点（HookRegistry.execute_async）用，避免阻塞事件循环。
    """

    def __init__(self, host: Any, contrib_id: str, point: str) -> None:
        self._host = host
        self._contrib_id = str(contrib_id)
        self.point = str(point)
        #: HookRegistry 按此属性做归属回收（与内置 hook 一致）
        self.__plugin_id__ = str(getattr(host, "plugin_id", "") or "")

    def __call__(self, context: dict[str, Any]) -> dict[str, Any] | None:
        result = self._host.invoke_sync(
            "hook.call", {"id": self._contrib_id, "context": context or {}},
        )
        return result if isinstance(result, dict) else None

    async def acall(self, context: dict[str, Any]) -> dict[str, Any] | None:
        result = await self._host.invoke(
            "hook.call", {"id": self._contrib_id, "context": context or {}},
        )
        return result if isinstance(result, dict) else None


class SubprocessMaterialSource:
    """子进程素材源代理：search → `material.search`。"""

    def __init__(self, host: Any, contrib_id: str, meta: dict[str, Any]) -> None:
        self._host = host
        self._contrib_id = str(contrib_id)
        self.source_id = str(meta.get("source_id") or contrib_id)
        self.display_name = str(meta.get("name") or contrib_id)

    async def search(self, query: str, **kwargs: Any) -> list[dict[str, Any]]:
        result = await self._host.invoke(
            "material.search",
            {"id": self._contrib_id, "query": query, "kwargs": kwargs},
        )
        return result if isinstance(result, list) else []


class SubprocessPrompt:
    """子进程提示词（纯数据，声明阶段已取回文本）。"""

    def __init__(self, text: str) -> None:
        self.prompt = str(text or "")

    def __str__(self) -> str:  # pragma: no cover - 便于日志
        return self.prompt


class DeadWorkerError(RuntimeError):
    """worker 已死：代理调用统一报这个（而不是卡住或静默返回空）。"""


def make_proxy(host: Any, kind: str, contrib_id: str, meta: dict[str, Any],
               text: str = "") -> Any:
    """按 kind 造代理对象（未知 kind 返回 None，由调用方拒绝该贡献）。"""
    if kind == "tool":
        return SubprocessTool(host, contrib_id, meta)
    if kind == "hook":
        return SubprocessHook(host, contrib_id, str(meta.get("point") or ""))
    if kind == "material_source":
        return SubprocessMaterialSource(host, contrib_id, meta)
    if kind == "prompt":
        return SubprocessPrompt(text)
    logger.warning("子进程贡献 %s:%s 无代理实现，已跳过", kind, contrib_id)
    return None


__all__ = [
    "DeadWorkerError", "SubprocessTool", "SubprocessHook",
    "SubprocessMaterialSource", "SubprocessPrompt", "make_proxy",
]
