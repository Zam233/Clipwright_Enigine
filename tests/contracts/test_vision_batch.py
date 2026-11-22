"""read_image_batch：并发、缓存、逐项失败。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from clipwright.tool import vision_batch as mod


class _FakeScorer:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, *, asset, scene_context, frame_count):
        self.calls += 1
        if asset.get("fail"):
            raise RuntimeError("vision provider failed")
        return SimpleNamespace(
            status="success",
            output={"score": 0.8, "tags": ["a"], "description": "d",
                    "frames_analyzed": 3, "extraction_method": "local"},
        )


def test_vision_batch_cache_and_partial_failure(monkeypatch) -> None:
    fake = _FakeScorer()
    monkeypatch.setattr(mod, "resolve", lambda _cap: fake)
    mod.clear_vision_batch_cache()
    tool = mod.ReadImageBatchTool()
    assets = [{"asset_id": "a", "url": "u"}, {"asset_id": "b", "fail": True}]
    out = asyncio.run(tool.execute(
        assets=assets, scene_context={"title": "s", "keywords": ["k"]},
        frame_count=3, concurrency=2,
    ))
    assert str(out.status) == "success"
    assert out.output["summary"] == {"total": 2, "ok": 1, "failed": 1}
    assert out.output["items"][0]["score"] == 0.8
    assert out.output["items"][1]["status"] == "failed"
    assert fake.calls == 2

    # 同一 asset/scene 第二次命中缓存（失败项不缓存）
    out2 = asyncio.run(tool.execute(
        assets=[{"asset_id": "a", "url": "u"}],
        scene_context={"title": "s", "keywords": ["k"]}, frame_count=3,
    ))
    assert out2.output["items"][0]["cached"] is True
    assert fake.calls == 2


def test_vision_batch_requires_capability(monkeypatch) -> None:
    monkeypatch.setattr(mod, "resolve", lambda _cap: None)
    out = asyncio.run(mod.ReadImageBatchTool().execute(assets=[{"asset_id": "a"}]))
    assert str(out.status) == "not_found"
