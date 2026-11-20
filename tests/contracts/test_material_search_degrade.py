"""B20：素材源搜集中断按降级处理（不把整个阶段打成失败）。"""

from __future__ import annotations

import pytest

from clipwright.agents import material_agent as ma
from clipwright.material import MaterialRegistry


class _Asset:
    def __init__(self, aid: str):
        self.id = aid
        self.title = "t"
        self.url = ""
        self.local_path = ""
        self.duration_sec = 3.0
        self.tags: list[str] = []
        self.type = "video"
        self.license = ""
        self.license_url = ""
        self.author = ""
        self.source_url = ""
        self.resolution = ""


class _Result:
    def __init__(self, aid: str):
        self.asset = _Asset(aid)
        self.source_name = "fake"
        self.score = 0.5


@pytest.fixture(autouse=True)
def _clear_cache():
    ma._search_cache.clear()
    yield
    ma._search_cache.clear()


@pytest.mark.asyncio
async def test_search_failure_degrades_not_raises(monkeypatch):
    events: list[tuple] = []

    async def boom(**kwargs):
        raise RuntimeError("rate limited")

    def record(kind, detail="", scope="", impact=""):
        events.append((kind, scope))

    monkeypatch.setattr(MaterialRegistry, "search", staticmethod(boom))
    import clipwright.services.degradation as rel

    monkeypatch.setattr(rel, "note_degradation", record)

    out = await ma._search_with_cache("cats", top_k=3, source_ids=None)
    assert out == []
    assert ("material_search_unavailable", "stage:material") in events


@pytest.mark.asyncio
async def test_search_results_are_cached(monkeypatch):
    calls = {"n": 0}

    async def fake(**kwargs):
        calls["n"] += 1
        return [_Result("a1")]

    monkeypatch.setattr(MaterialRegistry, "search", staticmethod(fake))
    first = await ma._search_with_cache("dogs", top_k=2, source_ids=None)
    second = await ma._search_with_cache("dogs", top_k=2, source_ids=None)
    assert len(first) == len(second) == 1
    assert calls["n"] == 1, "同查询在 TTL 内只打一次源"
