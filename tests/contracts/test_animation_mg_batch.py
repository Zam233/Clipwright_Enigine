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
