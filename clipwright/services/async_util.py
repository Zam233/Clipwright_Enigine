"""Async 边界辅助工具 — 解决 "async 路由里跑同步阻塞调用冻住事件循环" 的问题。

FastAPI 把 ``async def`` 路由跑在主事件循环线程上；该线程里任何同步阻塞调用
（subprocess、同步 pymongo、同步文件 I/O、同步网络、CPU 重活）都会让**整个服务**
的所有请求一起排队，表现为 "前端一连上后端就卡死"。本模块提供两个原语：

* :func:`run_blocking` —— 在 async 上下文里把同步阻塞函数 offload 到线程池
  （``asyncio.to_thread``）；在 sync 上下文里则直接调用，方便 sync/async 共用代码。
* :func:`cached_probe` —— 给 "探测外部工具是否可用" 这类昂贵检查用：结果按 TTL 缓存，
  缓存失效时在**后台线程**刷新，调用方 await 时**绝不阻塞事件循环**（冷启动尚未探测完
  会立即返回 ``default``）。/health 这类需要 "立刻返回" 的接口因此永远不会被 npx/ffmpeg
  的冷启动卡住。
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any, Awaitable, Callable, TypeVar

T = TypeVar("T")


async def run_blocking(func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """在 async 上下文里把同步阻塞 ``func`` 放到线程池执行，避免冻住事件循环。

    若当前没有运行中的事件循环（即被 sync 代码调用），则直接同步执行 ``func``。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return func(*args, **kwargs)
    return await asyncio.to_thread(func, *args, **kwargs)


class _CachedProbe:
    """带 TTL 缓存 + 后台线程刷新的探针，await 时永不阻塞事件循环。"""

    __slots__ = ("_fn", "_ttl", "_default", "_lock", "_value", "_expiry", "_refreshing")

    def __init__(self, fn: Callable[[], Any], ttl: float, default: Any) -> None:
        self._fn = fn
        self._ttl = ttl
        self._default = default
        self._lock = threading.Lock()
        self._value: Any = default
        self._expiry: float = 0.0
        self._refreshing = False

    def _spawn_refresh(self) -> None:
        # 在 daemon 线程里跑阻塞探测；线程不占用事件循环。
        t = threading.Thread(target=self._refresh, daemon=True)
        t.start()

    def _refresh(self) -> None:
        try:
            result = self._fn()
            with self._lock:
                self._value = result
                self._expiry = time.monotonic() + self._ttl
        except Exception:
            # 探测失败：保留旧值（若有），短暂 backoff 后允许重试，避免疯狂重试。
            with self._lock:
                self._expiry = time.monotonic() + min(30.0, self._ttl)
        finally:
            with self._lock:
                self._refreshing = False

    def get_sync(self) -> Any:
        """同步、非阻塞读取：命中返回缓存，失效则在后台线程刷新并返回旧值/default。

        用于 ``/health`` 等 sync 上下文或必须立即返回的场景；**绝不**在此线程跑探测。
        """
        now = time.monotonic()
        with self._lock:
            if not (now < self._expiry) and not self._refreshing:
                self._refreshing = True
                self._spawn_refresh()
            return self._value

    async def __call__(self) -> Any:
        now = time.monotonic()
        with self._lock:
            fresh = now < self._expiry
            if not fresh and not self._refreshing:
                self._refreshing = True
                self._spawn_refresh()
            return self._value  # 命中返回缓存；冷启动/刷新中返回 default/旧值，绝不阻塞


_PROBES: dict[str, _CachedProbe] = {}
_PROBES_LOCK = threading.Lock()


def cached_probe(
    key: str,
    fn: Callable[[], Any],
    *,
    ttl: float = 600.0,
    default: Any = None,
) -> Callable[[], Awaitable[Any]]:
    """注册/获取一个按 ``key`` 单例的缓存探针，返回一个 ``async`` 可调用对象。

    ``fn`` 是**同步**阻塞探测函数（如 ``subprocess.run([...])``）。其执行被放到后台
    线程，事件循环线程在 await 时立即拿到缓存值或 ``default``，不会被阻塞。
    """
    with _PROBES_LOCK:
        probe = _PROBES.get(key)
        if probe is None:
            probe = _CachedProbe(fn, ttl, default)
            _PROBES[key] = probe
    return probe
