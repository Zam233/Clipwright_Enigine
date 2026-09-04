"""Tests for A2 — AudioAgent LLM BGM emotion matching (with rule fallback)."""

from __future__ import annotations

from typing import Any

import pytest

from clipwright.agents.audio_agent import AudioAgent
from clipwright.schema.agent import AgentContext, AgentDecision, AudioInput
from clipwright.schema.timeline import Clip, ClipKind, Timeline, Track

BGM_SLOTS = {
    "intro": ["ambient"],
    "backing": ["piano"],
    "climax": ["epic"],
    "outro": ["calm"],
}

SCENES_EMOTIONS = [
    {"index": 0, "title": "开场", "description": "静谧的清晨，城市苏醒", "emotion": "calm"},
    {"index": 1, "title": "核心", "description": "产品发布的高光时刻", "emotion": "excited"},
    {"index": 2, "title": "结尾", "description": "总结与展望", "emotion": "warm"},
]

GOOD_RESULT = {
    "allocations": [
        {
            "slot": "intro",
            "style": "warm ambient piano",
            "volume_envelope": [
                {"t": 0.0, "v": 0.25},
                {"t": 0.5, "v": 0.4},
                {"t": 1.0, "v": 0.2},
            ],
            "pause_design": {"pause_before_sec": 0.0, "pause_after_sec": 1.5},
        },
        {
            "slot": "climax",
            "style": "tense electronic build",
            "volume_envelope": [
                {"t": 0.0, "v": 0.5},
                {"t": 1.0, "v": 0.6},
            ],
            "pause_design": {"pause_before_sec": 0.5, "pause_after_sec": 0.0},
        },
    ]
}


def _ctx(**extra: object) -> AgentContext:
    return AgentContext(
        pipeline_id="p_llm_bgm",
        persona_id="test_persona",
        category_plugin_id="test_plugin",
        topic="test topic",
        extra_params={**extra},
    )


def _bgm_timeline() -> Timeline:
    """3 个 BGM clip：0s(→intro) / 40s(→climax) / 80s(→outro)，总长 100s。"""
    tl = Timeline(duration_sec=100.0)
    tl.tracks.append(Track(
        id="a_bgm",
        name="BGM",
        kind=ClipKind.AUDIO,
        index=0,
        clips=[
            Clip(id="bgm1", kind=ClipKind.AUDIO, asset_id="/bgm1.mp3",
                 track_id="a_bgm", start_sec=0.0, duration_sec=15.0),
            Clip(id="bgm2", kind=ClipKind.AUDIO, asset_id="/bgm2.mp3",
                 track_id="a_bgm", start_sec=40.0, duration_sec=15.0),
            Clip(id="bgm3", kind=ClipKind.AUDIO, asset_id="/bgm3.mp3",
                 track_id="a_bgm", start_sec=80.0, duration_sec=15.0),
        ],
    ))
    return tl


def _audio_input(tl: Timeline) -> AudioInput:
    return AudioInput(
        context=_ctx(),
        timeline=tl,
        audio_config={"bgm_slots": BGM_SLOTS},
        production_plan={"scenes": SCENES_EMOTIONS},
    )


class FakeLLM:
    """记录 structured_output 调用并返回预设 allocations。"""

    def __init__(self, result: Any = None) -> None:
        self.result = result if result is not None else {"allocations": []}
        self.called = False
        self.system_prompt = ""
        self.user_prompt = ""

    async def structured_output(self, **kwargs: Any) -> dict:
        self.called = True
        self.system_prompt = kwargs.get("system_prompt", "")
        self.user_prompt = kwargs.get("user_prompt", "")
        return self.result


class RaisingLLM:
    """模拟 LLM 调用抛错（超时/网络故障）。"""

    async def structured_output(self, **kwargs: Any) -> dict:
        raise RuntimeError("LLM unavailable")


def _enable_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    from clipwright.config import settings
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    monkeypatch.setattr(settings, "llm_flash_api_key", "sk-test-flash")


# ── _llm_match_bgm 直接调用 ──

