"""EditAgent 首段 EDL 预取走 video_trim_batch。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from clipwright.agents.edit_agent import (
    EditAgent, _TRIM_CACHE, _trim_cache_get,
)


def test_prefetch_first_trims_batches(tmp_path) -> None:
    _TRIM_CACHE.clear()
    agent = EditAgent.__new__(EditAgent)
    calls: list[dict] = []

    async def fake_call(name, **kwargs):
        calls.append({"name": name, **kwargs})
        items = [{"index": i, "status": "ok", "output_path": str(tmp_path / f"t{i}.mp4")}
                 for i, _ in enumerate(kwargs.get("items") or [])]
        return SimpleNamespace(status="success", output={"items": items})

    agent.call_tool = fake_call  # type: ignore[method-assign]
    for i in range(2):
        (tmp_path / f"t{i}.mp4").write_bytes(b"x")
    scenes = [{"title": "s", "_orig_index": 0, "duration_sec": 15.0}]
    candidates = [{"scene_index": 0, "suggested_assets": [
        {"asset_id": "a", "local_path": "/tmp/a.mp4", "duration_sec": 10.0, "score": 1},
    ]}]
    asyncio.run(agent._prefetch_first_trims(
        scenes, candidates, duration_scale=1.0, base_shot_sec=4.0,
    ))
    assert calls and calls[0]["name"] == "video_trim_batch"
    assert calls[0]["items"][0]["input_path"] == "/tmp/a.mp4"
    assert len(calls[0]["items"]) == 2
    assert _trim_cache_get("/tmp/a.mp4", 0.0, 10.0) == str(tmp_path / "t0.mp4")
    assert _trim_cache_get("/tmp/a.mp4", 5.0, 5.0) == str(tmp_path / "t1.mp4")
    _TRIM_CACHE.clear()


def test_process_scene_units_uses_precomputed_without_trim() -> None:
    from types import SimpleNamespace

    agent = EditAgent.__new__(EditAgent)
    calls: list[str] = []

    async def fake_call(name, **_kwargs):
        calls.append(name)
        raise AssertionError("precomputed path should not call tools")

    agent.call_tool = fake_call  # type: ignore[method-assign]
    scene = {"title": "s", "_orig_index": 0, "duration_sec": 4.0}
    candidates = [{"scene_index": 0, "suggested_assets": [
        {"asset_id": "a", "local_path": "/tmp/a.mp4", "duration_sec": 20.0, "score": 1},
    ]}]
    out = asyncio.run(agent._process_scene_units(
        0, scene, candidates, 4.0, SimpleNamespace(pipeline_id="p"),
        precomputed={(0, 0): {"path": "/tmp/trim.mp4", "source": "/tmp/a.mp4",
                              "start": 0.0, "duration": 4.0}},
    ))
    assert out and out[0]["processed_path"] == "/tmp/trim.mp4"
    assert calls == []
