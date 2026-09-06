"""E2E 冒烟暴露的 Mongo 恢复路径修复——_load_result_from_mongo 协程 bug 回归。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from clipwright.api import pipeline as pipeline_api


class TestLoadResultFromMongo:
    @pytest.mark.asyncio
    async def test_recovers_result_dict(self, monkeypatch) -> None:
        """事件循环内调用时 Model 读取必须在线程中执行并返回结果字典。

        历史 bug：`_io` 在运行中的 loop 里返回未执行的协程，恢复路径永远
        失败于 'coroutine' object has no attribute 'items'。
        """
        from clipwright.models import pipeline_model as pm

        fake_model = SimpleNamespace(to_dict=lambda: {
            "status": "completed",
            "steps": [{"agent": "structure", "status": "ok"}],
            "request": {"topic": "t"},
            "shared_data": {"final_timeline": {"tracks": []}},
            "error": "",
            "output_path": "",
        })
        calls: list[str] = []

        def fake_find_by_id(cls, pid):
            calls.append(pid)
            return fake_model

        monkeypatch.setattr(pm.PipelineModel, "find_by_id", classmethod(fake_find_by_id))
        result = await pipeline_api._load_result_from_mongo("pl_test01")
        assert result is not None
        assert result["status"] == "completed"
        assert result["recovered_from_mongo"] is True
        assert result["shared_data"]["final_timeline"] == {"tracks": []}
        assert calls == ["pl_test01"]

    @pytest.mark.asyncio
    async def test_missing_pipeline_returns_none(self, monkeypatch) -> None:
        from clipwright.models import pipeline_model as pm

        monkeypatch.setattr(
            pm.PipelineModel, "find_by_id",
            classmethod(lambda cls, pid: None),
        )
        assert await pipeline_api._load_result_from_mongo("pl_missing") is None

    @pytest.mark.asyncio
    async def test_mongo_failure_returns_none_not_raise(self, monkeypatch) -> None:
        from clipwright.models import pipeline_model as pm

        def boom(cls, pid):
            raise RuntimeError("MongoDB not connected")

        monkeypatch.setattr(pm.PipelineModel, "find_by_id", classmethod(boom))
        assert await pipeline_api._load_result_from_mongo("pl_x") is None
