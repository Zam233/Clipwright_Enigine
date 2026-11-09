"""worker 内部的宿主调用桥（协议 v2）。

插件代码（tool/hook/clip renderer）需要访问 workspace、artifact、job 时，
不直接碰管道，而是：

    from clipwright.plugins.runtime.bridge import call_host

    rows = await call_host("workspace.stat", {"path": "motion/project.json"},
                           scope={"task_id": task_id})

约束：
- 只有 worker 进程内可用；宿主进程里 `available()` 恒为 False。
- 未绑定传输层（v1 worker 或单测）时抛 `RuntimeError`，不静默返回空。
- 调用方必须把领域错误当错误处理；桥只负责传输与超时。
"""
from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any, Optional

from clipwright.plugins.runtime.protocol import RpcError

#: 当前 worker 的传输客户端（由 `worker._serve` 绑定）
_client: Optional[Any] = None
#: 当前请求的 scope（宿主随 tool.call/clip.render 下发），插件的 call_host 默认沿用它
_SCOPE: ContextVar[dict[str, Any]] = ContextVar("plugin_rpc_scope", default={})


def set_scope(scope: dict[str, Any]) -> Token:
    return _SCOPE.set(dict(scope or {}))


def reset_scope(token: Token) -> None:
    _SCOPE.reset(token)


def current_scope() -> dict[str, Any]:
    return dict(_SCOPE.get() or {})


def bind(client: Any) -> None:
    """绑定传输层（worker 启动时调用一次）。"""
    global _client
    _client = client


def unbind() -> None:
    """解绑（worker 退出时调用，避免悬挂引用）。"""
    global _client
    _client = None


def available() -> bool:
    """当前进程是否具备宿主调用通道。"""
    return _client is not None


async def call_host(
    method: str,
    params: dict[str, Any] | None = None,
    *,
    timeout: float = 60.0,
    scope: dict[str, Any] | None = None,
) -> Any:
    """向宿主发起一次请求，返回其结果。

    `scope` 由宿主在进入工具/render 时通过环境或参数下发，插件原样回传；
    宿主据此做 owner 校验（M2 起生效）。
    """
    if _client is None:
        raise RpcError(
            "UNSUPPORTED_METHOD",
            "宿主通道不可用：当前 worker 未启用双向 RPC（需要协议 v2 且宿主注册 handler）",
        )
    effective_scope = dict(scope) if scope is not None else current_scope()
    return await _client.call(method, params or {}, timeout=timeout, scope=effective_scope)


__all__ = [
    "available", "bind", "call_host", "current_scope", "reset_scope",
    "set_scope", "unbind",
]
