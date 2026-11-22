"""StageCheckpoint 读写与 material 断点保存。"""

from __future__ import annotations

from types import SimpleNamespace

from clipwright.agents.material_agent import MaterialAgent
from clipwright.schema.checkpoint import StageCheckpoint
from clipwright.services import checkpoints


def test_checkpoint_roundtrip_and_digest_guard(tmp_path) -> None:
    digest = checkpoints.digest({"stage": "material", "scenes": [1, 2]})
    cp = StageCheckpoint(
        stage="material", task_id="t1", input_digest=digest,
        completed_units=[0], partial_outputs=[{"scene_index": 0, "suggested_assets": [{}]}],
    )
    checkpoints.save(tmp_path, cp)
    loaded = checkpoints.load(tmp_path, "material", digest)
    assert loaded is not None
    assert loaded.completed_units == [0]
    assert checkpoints.load(tmp_path, "material", "other") is None


def test_material_saves_only_scenes_with_assets(tmp_path) -> None:
    input_data = SimpleNamespace(resume_checkpoint={"input_digest": "d1"})
    context = SimpleNamespace(extra_params={"workspace_root": str(tmp_path)}, pipeline_id="task_x")
    candidates = [
        {"scene_index": 0, "suggested_assets": [{"url": "https://x"}]},
        {"scene_index": 1, "suggested_assets": [], "note": "no match"},
    ]
    MaterialAgent._save_checkpoint(object.__new__(MaterialAgent), input_data, context, candidates)
    cp = checkpoints.load(tmp_path, "material", "d1")
    assert cp is not None
    assert cp.completed_units == [0]
    assert cp.failures and cp.failures[0]["scene_index"] == 1
