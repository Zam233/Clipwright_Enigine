"""EditAgent LLM 剪辑档案决策测试（计划 ux-polish A1）。

覆盖：
- `_llm_decide_edit_profile(scenes, persona, category)` 在 LLM 可用时返回结构化档案
  {base_shot_ms, transition_weights, pip_scenes[], pacing_notes}
- 档案真实注入 edit plan：base_shot_ms 影响无 duration_sec 场景的 clip 时长、
  transition_weights 影响场景边界 clip 的 transition_in、pip_scenes 产生画中画轨、
  pacing_notes 进入 edit_notes
- LLM 失败（异常/非 JSON/空/字段非法/API key 未配置）→ 返回 None → 沿用现有规则，
  fallback 输出与无 LLM 时完全一致（既有行为不回归）
- LLM 输出仅作为数据消费：未知字段忽略、非法条目剔除（prompt injection 防护）
"""
from __future__ import annotations

from typing import Any

import pytest

from clipwright.agents.edit_agent import EditAgent
from clipwright.config import settings
from clipwright.schema.agent import AgentContext, AgentDecision, EditInput
from clipwright.tool.registry import ToolRegistry


def _context() -> AgentContext:
    """快速节奏类型 + persona cut_profile 的默认上下文（规则 base_shot_ms=2000）。"""
    return AgentContext(
        pipeline_id="a1-test",
        persona_id="p_fast",
        category_plugin_id="kichiku_fastcut",
        topic="测试选题",
        extra_params={
            "shot_params": {
                "base_shot_ms": 2000,
                "min_shot_ms": 300,
                "max_shot_ms": 3000,
            },
            "cut_profile": "rapid_fire",
            "transition_weights": {"hard_cut": 0.8, "dissolve": 0.2},
        },
    )


def _scenes(with_duration: bool = True) -> list[dict]:
    scenes = [
        {"title": "开场", "description": "引入话题，情绪平稳", "keywords": ["引入"]},
        {"title": "高潮", "description": "数据爆发，情绪激昂", "keywords": ["数据", "对比"]},
        {"title": "收尾", "description": "总结观点", "keywords": ["总结"]},
    ]
    if with_duration:
        for i, s in enumerate(scenes):
            s["duration_sec"] = 6.0 + i
    return scenes