@pytest.mark.asyncio
async def test_llm_match_bgm_returns_allocations(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM 返回槽位分配：风格 + 音量包络 + 停顿设计。"""
    _enable_llm(monkeypatch)
    agent = AudioAgent()
    fake = FakeLLM(GOOD_RESULT)
    agent._llm = fake  # type: ignore[assignment]

    out = await agent._llm_match_bgm(SCENES_EMOTIONS, BGM_SLOTS)

    assert fake.called is True
    assert fake.system_prompt
    assert "情绪" in fake.system_prompt
    slots = {a["slot"] for a in out["allocations"]}
    assert slots == {"intro", "climax"}
    intro = next(a for a in out["allocations"] if a["slot"] == "intro")
    assert intro["style"] == "warm ambient piano"
    assert len(intro["volume_envelope"]) == 3
    assert intro["volume_envelope"][0] == {"t": 0.0, "v": 0.25}
    assert intro["volume_envelope"][2] == {"t": 1.0, "v": 0.2}
    assert intro["pause_design"] == {"pause_before_sec": 0.0, "pause_after_sec": 1.5}


@pytest.mark.asyncio
async def test_no_api_key_skips_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """未配置 API key → 不调用 LLM，直接返回 {}（规则回退）。"""
    from clipwright.config import settings
    monkeypatch.setattr(settings, "llm_api_key", "")
    monkeypatch.setattr(settings, "llm_flash_api_key", "")
    agent = AudioAgent()
    fake = FakeLLM(GOOD_RESULT)
    agent._llm = fake  # type: ignore[assignment]

    out = await agent._llm_match_bgm(SCENES_EMOTIONS, BGM_SLOTS)

    assert out == {}
    assert fake.called is False


@pytest.mark.asyncio
async def test_misleading_allocations_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    """非法/越界/未知槽位的 LLM 输出被清洗，仅保留合法项。"""
    _enable_llm(monkeypatch)
    agent = AudioAgent()
    bad = {
        "allocations": [
            {"slot": "not_a_real_slot", "style": "x"},          # 未知槽位 → 丢弃
            {"slot": "intro", "style": 123,                      # 非字符串 style
             "volume_envelope": [{"t": 2.0, "v": 9.9}],          # 单点且越界 → 丢弃
             "pause_design": {"pause_before_sec": -5}},          # 负停顿 → 丢弃
            {"slot": "backing", "style": "soft piano",
             "volume_envelope": [{"t": 0.0, "v": 0.3}, {"t": 1.0, "v": 0.5}],
             "pause_design": {"pause_before_sec": 0.2}},
            {"slot": "backing"},                                  # 重复槽位 → 丢弃
        ]
    }
    fake = FakeLLM(bad)
    agent._llm = fake  # type: ignore[assignment]

    out = await agent._llm_match_bgm(SCENES_EMOTIONS, BGM_SLOTS)

    assert [a["slot"] for a in out["allocations"]] == ["backing"]
    a = out["allocations"][0]
    assert a["style"] == "soft piano"
    assert a["volume_envelope"] == [{"t": 0.0, "v": 0.3}, {"t": 1.0, "v": 0.5}]
    assert a["pause_design"] == {"pause_before_sec": 0.2}


# ── execute 注入 ──

@pytest.mark.asyncio
async def test_allocations_injected_into_bgm_clips(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM 分配注入 BGM clip：bgm_slot/bgm_style/volume_envelope/pause_design。"""
    _enable_llm(monkeypatch)
    agent = AudioAgent()
    agent._llm = FakeLLM(GOOD_RESULT)  # type: ignore[assignment]
    inp = _audio_input(_bgm_timeline())

    out = await agent.execute(inp, _ctx())

    assert out.decision == AgentDecision.PASS
    clips = {c.id: c for c in out.timeline.tracks[0].clips}
    # bgm1（0s → intro）命中 intro 分配
    assert clips["bgm1"].metadata["bgm_slot"] == "intro"
    assert clips["bgm1"].metadata["bgm_style"] == "warm ambient piano"
    assert clips["bgm1"].metadata["volume_envelope"][-1] == {"t": 1.0, "v": 0.2}
    assert clips["bgm1"].metadata["pause_design"]["pause_after_sec"] == 1.5
    # bgm2（40s/100s → climax）命中 climax 分配
    assert clips["bgm2"].metadata["bgm_slot"] == "climax"
    assert clips["bgm2"].metadata["bgm_style"] == "tense electronic build"
    assert clips["bgm2"].metadata["volume_envelope"][0] == {"t": 0.0, "v": 0.5}
    # bgm3（80s → outro）无分配 → 规则回退，无增强注入
    assert clips["bgm3"].metadata["bgm_slot"] == "outro"
    assert "bgm_style" not in clips["bgm3"].metadata
    assert "volume_envelope" not in clips["bgm3"].metadata
    assert "pause_design" not in clips["bgm3"].metadata
    # 既有行为不回归
    assert clips["bgm1"].metadata["bpm"] == 120
    # B6: 淡入淡出改用 audio_fade 真实曲线，不再硬压音量 0.3
    assert clips["bgm1"].volume == 1.0
    assert clips["bgm1"].audio_fade_in_sec == 1.0
    assert clips["bgm1"].audio_fade_out_sec == 2.0
    assert any("LLM BGM 情绪匹配" in n for n in out.audio_notes)


# ── 回退路径（LLM 失败 / 不可用）──

@pytest.mark.asyncio
async def test_llm_failure_falls_back_to_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM 抛错 → 规则回退，BGM 布局与无 LLM 时完全一致。"""
    _enable_llm(monkeypatch)
    agent = AudioAgent()
    agent._llm = RaisingLLM()  # type: ignore[assignment]
    inp = _audio_input(_bgm_timeline())

    out = await agent.execute(inp, _ctx())

    assert out.decision == AgentDecision.PASS
    clips = {c.id: c for c in out.timeline.tracks[0].clips}
    assert clips["bgm1"].metadata["bgm_slot"] == "intro"
    assert clips["bgm2"].metadata["bgm_slot"] == "climax"
    assert clips["bgm3"].metadata["bgm_slot"] == "outro"
    for c in clips.values():
        assert c.metadata["bpm"] == 120
        assert "bgm_style" not in c.metadata
        assert "volume_envelope" not in c.metadata
        assert "pause_design" not in c.metadata
    assert not any("LLM BGM 情绪匹配" in n for n in out.audio_notes)


@pytest.mark.asyncio
async def test_garbage_llm_output_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM 返回非 allocations 结构（如 JSON 解析失败产物）→ 规则回退。"""
    _enable_llm(monkeypatch)
    agent = AudioAgent()
    agent._llm = FakeLLM({"content": "not json at all"})  # type: ignore[assignment]
    inp = _audio_input(_bgm_timeline())

    out = await agent.execute(inp, _ctx())

    assert out.decision == AgentDecision.PASS
    clips = {c.id: c for c in out.timeline.tracks[0].clips}
    assert clips["bgm1"].metadata["bgm_slot"] == "intro"
    assert "bgm_style" not in clips["bgm1"].metadata


@pytest.mark.asyncio
async def test_no_key_skips_llm_in_execute(monkeypatch: pytest.MonkeyPatch) -> None:
    """无 API key 时 execute 不调用 LLM，规则行为不变（含默认 0.7 音量）。"""
    from clipwright.config import settings
    monkeypatch.setattr(settings, "llm_api_key", "")
    monkeypatch.setattr(settings, "llm_flash_api_key", "")
    agent = AudioAgent()
    fake = FakeLLM(GOOD_RESULT)
    agent._llm = fake  # type: ignore[assignment]
    inp = _audio_input(_bgm_timeline())

    out = await agent.execute(inp, _ctx())

    assert out.decision == AgentDecision.PASS
    clips = {c.id: c for c in out.timeline.tracks[0].clips}
    assert clips["bgm2"].metadata["bgm_slot"] == "climax"
    assert clips["bgm2"].volume == 1.0  # 非首 clip 音量不被 LLM 路径改动
    assert "bgm_style" not in clips["bgm2"].metadata
    assert fake.called is False


# ── 规则分界回归（15% / 40% / 70% 不变）──

def test_rule_fallback_unchanged() -> None:
    """`_match_bgm_slot` 规则分界与历史一致（无 LLM 时行为不变）。"""
    agent = AudioAgent()
    assert agent._match_bgm_slot(0.0, 100.0, BGM_SLOTS) == "intro"
    assert agent._match_bgm_slot(20.0, 100.0, BGM_SLOTS) == "backing"
    assert agent._match_bgm_slot(40.0, 100.0, BGM_SLOTS) == "climax"
    assert agent._match_bgm_slot(80.0, 100.0, BGM_SLOTS) == "outro"
    assert agent._match_bgm_slot(10.0, 100.0, {}) == "default"
