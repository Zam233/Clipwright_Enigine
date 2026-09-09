"""M7: 插件错误通道 — 集中记录插件运行期错误（加载/初始化/Hook/动作），供诊断与前端展示。

设计：进程内环形缓冲（上限 PLUGIN_ERROR_CAP），每次记录同时写 audit；
前端可通过 /api/plugin/errors 拉取最近错误。
"""

from __future__ import annotations

import threading
import time
from typing import Any

from clipwright.config import logger

PLUGIN_ERROR_CAP = 200


class PluginErrorBus:
    """插件错误环形缓冲（线程安全）。"""

    def __init__(self, cap: int = PLUGIN_ERROR_CAP) -> None:
        self._cap = cap
        self._last_seen: dict[tuple[str, str, str], float] = {}  # P3-09a 去重
        self._errors: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def record(
        self,
        plugin_id: str,
        phase: str,
        message: str,
        details: str = "",
    ) -> None:
        """记录一次插件错误。phase ∈ {load, initialize, hook, action, shutdown, config}。"""
        entry = {
            "plugin_id": plugin_id,
            "phase": phase,
            "message": message[:500],
            "details": details[:2000],
            "ts": time.time(),
        }
        with self._lock:
            # P3-09a: 去重——同 (plugin_id, phase, message) 60s 窗口内只记一次
            # （热重载循环失败曾每秒刷屏把别的插件错误挤出容量上限）
            key = (plugin_id, phase, entry["message"])
            last = self._last_seen.get(key, 0.0)
            now = entry["ts"]
            if now - last < 60.0:
                return
            self._last_seen[key] = now
            self._errors.append(entry)
            if len(self._errors) > self._cap:
                self._errors = self._errors[-self._cap:]
        logger.warning("插件错误通道 [%s] %s: %s", plugin_id, phase, message)
        try:
            from clipwright import audit
            audit.record("plugin_error", "", {
                "plugin_id": plugin_id, "phase": phase, "message": message[:300],
            })
        except Exception:
            pass

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._errors[-limit:])

    def clear(self, plugin_id: str | None = None) -> int:
        with self._lock:
            if plugin_id is None:
                n = len(self._errors)
                self._errors = []
                return n
            before = len(self._errors)
            self._errors = [e for e in self._errors if e["plugin_id"] != plugin_id]
            return before - len(self._errors)


# 全局单例（loader 在 lifespan 注入）
_plugin_error_bus = PluginErrorBus()


def get_error_bus() -> PluginErrorBus:
    return _plugin_error_bus
