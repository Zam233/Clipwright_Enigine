"""material 断点续跑：已完成场景不再重跑，只补占位场景。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from clipwright.agents import material_agent as mod
from clipwright.agents.material_agent import MaterialAgent
from clipwright.schema.agent import MaterialInput


@pytest.mark.asyncio
async def test_material_resume_reuses_completed_scene(tmp_path, monkeypatch) -> None:
    scenes = [
        {"title": "场景0", "description": "a", "duration_sec": 2, "keywords": []},
        {"title": "场景1", "description": "b", "duration_sec": 2, "keywords": []},
    ]
    calls: list[int] = []

    class _Source:
        pass

    monkeypatch.setattr(mod.MaterialRegistry, "list", classmethod(lambda cls: [_Source()]))

    async def fake_batch(*_a, **_kw):
        return []

    async def fake_scene(self, i, scene, **_kw):
        calls.append(i)
        return {
            "scene_index": i, "scene_title": scene["title"],
            "suggested_assets": [{"url": f"https://example.com/{i}.mp4", "type": "video"}],
        }

    async def fake_download(self, clips, context):
        return {"attempted": 0, "ok": 0, "reason": ""}

    monkeypatch.setattr(mod, "_llm_search_queries_batch", fake_batch)
    monkeypatch.setattr(mod.MaterialAgent, "_process_scene", fake_scene)
    monkeypatch.setattr(mod.MaterialAgent, "_download_candidates", fake_download)
    monkeypatch.setattr(mod, "_placement_enabled", lambda _ctx: False)

    input_data = MaterialInput.model_construct(
        context=SimpleNamespace(),
        script_skeleton={"scenes": scenes},
        persona_config={},
        material_plugin_config={},
        creative_brief={},
        production_plan={},
        resume_checkpoint={
            "input_digest": "d1",
            "completed_units": [0],
            "partial_outputs": [{
                "scene_index": 0, "scene_title": "场景0",
                "suggested_assets": [{"url": "https://example.com/0.mp4", "type": "video"}],
            }],
        },
    )
    context = SimpleNamespace(
        extra_params={"workspace_root": str(tmp_path)},
        pipeline_id="task_test", category_plugin_id="", topic="",
    )

    output = await MaterialAgent().execute(input_data, context)

    assert calls == [1], "已完成的场景不应重新检索"
    assert sorted(c["scene_index"] for c in output.candidate_clips) == [0, 1]
    cp = __import__("clipwright.services.checkpoints", fromlist=["load"]).load(
        tmp_path, "material", "d1"
    )
    assert cp is not None and 0 in cp.completed_units
