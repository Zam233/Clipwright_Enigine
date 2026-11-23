"""stage_inputs 必须把生成图片下发给所有子 Agent。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from clipwright.schema.agent import AgentContext
from clipwright.schema.timeline import Timeline
from clipwright.services import stage_inputs
from clipwright.services.workspace_materials import workspace_image_entries


def _task(tmp_path):
    return SimpleNamespace(
        id="task_img_inputs",
        request=SimpleNamespace(
            persona_id="", category_plugin_id="", topic="t",
            extra={"workspace_root": str(tmp_path)},
        ),
    )


@pytest.mark.parametrize("stage,board,mins", [
    ("requirements", {}, {"topic": "t"}),
    ("structure", {}, {}),
    ("material", {"script_skeleton": {"scenes": [{"title": "s"}]}}, {}),
    ("edit", {"script_skeleton": {"scenes": []}, "candidate_clips": []}, {}),
    ("animation", {"timeline": Timeline()}, {}),
    ("audio", {"timeline": Timeline()}, {}),
    ("quality", {"timeline": Timeline()}, {}),
])
def test_all_stage_inputs_receive_workspace_images(tmp_path, stage, board, mins) -> None:
    image_dir = tmp_path / "materials" / "image"
    image_dir.mkdir(parents=True)
    image = image_dir / "A_dark_launcher_screenshot_abc.png"
    image.write_bytes(b"png")

    ctx = AgentContext(
        pipeline_id="task_img_inputs", persona_id="", category_plugin_id="",
        topic="t", extra_params={"workspace_root": str(tmp_path)},
    )
    inp = stage_inputs.assemble(stage, task=_task(tmp_path), board=board, context=ctx)
    if isinstance(inp, dict):
        assets = inp["image_assets"]
    else:
        assets = inp.image_assets
    assert any(str(a.get("path", "")).endswith(image.name) for a in assets)
    assert ctx.extra_params.get("image_assets") == assets


def test_generated_image_entries_reads_workspace(tmp_path) -> None:
    image_dir = tmp_path / "materials" / "image"
    image_dir.mkdir(parents=True)
    image = image_dir / "launcher_ui.png"
    image.write_bytes(b"png")
    entries = workspace_image_entries(tmp_path)
    assert entries
    assert entries[0]["path"].endswith(image.name)
    assert entries[0]["asset_id"] == "launcher_ui"


def test_generated_image_entries_without_workspace_is_empty(tmp_path) -> None:
    assert workspace_image_entries(tmp_path) == []
