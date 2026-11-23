"""AnimationAgent mg_dynamic 批量预生成。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from clipwright.agents.animation_agent import AnimationAgent


def test_prefetch_mg_dynamic_caches_result(monkeypatch) -> None:
    agent = AnimationAgent.__new__(AnimationAgent)
    agent._mg_batch_cache = {}
    agent._fact_clip_ids = set()
    agent._tl_width, agent._tl_height = 1920, 1080
    calls: list[dict] = []

    async def fake_call(name, **kwargs):
        calls.append({"name": name, **kwargs})
        return SimpleNamespace(
            status="success",
            output={"items": [{"index": 0, "status": "ok", "method": "batch",
                               "mg_def": {"elements": []}}]},
        )

    agent.call_tool = fake_call  # type: ignore[method-assign]
    clip = SimpleNamespace(
        id="c1", text="", metadata={"title": "s", "description": "d"},
    )
    job = (None, clip, "mg_dynamic", "动态", {"payload": {"description": "画一个图",
                                                           "text": "A"}}, {})
    asyncio.run(agent._prefetch_mg_dynamic([job], SimpleNamespace(pipeline_id="p")))
    assert calls and calls[0]["name"] == "generate_mg_batch"
    assert agent._mg_batch_cache["c1"]["mg_def"] == {"elements": []}


def test_prefetch_typed_mg_caches_result() -> None:
    from clipwright.animation.type_specs import all_specs

    agent = AnimationAgent.__new__(AnimationAgent)
    agent._mg_typed_cache = {}
    agent._tl_width, agent._tl_height = 1920, 1080
    agent._typed_generation_enabled = lambda: True  # type: ignore[method-assign]
    type_id = all_specs()[0].id
    calls: list[dict] = []

    async def fake_call(name, **kwargs):
        calls.append({"name": name, **kwargs})
        return SimpleNamespace(status="success", output={"items": [{
            "index": 0, "status": "ok", "mg_def": {"elements": []},
            "html": "<div/>", "issues": [], "motion": {}, "duration_s": 3,
            "method": "typed-batch",
        }]})

    agent.call_tool = fake_call  # type: ignore[method-assign]
    clip = SimpleNamespace(id="c2", text="A", duration_sec=3.0, metadata={"title": "s"})
    job = (None, clip, type_id, "类型", {"text": "A"}, {})
    asyncio.run(agent._prefetch_typed_mg([job], SimpleNamespace(pipeline_id="p")))
    assert calls and calls[0]["items"][0]["type_id"] == type_id
    assert agent._mg_typed_cache["c2"]["mg_def"] == {"elements": []}


def test_prefetch_mg_dynamic_chunks_all() -> None:
    agent = AnimationAgent.__new__(AnimationAgent)
    agent._mg_batch_cache = {}
    agent._fact_clip_ids = set()
    agent._tl_width, agent._tl_height = 1920, 1080
    calls: list[int] = []

    async def fake_call(_name, **kwargs):
        batch = kwargs.get("items") or []
        calls.append(len(batch))
        return SimpleNamespace(status="success", output={"items": [
            {"index": i, "status": "ok", "mg_def": {"elements": []}, "method": "batch"}
            for i in range(len(batch))
        ]})

    agent.call_tool = fake_call  # type: ignore[method-assign]
    jobs = []
    for i in range(13):
        clip = SimpleNamespace(id=f"c{i}", text="", metadata={"title": f"s{i}"})
        jobs.append((None, clip, "mg_dynamic", "动态",
                     {"payload": {"description": f"d{i}", "text": "A"}}, {}))
    asyncio.run(agent._prefetch_mg_dynamic(jobs, SimpleNamespace(pipeline_id="p")))
    assert calls == [12, 1]
    assert len(agent._mg_batch_cache) == 13
