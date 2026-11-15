"""共享网络资源（SSL context）——避免每次 new client 重解析证书链。

背景（2026-11-15 实测）：Windows 上 `ssl.create_default_context(cafile=certifi)`
在本机要 **1.2–1.4s**，而 `httpx.AsyncClient()` 默认每次自己建 context：
一次客户端构造实测 **5.1s**。材料阶段 `frame_extractor` 每个候选素材都新建
client，且是在事件循环线程里同步构造 → 整个后端 API 跟着卡住（py-spy 采样
主线程 499/500 命中 `create_default_context`）。

因此：**context 进程内只建一次**，所有 client 传 `verify=` 复用它
（复用后 client 构造 0.07s）。首次创建仍可能慢，调用方应在线程里建：
`await asyncio.to_thread(net.ssl_context)`。
"""

from __future__ import annotations

import ssl
import threading

_lock = threading.Lock()
_context: ssl.SSLContext | None = None


def ssl_context() -> ssl.SSLContext:
    """进程级共享 SSL context（首次创建可能耗时，建议放线程里）。"""
    global _context
    if _context is not None:
        return _context
    with _lock:
        if _context is None:
            try:
                import certifi

                _context = ssl.create_default_context(cafile=certifi.where())
            except Exception:  # noqa: BLE001 — 没有 certifi 时退回系统证书
                _context = ssl.create_default_context()
    return _context


async def async_client(**kwargs):
    """构造 httpx.AsyncClient 并复用共享 SSL context（调用方负责 aclose）。"""
    import httpx

    kwargs.setdefault("verify", ssl_context())
    return httpx.AsyncClient(**kwargs)


def prewarm() -> None:
    """启动期预热（可放线程池里调，避免首个请求付冷启动成本）。"""
    ssl_context()


def install_httpx_ssl_sharing() -> None:
    """让所有 httpx client（含 OpenAI SDK 内部 client）复用共享 context。

    httpx 默认每个 transport 自己 `create_ssl_context()`；在本机一次 1.2s+。
    这里替换 httpx 的内部工厂：`verify is True` 且没指定自定义证书时复用本模块
    的 context；显式 `verify=False`/自定义 cert 的行为不变。
    """
    try:
        import httpx._config as cfg
    except Exception:  # noqa: BLE001 — httpx 版本差异时不影响启动
        return
    if getattr(cfg, "_clipwright_ssl_sharing", False):
        return
    original = cfg.create_ssl_context

    def _create_ssl_context(verify=True, cert=None, trust_env=True):  # noqa: ANN001
        if verify is True and cert is None:
            return ssl_context()
        return original(verify=verify, cert=cert, trust_env=trust_env)

    cfg.create_ssl_context = _create_ssl_context  # type: ignore[assignment]
    # httpx 的 transport 在模块导入时就 `from .._config import create_ssl_context`
    # 绑定了旧引用，必须同步替换，否则 patch 只对之后的新导入生效（实测仍 5s）。
    try:
        import httpx._transports.default as _default_transport

        _default_transport.create_ssl_context = _create_ssl_context  # type: ignore[assignment]
    except Exception:  # noqa: BLE001
        pass
    cfg._clipwright_ssl_sharing = True  # type: ignore[attr-defined]
