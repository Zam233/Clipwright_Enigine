"""日志流 Handler — 将 INFO 级别以上日志推送到 SSE。"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import Any

from clipwright.config import logger

# 全局日志缓冲（最近 200 条）
_log_buffer: deque[dict[str, Any]] = deque(maxlen=200)
_subscribers: list[asyncio.Queue] = []


class SSELogHandler(logging.Handler):
    """将日志记录推送到 SSE 订阅者。"""

    def emit(self, record: logging.LogRecord) -> None:
        entry = {
            "level": record.levelname,
            "message": self.format(record),
            "name": record.name,
            "timestamp": record.created,
        }
        _log_buffer.append(entry)
        for q in list(_subscribers):
            try:
                q.put_nowait(entry)
            except asyncio.QueueFull:
                pass


def install_log_stream() -> None:
    """安装 SSE 日志 Handler 到全局 logger。"""
    handler = SSELogHandler(level=logging.INFO)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)


def subscribe() -> asyncio.Queue:
    """订阅日志流。"""
    q: asyncio.Queue = asyncio.Queue(maxsize=50)
    _subscribers.append(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    """取消订阅。"""
    if q in _subscribers:
        _subscribers.remove(q)


def get_recent_logs(n: int = 50) -> list[dict[str, Any]]:
    """获取最近 N 条日志。"""
    return list(_log_buffer)[-n:]
