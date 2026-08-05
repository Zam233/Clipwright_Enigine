"""日志流 — 将 Python logging 输出实时推送到 SSE trace。"""

from __future__ import annotations

import logging
import threading
from typing import Any


def _push_to_all_traces(event_type: str, summary: str, detail: Any = None) -> None:
    """推送到所有活跃 pipeline trace。"""
    from clipwright.services.trace import _traces
    import time as _time
    for pid, events in list(_traces.items()):
        if not pid.startswith("pl"):
            continue
        events.append({
            "time": _time.time(),
            "agent": "system",
            "type": event_type,
            "summary": summary,
            "detail": detail,
        })


class TraceLogHandler(logging.Handler):
    """将 logging 输出转发到 trace 事件流的 Handler。

    每个 INFO 级别以上的日志自动作为 SSE 事件推送到所有活跃的 pipeline trace。
    """

    def __init__(self, level: int = logging.INFO):
        super().__init__(level)
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record: logging.Record) -> None:
        try:
            msg = self.format(record)
            if not msg:
                return
            level = record.levelname.lower()
            event_type = {
                "error": "error", "warning": "warning",
                "info": "log", "debug": "debug",
            }.get(level, "log")
            summary = msg.split("\n")[0][:300]

            from clipwright.services.trace import _traces
            import time as _time
            pushed = 0
            for pid, events in list(_traces.items()):
                if not str(pid).startswith("pl"):
                    continue
                events.append({
                    "time": _time.time(),
                    "agent": "system",
                    "type": event_type,
                    "summary": summary,
                    "detail": {"full": msg[:800], "logger": record.name, "level": level},
                })
                pushed += 1
        except Exception:
            self.handleError(record)


# 全局 Handler 实例
_handler: TraceLogHandler | None = None
_handler_lock = threading.Lock()


def install_log_stream() -> None:
    """安装日志流 Handler — 将所有 clipwright 的 INFO 日志推送到 SSE。"""
    global _handler
    with _handler_lock:
        if _handler is not None:
            return
        _handler = TraceLogHandler(logging.INFO)
        # 只捕获 clipwright 命名空间的日志
        logger = logging.getLogger("clipwright")
        logger.addHandler(_handler)
        # 也捕获插件日志
        plugin_logger = logging.getLogger("clipwright.plugin")
        plugin_logger.addHandler(_handler)


def uninstall_log_stream() -> None:
    global _handler
    with _handler_lock:
        if _handler is None:
            return
        logging.getLogger("clipwright").removeHandler(_handler)
        logging.getLogger("clipwright.plugin").removeHandler(_handler)
        _handler = None
