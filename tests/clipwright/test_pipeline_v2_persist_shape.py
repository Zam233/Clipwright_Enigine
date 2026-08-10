"""B8: _persist_state 截断应保留 dict/list 结构，仅递归截断字符串字段。

修复前 _persist_state 把 dict/list 值 str() 成字符串存入 Mongo，
导致 shared_data 形状漂移（大 scenes dict 落库后不再是 dict）。
"""

from __future__ import annotations

import pytest

from clipwright.schema.pipeline import PipelineRequest, PipelineState
from clipwright.services.pipeline_v2 import (
    PipelineOrchestratorV2,
    truncate_shared_data,
)


class TestTruncateSharedData:
    """truncate_shared_data 纯函数：保持结构、递归截断内部字符串。"""

    def test_nested_dict_keeps_dict_shape(self) -> None:
        data = {"scenes": [{"text": "x" * 8000}]}
        out = truncate_shared_data(data)
        assert isinstance(out["scenes"], list)
        assert isinstance(out["scenes"][0], dict)
        inner = out["scenes"][0]["text"]
        assert len(inner) <= 5000 + 100  # 截断 + 后缀
        assert inner.endswith("...") or "截断" in inner

    def test_list_value_stays_list(self) -> None:
        data = {"items": [{"k": "y" * 6000}, {"k": "short"}]}
        out = truncate_shared_data(data)
        assert isinstance(out["items"], list)
        assert len(out["items"]) == 2
        assert isinstance(out["items"][0], dict)
        assert len(out["items"][0]["k"]) <= 5000 + 100
        assert out["items"][1]["k"] == "short"

    def test_top_level_scalar_string_truncated(self) -> None:
        data = {"big": "z" * 8000}
        out = truncate_shared_data(data)
        assert isinstance(out["big"], str)
        assert len(out["big"]) <= 5000 + 100

    def test_short_values_untouched(self) -> None:
        data = {"a": "short", "b": 123, "c": True, "d": None}
        out = truncate_shared_data(data)
        assert out == data

    def test_empty_and_nested_small(self) -> None:
        data = {"scenes": [{"title": "s1", "duration_sec": 10.0}]}
        out = truncate_shared_data(data)
        assert out == data


class TestPersistStateNoMongo:
    """_persist_state 在 Mongo 不可用时静默返回、不抛异常。"""

    def test_no_mongo_does_not_raise(self, monkeypatch) -> None:
        # is_connected 是只读 property；直接置单例 _client 为 None 模拟未连接
        from clipwright.context import mongo as mongo_ctx
        monkeypatch.setattr(mongo_ctx, "_client", None, raising=False)
        monkeypatch.setattr(mongo_ctx, "_db", None, raising=False)
        orch = PipelineOrchestratorV2.__new__(PipelineOrchestratorV2)
        req = PipelineRequest(
            persona_id="default",
            category_plugin_id="knowledge_longform",
            topic="测试",
        )
        state = PipelineState(pipeline_id="pl_persist", request=req)
        state.shared_data["scenes"] = [{"text": "x" * 8000}]
        orch._persist_state(state, "running")  # 不应抛异常
