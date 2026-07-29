"""全局上下文 — MongoDB 连接管理。"""

from __future__ import annotations

from typing import Optional

from pymongo import MongoClient
from pymongo.database import Database

from clipwright.config import logger, settings


class MongoContext:
    """MongoDB 连接上下文 — 延迟连接，全局单例。"""

    def __init__(self) -> None:
        self._client: Optional[MongoClient] = None
        self._db: Optional[Database] = None

    @property
    def is_connected(self) -> bool:
        return self._client is not None

    @property
    def cx(self) -> MongoClient:
        """获取 MongoClient 实例（未连接时抛出异常）。"""
        if self._client is None:
            raise RuntimeError("MongoDB not connected. Call mongo.connect() first.")
        return self._client

    @property
    def db(self) -> Database:
        """获取 Database 实例。"""
        if self._db is None:
            raise RuntimeError("MongoDB not connected. Call mongo.connect() first.")
        return self._db

    def connect(self) -> None:
        """建立 MongoDB 连接。"""
        if self._client is not None:
            return
        try:
            self._client = MongoClient(
                settings.mongo_uri,
                serverSelectionTimeoutMS=3000,
                connectTimeoutMS=3000,
            )
            # 验证连接
            self._client.admin.command("ping")
            self._db = self._client[settings.mongo_db_name]
            logger.info("MongoDB connected: %s/%s", settings.mongo_uri, settings.mongo_db_name)
        except Exception as e:
            logger.warning("MongoDB connection failed: %s (running without persistence)", e)
            self._client = None
            self._db = None

    def disconnect(self) -> None:
        """关闭连接。"""
        if self._client:
            self._client.close()
            self._client = None
            self._db = None
            logger.info("MongoDB disconnected")


# 全局单例
mongo = MongoContext()
