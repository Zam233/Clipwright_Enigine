"""D23 拍板后的镜头节奏归一：edit_agent 的 merge / split 接线。

`shot_floor` 的纯函数语义已由 ``test_shot_floor.py`` 覆盖；本文件验证**接线**：
场景时长违规时，管线真的把场景合并/切分，成片片段落在类型声明的区间内，而且
**总时长不变**（这是 D23 选择 merge/split 而不是 extend 的全部理由）。

用假 tool + 假 LLM 跑完整 ``EditAgent.execute``，不联网、不出片。
"""

from __future__ import annotations

import pytest

from clipwright.agents.edit_agent import EditAgent
from clipwright.config import settings
from clipwright.schema.agent import AgentContext, AgentDecision, EditInput
from clipwright.tool.registry import ToolRegistry


class _FakeToolResult:
    status = "success"
    output_path = r"C:\tmp\fake_out.mp4"
    output = {"output_path": r"C:\tmp\fake_out.mp4"}


async def _fake_tool_execute(name: str, **kwargs):  # type: ignore[no-untyped-def]
    return _FakeToolResult()


class _NoopLLM:
    """不做剪辑档案决策（structured_output 抛错 → 走确定性规则）。"""

    async def structured_output(self, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("llm off")


def _context(extra: dict | None = None) -> AgentContext:
    return AgentContext(
        pipeline_id="d23-rhythm",
        persona_id="p_default",
        category_plugin_id="vlog_daily",
        topic="节奏归一",
        extra_params={
            "shot_params": {"base_shot_ms": 5000, "min_shot_ms": 9000, "max_shot_ms": 20000},
            **(extra or {}),
        },
    )


def _scenes() -> list[dict]:
    return [
        {"title": "开场", "description": "引入", "duration_sec": 5.0},
        {"title": "铺垫", "description": "展开", "duration_sec": 3.0},
        {"title": "主体", "description": "说明", "duration_sec": 5.0},
        {"title": "细节", "description": "特写", "duration_sec": 6.0},
        {"title": "收尾", "description": "总结", "duration_sec": 5.0},
    ]


def _vid_clips(timeline):  # type: ignore[no-untyped-def]
    track = next(t for t in timeline.tracks if t.kind == "video" and t.name == "视频轨")
    return [round(float(c.duration_sec), 3) for c in track.clips]


async def _run(extra: dict | None = None):  # type: ignore[no-untyped-def]
    agent = EditAgent()
    agent._llm = _NoopLLM()  # type: ignore[assignment]
    ctx = _context(extra)
    out = await agent.execute(
        EditInput(context=ctx, script_skeleton={"scenes": _scenes()}, candidate_clips=[]),
        ctx,
    )
    assert out.decision == AgentDecision.PASS
    assert out.timeline is not None
    return out


@pytest.fixture(autouse=True)
def _patch(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "")  # 不出档案、不联网
    monkeypatch.setattr(ToolRegistry, "execute", _fake_tool_execute)


@pytest.mark.asyncio
async def test_merge_is_default_and_respects_floor() -> None:
    """默认策略 merge：24s / 5 段的规划，全部片段 ≥ 9s，总时长仍 24s。"""
    out = await _run()
    durations = _vid_clips(out.timeline)
    assert sum(durations) == pytest.approx(24.0, abs=0.2)
    assert all(value >= 9.0 - 0.05 for value in durations), durations
    assert any("镜头下限策略[merge]" in note for note in out.edit_notes)


@pytest.mark.asyncio
async def test_off_policy_keeps_original_durations() -> None:
    out = await _run({"shot_floor_policy": "off"})
    durations = _vid_clips(out.timeline)
    assert durations == pytest.approx([5.0, 3.0, 5.0, 6.0, 5.0], abs=0.05)


@pytest.mark.asyncio
async def test_extend_policy_still_available_and_changes_total() -> None:
    out = await _run({"shot_floor_policy": "extend"})
    durations = _vid_clips(out.timeline)
    assert all(value >= 9.0 - 0.05 for value in durations), durations
    assert sum(durations) > 24.0  # extend 会膨胀总时长（D23 因此没选它做默认）


@pytest.mark.asyncio
async def test_split_caps_long_scenes_and_keeps_total() -> None:
    """上界 3s：24s 规划被切成 8 个 3s 镜，总时长不变。"""
    out = await _run({
        "shot_floor_policy": "off",
        "shot_params": {"base_shot_ms": 2400, "min_shot_ms": 600, "max_shot_ms": 3000},
    })
    durations = _vid_clips(out.timeline)
    assert all(value <= 3.0 + 0.05 for value in durations), durations
    assert sum(durations) == pytest.approx(24.0, abs=0.2)
    assert len(durations) > len(_scenes())


@pytest.mark.asyncio
async def test_cap_policy_off_leaves_scenes_alone() -> None:
    out = await _run({
        "shot_floor_policy": "off",
        "shot_cap_policy": "off",
        "shot_params": {"base_shot_ms": 2400, "min_shot_ms": 600, "max_shot_ms": 3000},
    })
    assert _vid_clips(out.timeline) == pytest.approx([5.0, 3.0, 5.0, 6.0, 5.0], abs=0.05)


@pytest.mark.asyncio
async def test_split_reuses_scene_candidates_by_original_index() -> None:
    """切分后的子场景仍按**原场景号**取候选素材（否则第 2 镜起会没素材可放）。"""
    ctx = _context({
        "shot_cap_policy": "split",
        "shot_floor_policy": "off",
        "shot_params": {"base_shot_ms": 2400, "min_shot_ms": 600, "max_shot_ms": 3000},
    })
    candidates = [
        {
            "scene_index": index,
            "suggested_assets": [
                {"asset_id": f"a{index}", "local_path": r"C:\tmp\v.mp4",
                 "duration_sec": 10.0, "score": 1.0}
            ],
        }
        for index in range(len(_scenes()))
    ]
    agent = EditAgent()
    agent._llm = _NoopLLM()  # type: ignore[assignment]
    out = await agent.execute(
        EditInput(context=ctx, script_skeleton={"scenes": _scenes()}, candidate_clips=candidates),
        ctx,
    )
    assert out.decision == AgentDecision.PASS
    # 每个新镜都取到了素材（占位说明会出现在 notes 里，素材缺失则没有）
    assert not [n for n in out.edit_notes if "无可用素材" in n and "场景0" in n]
    track = next(t for t in out.timeline.tracks if t.kind == "video" and t.name == "视频轨")
    assert track.clips and all(c.asset_id for c in track.clips)
