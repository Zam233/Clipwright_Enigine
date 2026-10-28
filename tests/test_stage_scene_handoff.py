"""stage 之间的分镜交接契约（E2E 2026-10-28 发现的 P0 断链）。

现象（K:\\demo 一镜到底，主 agent 第 2 步就死）：
1. `delegate_agent(structure)` 报 ok、`分镜 6 个`；
2. `delegate_agent(material)` → 候选素材 0 条 → 「scenes=[] 为空」；
3. 重派 structure 两次都一样，最终 `task_end=failed`。

根因：`MaterialAgent`（material_agent.py:719）与 `EditAgent`（edit_agent.py:276）
都读 `input_data.script_skeleton.get("scenes", [])`，而 `StructureOutput` 里
scenes 是**平级字段**，`script_skeleton` 只有 topic/tone/scene_count/_warnings；
黑板上的 `scenes` 又是个独立 key（stage_merge 白名单），从没进过 skeleton。
于是 material 永远看到空分镜。

契约（双向兜底）：
- structure 输出时把 scenes 同步进 script_skeleton；
- stage_inputs.assemble 发现 skeleton 没 scenes、黑板有 scenes 时补进去。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from clipwright.services import stage_inputs


def _task() -> SimpleNamespace:
    request = SimpleNamespace(
        topic="t", persona_id="", category_plugin_id="knowledge_longform", extra={},
    )
    return SimpleNamespace(id="task_x", request=request)


SCENES = [
    {"title": "s0", "description": "d0", "duration_sec": 10, "keywords": ["k0"],
     "voiceover_script": "v0"},
    {"title": "s1", "description": "d1", "duration_sec": 20, "keywords": ["k1"],
     "voiceover_script": "v1"},
]


def test_material_input_picks_up_board_scenes():
    """黑板里有 scenes、skeleton 里没有 → assemble 必须补齐。"""
    board = {
        "script_skeleton": {"topic": "t", "tone": "冷", "scene_count": 2},
        "scenes": SCENES,
    }

    inp = stage_inputs.assemble("material", task=_task(), board=board)

    assert inp.script_skeleton["scenes"] == SCENES, (
        "material 只读 script_skeleton['scenes']，不补就等于给它 scenes=[]"
    )
    assert inp.script_skeleton["tone"] == "冷", "原有字段不能被覆盖丢失"


def test_edit_input_picks_up_board_scenes():
    board = {
        "script_skeleton": {"topic": "t"},
        "candidate_clips": [{"asset_id": "a"}],
        "scenes": SCENES,
    }

    inp = stage_inputs.assemble("edit", task=_task(), board=board)

    assert inp.script_skeleton["scenes"] == SCENES


def test_skeleton_own_scenes_win():
    """skeleton 自带 scenes 时不覆盖（尊重显式传入）。"""
    own = [{**SCENES[0], "title": "skeleton-own"}]
    board = {"script_skeleton": {"scenes": own}, "scenes": SCENES}

    inp = stage_inputs.assemble("material", task=_task(), board=board)

    assert inp.script_skeleton["scenes"] == own


def test_no_scenes_anywhere_is_untouched():
    board = {"script_skeleton": {"topic": "t"}}

    inp = stage_inputs.assemble("material", task=_task(), board=board)

    assert "scenes" not in inp.script_skeleton


@pytest.mark.parametrize("stage", ["material", "edit"])
def test_missing_required_key_still_raises(stage):
    with pytest.raises(stage_inputs.StageInputMissing):
        stage_inputs.assemble(stage, task=_task(), board={})
