"""RequirementsSession MongoDB Model — 持久化需求对话会话。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from clipwright.config import TIME_ZONE, logger
from clipwright.services.mongodb_service import MongoDbModel


class RequirementsSessionModel(MongoDbModel):
    """需求对话会话的 MongoDB 持久化模型。

    Collection: requirements_sessions

    Fields:
        _id: str (UUID)
        status: str — SessionStatus 枚举值
        messages: list[dict] — 对话消息 [{role, content, timestamp, metadata}]
        creative_brief: dict | None — CreativeBrief 序列化
        production_plan: dict | None — ProductionPlan 序列化
        pipeline_id: str — 关联的管线 ID
        user_inputs: dict — 用户初始输入
        extra: dict — 扩展字段
    """

    collection_name = "requirements_sessions"

    def __init__(self, **kwargs: Any):
        now = datetime.now(tz=TIME_ZONE) if TIME_ZONE else datetime.now(timezone.utc)
        super().__init__(
            created_time=kwargs.pop("created_time", now),
            updated_time=kwargs.pop("updated_time", now),
            **kwargs,
        )

    @classmethod
    def from_session_dict(cls, session_id: str, data: dict) -> RequirementsSessionModel:
        """从 RequirementsSession 的 model_dump() 创建模型实例。"""
        now = datetime.now(tz=TIME_ZONE) if TIME_ZONE else datetime.now(timezone.utc)
        return cls(
            _id=session_id,
            status=data.get("status", "gathering"),
            messages=data.get("messages", []),
            creative_brief=data.get("creative_brief"),
            production_plan=data.get("production_plan"),
            pipeline_id=data.get("pipeline_id", ""),
            user_inputs=data.get("user_inputs", {}),
            extra=data.get("extra", {}),
            created_time=data.get("created_at", now),
            updated_time=data.get("updated_at", now),
        )

    def to_session_dict(self) -> dict:
        """序列化为 RequirementsSession 兼容的字典。"""
        return {
            "session_id": self.id,
            "status": getattr(self, "status", "gathering"),
            "messages": getattr(self, "messages", []),
            "creative_brief": getattr(self, "creative_brief", None),
            "production_plan": getattr(self, "production_plan", None),
            "pipeline_id": getattr(self, "pipeline_id", ""),
            "user_inputs": getattr(self, "user_inputs", {}),
            "extra": getattr(self, "extra", {}),
            "created_at": getattr(self, "created_time", None),
            "updated_at": getattr(self, "updated_time", None),
        }
