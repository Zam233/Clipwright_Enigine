"""P5-B5: 审计日志 — 关键操作事件写 Mongo audit 集合（未连接时仅日志）。"""

from __future__ import annotations

import time
from typing import Any, Optional

from clipwright.config import logger
from clipwright.context import mongo


def record(event: str, user_id: Optional[str] = None, detail: Optional[dict[str, Any]] = None) -> None:
    """写入审计事件（Mongo 可用时；失败仅告警不阻断业务）。"""
    entry = {
        "event": event,
        "user_id": user_id or "",
        "detail": detail or {},
        "created_at": time.time(),
    }
    try:
        if mongo.is_connected:
            mongo.db["audit"].insert_one(entry)
    except Exception as e:
        logger.warning("审计写入失败: %s", e)
    logger.info("audit: %s user=%s %s", event, user_id or "-", detail or "")
