"""配音锚定的时间线不得被交叉转场侵蚀（E2E 2026-10-28）。

现象：702.336s 配音、43 段分镜的知识区长片，渲出来 **686.1s 且没有声音**：
1. 主 agent 派完 edit 就收尾，audio 阶段没跑 → 黑板只有 `timeline`、没有
   `final_timeline`，音频轨 43 个 clip 全空 `asset_id`；
2. 42 个场景边界都带 `transition_in=dissolve/0.4s`，而 clip 是首尾相接放的，
   xfade 每处吃 0.4s → 视频总长 702.336 − 16.8 = 685.5s，配音/字幕却按绝对
   时间铺 → 音画不同步、末尾少 17 秒画面。

修复：
1. 收尾守门只认 `final_timeline`（audio 阶段产物），并明确要求派 audio；
2. 配音锚定（有上传 audio_path 或 video_mode=voiceover）时切点一律硬切。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from clipwright.schema.agent import AgentContext
from clipwright.services import stage_inputs


def _ctx(extra: dict) -> AgentContext:
    return AgentContext(pipeline_id="p", persona_id="", category_plugin_id="knowledge_longform",
                        topic="t", extra_params=extra)


def test_narration_anchored_is_detected_for_voiceover():
    """voiceover / 上传配音 → 锚定（这两个条件任一成立）。"""
    from clipwright.agents.edit_agent import EditAgent

    fn = EditAgent._is_narration_anchored
    assert fn(_ctx({"video_mode": "voiceover"})) is True
    assert fn(_ctx({"audio_path": "/tmp/a.mp3"})) is True
    assert fn(_ctx({})) is False


def test_final_timeline_is_the_only_done_signal(tmp_path, monkeypatch):
    """守门只认 final_timeline：只有 timeline 时必须继续推，不能判 done。"""
    from clipwright.services import agent_kernel as ak
    from clipwright.services import task_blackboard as bb
    from clipwright.schema.task import TaskRequest

    calls: list[str] = []

    async def driver(task, *, context_text="", **kwargs):  # noqa: ANN001, ANN003, ANN201
        calls.append(context_text)
        if len(calls) == 1:
            # 第一次只产出中间时间线（edit），没有 final_timeline
            bb.put(task.id, "timeline", {"tracks": [], "duration_sec": 702.3}, by="edit")
        else:
            bb.put(task.id, "final_timeline", {"tracks": [], "duration_sec": 702.3}, by="audio")
        return {"status": "ok", "finish": {"conclusion": "ok"}, "tool_calls": [], "rounds": 1}

    deps = ak.KernelDeps(driver=driver, checkpoint_dir=tmp_path)
    k = ak.AgentKernel(deps=deps)
    ak._TASKS.clear()
    ak._CONTROL.clear()
    ak._TIMELINE_CONTINUATIONS.clear()
    task = k.create(TaskRequest(topic="x", plan_only=False))
    task = asyncio.run(k.run(task))

    assert len(calls) == 2, "只有 timeline 时必须再推一把去跑 audio"
    assert "必须再派 `audio`" in calls[1]
    assert task.status == "done"


def test_empty_scenes_still_fill_from_board():
    """显式传空值仍从黑板补齐（防 edit 拿到 0 场景）。"""
    scenes = [{"title": "s", "description": "d", "duration_sec": 1, "keywords": ["k"],
               "voiceover_script": "v"}]
    board = {"script_skeleton": {"scenes": scenes}, "candidate_clips": [{"asset_id": "a"}]}
    task = SimpleNamespace(id="t", request=SimpleNamespace(
        topic="t", persona_id="", category_plugin_id="", extra={}))

    inp = stage_inputs.assemble("edit", task=task, board=board,
                                override={"script_skeleton": {}, "candidate_clips": []})

    assert inp.script_skeleton.get("scenes") == scenes
    assert inp.candidate_clips == [{"asset_id": "a"}]
