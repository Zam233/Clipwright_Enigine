"""Pipeline 请求模型与类型注册表测试（V1 编排器已删除）。"""

from __future__ import annotations

import pytest

from clipwright.category import CategoryRegistry, KnowledgeLongformPlugin
from clipwright.schema.pipeline import PipelineRequest


def test_category_registry() -> None:
    """验证类型插件注册表。"""
    CategoryRegistry.clear()
    CategoryRegistry.register(KnowledgeLongformPlugin())
    plugin = CategoryRegistry.get("knowledge_longform")
    assert plugin is not None
    assert plugin.display_name == "知识区长片"
    CategoryRegistry.clear()


def test_pipeline_request_model() -> None:
    """验证 PipelineRequest 模型。"""
    req = PipelineRequest(
        persona_id="test_persona",
        category_plugin_id="knowledge_longform",
        topic="测试话题",
    )
    assert req.persona_id == "test_persona"
    assert req.dry_run is False
