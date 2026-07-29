"""需求会话持久化模型。"""

from __future__ import annotations

from clipwright.services.mongodb_service import MongoDbModel


class RequirementsSessionModel(MongoDbModel):
    """需求采集会话记录。"""

    collection_name = "requirements_sessions"
