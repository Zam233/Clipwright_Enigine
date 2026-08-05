"""MongoDB 连接管理 — 全局单例 mongo 对象。

其他模块通过 `from clipwright.context import mongo` 访问。
使用 Clipwright Settings 中的 Mongo URI / DB name 初始化。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from clipwright.config import settings, logger


@dataclass
class MongoHelper:
    """MongoDB 助手 — 延迟连接，包装 PyMongo 的 MongoClient 和 Database。"""
    cx: Optional["MongoClient"] = None  # type: ignore
    db: Optional["Database"] = None     # type: ignore

    _connected: bool = False

    def connect(self) -> bool:
        """初始化 MongoDB 连接（幂等）。返回是否连接成功。"""
        if self._connected:
            return True
        try:
            from pymongo import MongoClient
            self.cx = MongoClient(
                settings.mongo_uri,
                serverSelectionTimeoutMS=3000,
                connect=True,
            )
            self.cx.admin.command("ping")
            self.db = self.cx[settings.mongo_db_name]
            self._connected = True
            logger.info("MongoDB 已连接: %s / %s", settings.mongo_uri, settings.mongo_db_name)
            return True
        except Exception as e:
            logger.warning("MongoDB 连接失败 (非致命): %s", e)
            self.cx = None
            self.db = None
            self._connected = False
            return False

    @property
    def is_connected(self) -> bool:
        return self._connected and self.cx is not None


mongo = MongoHelper()
