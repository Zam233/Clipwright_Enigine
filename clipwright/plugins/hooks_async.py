"""钩子的异步调度（P9）。

为什么单独一个模块：`plugins/hooks.py` 的 `execute()` 是**同步**契约（动画/渲染等
同步调用点在用）。子进程插件的 hook 代理只有异步路径（`acall`）能做到「不阻塞
事件循环」的 RPC；把它们塞进同一处会让同步语义变复杂，因此异步调度独立成模块：

- 异步调用点（pipeline / render 等 async 端点）→ `await execute_async(point, ctx)`；
- 同步调用点（动画目录扩展、diagram 钩子）→ 继续 `HookRegistry.execute(...)`，
  子进程 hook 在这条路径上会走代理的阻塞 `__call__`（有限阻塞，见 host 超时）。

对同步 hook，`execute_async` 保持与 `execute` 完全一致的调用语义。
"""
from __future__ import annotations

from typing import Any

from clipwright.config import logger
from clipwright.plugins.hooks import HookPoint, HookRegistry


async def execute_async(point: HookPoint, context: dict[str, Any]) -> dict[str, Any]:
    """异步执行某注入点的全部钩子；返回合并后的上下文。

    单钩子异常不阻塞同点其余钩子（与同步版一致），异常写入日志。
    """
    hooks = HookRegistry.hook_list(point)
    logger.debug("Executing %d hooks for point %s (async)", len(hooks), point.value)
    for hook in hooks:
        try:
            async_runner = getattr(hook, "acall", None)
            patch: Any = None
            if callable(async_runner):
                patch = await async_runner(context)
            else:
                patch = hook(context)
            if patch:
                context.update(patch)
        except Exception as e:  # noqa: BLE001 — 单个钩子失败不影响其余
            logger.warning("Hook 异常 (%s): %s", point.value, e)
    return context


__all__ = ["execute_async"]
