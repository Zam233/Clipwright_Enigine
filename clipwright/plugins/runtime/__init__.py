"""插件子进程运行时（P9）。

宿主侧入口见 `host.PluginHost`，worker 入口见 `worker.main`，
线协议见 `protocol`，权限强制见 `permissions`，代理对象见 `proxies`。
"""
from __future__ import annotations

from clipwright.plugins.runtime.host import PluginHost, runtime_available
from clipwright.plugins.runtime.protocol import PROTOCOL_VERSION, RpcError

__all__ = ["PluginHost", "PROTOCOL_VERSION", "RpcError", "runtime_available"]