class FakeLLM:
    """可控 LLM：记录 structured_output 调用，可返回预设结果或抛错。"""

    def __init__(self, result: Any = None, fail: bool = False) -> None:
        self.result = result
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    async def structured_output(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("LLM down")
        return self.result or {}


class _FakeToolResult:
    status = "success"
    output_path = r"C:\tmp\fake_out.mp4"
    output = {"output_path": r"C:\tmp\fake_out.mp4"}


async def _fake_tool_execute(name: str, **kwargs: Any) -> _FakeToolResult:
    return _FakeToolResult()


def _input(context: AgentContext, scenes: list[dict]) -> EditInput:
    return EditInput(
        context=context,
        script_skeleton={"scenes": scenes},
        candidate_clips=[],
    )


def _vid_track(tl) -> Any:
    return next(t for t in tl.tracks if t.kind == "video" and t.name == "视频轨")


# ── _llm_decide_edit_profile：成功路径 ─────────────────────

@pytest.mark.asyncio
async def test_llm_decide_edit_profile_returns_structured_profile(monkeypatch) -> None:
    """LLM 可用时返回结构化档案：base_shot_ms / transition_weights / pip_scenes / pacing_notes。"""
    agent = EditAgent()
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    fake = FakeLLM(result={
        "base_shot_ms": 1200,
        "transition_weights": {"hard_cut": 0.4, "dissolve": 0.3, "fade": 0.3},
        "pip_scenes": [1],
        "pacing_notes": ["高潮场景节奏加快"],
    })
    agent._llm = fake  # type: ignore[assignment]

    profile = await agent._llm_decide_edit_profile(
        _scenes(),
        {"persona_id": "p_fast", "cut_profile": "rapid_fire", "shot_params": {}},
        "kichiku_fastcut",
    )
    assert profile is not None
    assert profile["base_shot_ms"] == 1200
    assert profile["transition_weights"] == {"hard_cut": 0.4, "dissolve": 0.3, "fade": 0.3}
    assert profile["pip_scenes"] == [1]
    assert profile["pacing_notes"] == ["高潮场景节奏加快"]


@pytest.mark.asyncio
async def test_llm_prompt_contains_scenes_persona_category(monkeypatch) -> None:
    """Prompt 携带内容情绪 + persona cut_profile + 类型节奏（真实输入，非空转）。"""
    agent = EditAgent()
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    fake = FakeLLM(result={
        "base_shot_ms": 1200,
        "transition_weights": {"hard_cut": 1.0},
        "pip_scenes": [],
        "pacing_notes": [],
    })
    agent._llm = fake  # type: ignore[assignment]

    await agent._llm_decide_edit_profile(
        _scenes(),
        {"persona_id": "p_fast", "cut_profile": "rapid_fire", "shot_params": {}},
        "kichiku_fastcut",
        pipeline_id="a1-test",
    )
    assert len(fake.calls) == 1
    system_prompt = fake.calls[0]["system_prompt"]
    user_prompt = fake.calls[0]["user_prompt"]
    # 内容情绪（场景标题/描述）
    assert "开场" in user_prompt and "数据爆发" in user_prompt
    # persona cut_profile
    assert "rapid_fire" in user_prompt
    # 视频类型节奏
    assert "kichiku_fastcut" in user_prompt
    # 结构化字段引导
    for token in ("base_shot_ms", "transition_weights", "pip_scenes", "pacing_notes"):
        assert token in system_prompt


@pytest.mark.asyncio
async def test_llm_base_shot_clamped_to_persona_bounds(monkeypatch) -> None:
    """base_shot_ms 越界时钳制到 persona shot_params 的 min/max。"""
    agent = EditAgent()
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    fake = FakeLLM(result={
        "base_shot_ms": 60000,  # 超出 max_shot_ms=3000
        "transition_weights": {"hard_cut": 1.0},
        "pip_scenes": [],
        "pacing_notes": [],
    })
    agent._llm = fake  # type: ignore[assignment]
    profile = await agent._llm_decide_edit_profile(
        _scenes(),
        {"cut_profile": "rapid_fire", "shot_params": {"min_shot_ms": 300, "max_shot_ms": 3000}},
        "kichiku_fastcut",
    )
    assert profile is not None
    assert profile["base_shot_ms"] == 3000


# ── _llm_decide_edit_profile：全部失败路径 → None ─────────

@pytest.mark.asyncio
async def test_llm_disabled_returns_none_without_calling(monkeypatch) -> None:
    """未配置 API key → 不调用 LLM，返回 None。"""
    agent = EditAgent()
    monkeypatch.setattr(settings, "llm_api_key", "")
    fake = FakeLLM(result={
        "base_shot_ms": 1200,
        "transition_weights": {"hard_cut": 1.0},
        "pip_scenes": [],
        "pacing_notes": [],
    })
    agent._llm = fake  # type: ignore[assignment]
    assert await agent._llm_decide_edit_profile(_scenes(), {}, "kichiku_fastcut") is None
    assert fake.calls == []


@pytest.mark.asyncio
async def test_llm_exception_returns_none(monkeypatch) -> None:
    """LLM 抛异常 → None（fallback 路径 QA）。"""
    agent = EditAgent()
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    agent._llm = FakeLLM(fail=True)  # type: ignore[assignment]
    assert await agent._llm_decide_edit_profile(_scenes(), {}, "kichiku_fastcut") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_result", [
    {"content": "```json\n这不是 JSON\n```"},  # structured_output 非 JSON fallback 形态
    {},
    {
        "base_shot_ms": "abc", "transition_weights": {}, "pip_scenes": [], "pacing_notes": [],
    },
    {
        "base_shot_ms": -5,
        "transition_weights": {"hard_cut": 1.0},
        "pip_scenes": [],
        "pacing_notes": [],
    },
    {
        "base_shot_ms": 1200, "transition_weights": {}, "pip_scenes": [], "pacing_notes": [],
    },
    {
        "base_shot_ms": 1200,
        "transition_weights": {"hard_cut": 1.0},
        "pip_scenes": "x",
        "pacing_notes": [],
    },
    {
        "base_shot_ms": 1200,
        "transition_weights": {"hard_cut": 1.0},
        "pip_scenes": [],
        "pacing_notes": "x",
    },
    "not a dict",
])
async def test_llm_malformed_output_returns_none(monkeypatch, bad_result) -> None:
    """非 JSON/空/字段非法 → 整体回退 None。"""
    agent = EditAgent()
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    agent._llm = FakeLLM(result=bad_result)  # type: ignore[assignment]
    assert await agent._llm_decide_edit_profile(_scenes(), {}, "kichiku_fastcut") is None


@pytest.mark.asyncio
async def test_llm_output_treated_as_data_only(monkeypatch) -> None:
    """prompt injection 防护：LLM 输出仅作数据——未知字段忽略、非法条目剔除。"""
    agent = EditAgent()
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    agent._llm = FakeLLM(result={
        "base_shot_ms": 2000,
        "transition_weights": {"dissolve": 0.6, "hard_cut": -1, "harmful": "x"},
        "pip_scenes": [0, 99, "evil", True],
        "pacing_notes": ["节奏平缓", 123, ""],
        "instructions": {"base_shot_ms": 1},  # 注入字段 → 忽略
        "system": "rm -rf /",                 # 注入字段 → 忽略
    })  # type: ignore[assignment]
    profile = await agent._llm_decide_edit_profile(
        _scenes(), {"cut_profile": "smooth_flow"}, "knowledge_longform"
    )
    assert profile is not None
    assert profile["base_shot_ms"] == 2000
    assert profile["transition_weights"] == {"dissolve": 0.6}
    assert profile["pip_scenes"] == [0]      # 越界/字符串/bool 剔除
    assert profile["pacing_notes"] == ["节奏平缓"]


