"""显式 inputs 传空值时必须回退黑板（E2E 2026-10-28：edit 拿到 0 场景）。

现象：主 agent 派 edit 时传了 `script_skeleton`（但里面没有 scenes）与空
`candidate_clips`，而 `run_stage` 在"有显式 inputs"时把 `board` 置空 →
`assemble` 无从补齐 → `EditAgent: 0 个场景, 0 候选素材` → 空时间线 →
收尾守门判失败。

契约（PRD §16.2 与可用性的交点）：
- **校验**只认显式键（模型必须知道该传什么）；
- **取值**时，大对象（script_skeleton / candidate_clips / scenes / timeline）
  若显式值是空的，回退到黑板上游产物——这些对象由上游阶段产出，
  要求模型把 43 个场景内联进来既不现实也没有意义。
"""

from __future__ import annotations

from types import SimpleNamespace

from clipwright.services import stage_inputs


def _task() -> SimpleNamespace:
    request = SimpleNamespace(
        topic="t", persona_id="", category_plugin_id="knowledge_longform", extra={},
    )
    return SimpleNamespace(id="task_x", request=request)


SCENES = [{"title": "s0", "description": "d0", "duration_sec": 10,
           "keywords": ["k"], "voiceover_script": "v"}]
CLIPS = [{"asset_id": "a1", "scene_index": 0}]


def test_empty_explicit_values_fall_back_to_board():
    board = {
        "script_skeleton": {"scenes": SCENES},
        "candidate_clips": CLIPS,
        "scenes": SCENES,
    }
    override = {"script_skeleton": {}, "candidate_clips": []}

    inp = stage_inputs.assemble("edit", task=_task(), board=board, override=override)

    assert inp.script_skeleton.get("scenes") == SCENES
    assert inp.candidate_clips == CLIPS, "空 candidate_clips 必须回退黑板，否则 edit 是空转"


def test_explicit_non_empty_values_still_win():
    board = {"script_skeleton": {"scenes": SCENES}, "candidate_clips": CLIPS}
    mine = [{"asset_id": "mine"}]

    inp = stage_inputs.assemble(
        "edit", task=_task(), board=board,
        override={"candidate_clips": mine, "script_skeleton": {"scenes": SCENES}},
    )

    assert inp.candidate_clips == mine, "主 agent 显式给的候选素材优先"


def test_empty_timeline_falls_back_to_board():
    timeline = {"tracks": [], "duration_sec": 12.0}
    board = {"timeline": timeline}

    inp = stage_inputs.assemble(
        "animation", task=_task(), board=board, override={"timeline": {}},
    )

    assert inp.timeline is not None
    assert getattr(inp.timeline, "duration_sec", None) == 12.0
