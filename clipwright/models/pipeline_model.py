"""Pipeline 持久化模型。"""

from __future__ import annotations

from clipwright.services.mongodb_service import MongoDbModel


class PipelineModel(MongoDbModel):
    """管线执行记录。"""

    collection_name = "pipelines"


class LLMCallModel(MongoDbModel):
    """LLM 调用记录。"""

    collection_name = "llm_calls"
