"""统一超时语义（E2E 2026-10-28 收口）。

本项目历史上散落着一堆硬编码超时（120s / 300s / 900s），它们不是"保护"，
而是**把还在正常做的工作掐死**：LLM 长输出被 120s 截断成"空正文"、
阶段被 900s 截断成"超时失败"、整片渲染被 600s 截断。

统一约定（用户 2026-10-28 裁定）：
- `timeout <= 0` 或 `None` 表示**不设超时**（无限等待）；
- 需要保护的边（第三方网络请求）由调用方显式给正数；
- 所有 `asyncio.wait_for` 都走这里，避免"0 被当成立即超时"这种坑。
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, TypeVar

T = TypeVar("T")

#: 语义常量：不设超时
UNLIMITED = 0.0


def resolve(timeout: Any) -> float | None:
    """把配置值解析成 `wait_for` 的超时参数：`<=0` / `None` → None（无限）。"""
    if timeout is None:
        return None
    try:
        t = float(timeout)
    except (TypeError, ValueError):
        return None
    return t if t > 0 else None


async def wait_or_run(coro: Awaitable[T], timeout: Any) -> T:
    """有超时就用超时，没有就直等。"""
    t = resolve(timeout)
    if t is None:
        return await coro
    return await asyncio.wait_for(coro, timeout=t)


def limited(timeout: Any) -> bool:
    """是否真的限时（给日志/降级判断用）。"""
    return resolve(timeout) is not None
