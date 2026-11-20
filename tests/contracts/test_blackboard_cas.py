"""B05：黑板 revision / CAS 提交 / 冲突不覆盖 / inline 不写生产黑板。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from clipwright.services import stage_adapter, stage_merge, stage_subagent, task_blackboard as bb


def setup_function(_fn=None):
    bb.reset()


def test_revision_increments_on_writes():
    assert bb.revision("t") == 0
    bb.put("t", "a", 1, by="structure")
    assert bb.revision("t") == 1
    bb.merge("t", {"b": 2, "c": 3}, by="structure")
    assert bb.revision("t") == 3


def test_commit_rejects_stale_revision_without_writing():
    bb.put("t", "a", 1, by="structure")  # r1
    ok, rev, written = bb.commit("t", {"b": 2}, by="structure", expect_revision=0)
    assert ok is False and written == [] and rev == 1
    assert "b" not in bb.get("t")

    ok, rev, written = bb.commit("t", {"b": 2}, by="structure", expect_revision=1)
    assert ok is True and written == ["b"] and rev == 2


def test_snapshot_revision_is_consistent():
    bb.put("t", "a", 1, by="structure")
    board, rev = bb.snapshot_with_revision("t")
    assert board["a"] == 1 and rev == bb.revision("t")


def test_reset_timeline_bumps_revision():
    bb.put("t", "timeline", {"tracks": []}, by="edit")
    rev = bb.revision("t")
    bb.reset_timeline("t", {"tracks": [{"kind": "video", "clips": []}]}, by="animation",
                      reason="redo")
    assert bb.revision("t") == rev + 1


def test_stage_merge_conflict_writes_nothing():
    bb.put("t", "script_skeleton", {"v": 1}, by="structure")  # r1
    writes, errors = stage_merge.merge(
        "structure", {"script_skeleton": {"v": 2}, "scenes": [{"id": "s1"}]},
        task_id="t", expect_revision=0,
    )
    assert writes == [] and errors and "board_conflict" in errors[0]
    assert bb.get("t")["script_skeleton"] == {"v": 1}
    assert "scenes" not in bb.get("t")


@pytest.mark.asyncio
async def test_inline_does_not_touch_board(monkeypatch):
    from clipwright.schema.agent import StructureOutput

    bb.reset()

    async def fake_inline(stage, input_obj, context, timeout_sec=None):
        return StructureOutput(scenes=[{"id": "s1", "duration": 5.0}])

    monkeypatch.setattr(stage_subagent, "run_inline", fake_inline)
    ctx = SimpleNamespace(pipeline_id="task_inline")
    result = await stage_adapter.run_inline("structure", object(), ctx)
    assert result.mode == "inline"
    assert bb.get("task_inline") == {}
    assert bb.revision("task_inline") == 0