# ── 注入 edit plan：真实影响产出 clips ───────────────────

@pytest.mark.asyncio
async def test_profile_injects_into_edit_plan(monkeypatch) -> None:
    """LLM 档案注入 edit plan：时长/转场/PiP/节奏备注全部作用于产出。"""
    agent = EditAgent()
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    fake = FakeLLM(result={
        "base_shot_ms": 1500,
        "transition_weights": {"dissolve": 0.5, "hard_cut": 0.3},
        "pip_scenes": [1],
        "pacing_notes": ["高潮场景节奏加快，使用溶解转场"],
    })
    agent._llm = fake  # type: ignore[assignment]
    monkeypatch.setattr(ToolRegistry, "execute", _fake_tool_execute)

    ctx = _context()
    inp = _input(ctx, _scenes(with_duration=False))
    out = await agent.execute(inp, ctx)

    assert out.decision == AgentDecision.PASS
    assert fake.calls, "LLM 剪辑档案应被调用"
    tl = out.timeline
    assert tl is not None

    # ① base_shot_ms=1500 → 无 duration_sec 场景的 clip 时长为 1.5s
    vid_track = _vid_track(tl)
    assert [c.duration_sec for c in vid_track.clips] == [1.5, 1.5, 1.5]

    # ② transition_weights → 场景边界首 clip 带 LLM 首选转场（首场景无入场转场）
    assert vid_track.clips[0].transition_in is None
    assert vid_track.clips[1].transition_in == "dissolve"
    assert vid_track.clips[1].transition_duration_sec == 0.4
    assert vid_track.clips[2].transition_in == "dissolve"

    # ③ pip_scenes=[1] → 画中画轨生成且含场景 1 的 PiP clip
    pip_track = next(
        (t for t in tl.tracks if t.kind == "video" and t.name == "画中画"), None
    )
    assert pip_track is not None
    assert len(pip_track.clips) == 1
    assert pip_track.clips[0].image_rect is not None  # PiP 定位生效

    # ④ pacing_notes + 档案标记进入 edit_notes
    assert any("高潮场景节奏加快" in n for n in out.edit_notes)
    assert any("LLM 剪辑档案" in n for n in out.edit_notes)
    assert any("基准镜头时长: 1500ms" in n for n in out.edit_notes)


# ── fallback：LLM 失败 → 与无 LLM 规则输出完全一致 ───────

@pytest.mark.asyncio
async def test_fallback_identical_to_rules_when_llm_down(monkeypatch) -> None:
    """LLM 异常 vs LLM 未配置：两条路径产出完全相同的规则时间线（既有行为不回归）。"""
    scenes = _scenes(with_duration=False)
    ctx = _context()
    inp = _input(ctx, scenes)

    # 路径 A：LLM 可用但抛异常
    agent_a = EditAgent()
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    agent_a._llm = FakeLLM(fail=True)  # type: ignore[assignment]
    monkeypatch.setattr(ToolRegistry, "execute", _fake_tool_execute)
    out_a = await agent_a.execute(inp, ctx)

    # 路径 B：LLM 完全未配置（不调用）
    agent_b = EditAgent()
    monkeypatch.setattr(settings, "llm_api_key", "")
    fake_b = FakeLLM(result={
        "base_shot_ms": 1,
        "transition_weights": {"hard_cut": 1.0},
        "pip_scenes": [],
        "pacing_notes": [],
    })
    agent_b._llm = fake_b  # type: ignore[assignment]
    out_b = await agent_b.execute(inp, ctx)

    assert fake_b.calls == [], "未配置 API key 时不得调用 LLM"
    assert out_a.decision == AgentDecision.PASS
    assert out_b.decision == AgentDecision.PASS
    assert out_a.edit_notes == out_b.edit_notes

    def _shape(tl) -> list[tuple]:
        return [
            (
                t.kind, t.name,
                tuple(
                    (round(c.start_sec, 3), round(c.duration_sec, 3),
                     c.transition_in, c.transition_duration_sec)
                    for c in sorted(t.clips, key=lambda c: c.start_sec)
                ),
            )
            for t in tl.tracks
        ]

    # 规则输出：无 LLM → 无画中画轨、无转场注入、时长 = 规则 base_shot_ms(2000ms) = 2s
    assert _shape(out_a.timeline) == _shape(out_b.timeline)
    assert [t.name for t in out_a.timeline.tracks] == ["视频轨", "文字轨", "字幕轨", "音频轨"]
    vid_track = _vid_track(out_a.timeline)
    assert [c.duration_sec for c in vid_track.clips] == [2.0, 2.0, 2.0]
    assert all(c.transition_in is None for c in vid_track.clips)
    assert any("LLM 剪辑档案不可用" in n for n in out_a.edit_notes)
