"""timeline_insert_images：批量插入、逐项失败、dry-run。"""

from __future__ import annotations

import asyncio

import pytest

from clipwright.schema.timeline import Timeline
from clipwright.services import task_blackboard as bb
from clipwright.tool.timeline_insert_image import TimelineInsertImagesTool


@pytest.fixture
def inserted(tmp_path):
    task_id = "task_insert_images"
    image = tmp_path / "materials" / "image" / "a.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"png")
    bb.reset(task_id)
    bb.put(task_id, "timeline", Timeline().model_dump(mode="json"), by="edit")
    yield task_id, image
    bb.reset(task_id)


def test_batch_insert(inserted) -> None:
    task_id, image = inserted
    out = asyncio.run(TimelineInsertImagesTool().execute(
        task_id=task_id,
        items=[{"asset_ref": str(image), "position": "end", "duration_sec": 2},
               {"asset_ref": str(image), "position": "end", "duration_sec": 3}],
        workspace_root=str(image.parents[2]),
    ))
    assert str(out.status) == "success"
    assert out.output["items"][0]["status"] == "ok"
    assert out.output["items"][1]["status"] == "ok"
    assert len(out.output["added_clip_ids"]) == 2
    timeline = Timeline(**bb.get(task_id)["timeline"])
    assert sum(len(t.clips) for t in timeline.tracks) == 2


def test_partial_failure_and_dry_run(inserted) -> None:
    task_id, image = inserted
    out = asyncio.run(TimelineInsertImagesTool().execute(
        task_id=task_id,
        items=[{"asset_ref": str(image)}, {"asset_ref": "missing.png"}],
        workspace_root=str(image.parents[2]),
    ))
    assert str(out.status) == "success"
    assert out.output["items"][0]["status"] == "ok"
    assert out.output["items"][1]["status"] == "failed"

    dry = asyncio.run(TimelineInsertImagesTool().execute(
        task_id=task_id, items=[{"asset_ref": str(image)}],
        workspace_root=str(image.parents[2]), dry_run=True,
    ))
    assert str(dry.status) == "success"
    assert dry.output["items"][0]["status"] == "preview"
