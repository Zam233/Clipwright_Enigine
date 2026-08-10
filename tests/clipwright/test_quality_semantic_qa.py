"""QualityAgent LLM 语义质检测试（计划 C1，enable_semantic_qa 门控）。

覆盖：
- ``enable_semantic_qa=True`` + LLM 返回问题 → issues 含 category="semantic"
  的问题，severity 正确映射，LLM 以 use_flash=True 调用
- ``enable_semantic_qa=True`` + LLM 抛异常 / 返回非 JSON → 无新增问题、无异常
- ``enable_semantic_qa=False`` → 与基线完全一致（LLM 不被调用）
- 非法 severity 被丢弃
"""
from __future__ import annotations

from typing import Any

import pytest

from clipwright.agents.quality_agent import QualityAgent
from clipwright.schema.agent import AgentContext, QualityInput
from clipwright.schema.timeline import Clip, ClipKind, Timeline, Track


def _context() -> AgentContext:
    return AgentContext(
        pipeline_id="cq-semantic-test",
        persona_id="p_test",
        category_plugin_id="test",
        topic="测试选题",
    )


def _timeline() -> Timeline:
    """无违规基线时间线：2 个带文案视频片段 + 1 个音频片段。"""
    vid_clips = [
        Clip(
            id="v1", kind=ClipKind.VIDEO, asset_id="a1", track_id="t1",
            start_sec=0, duration_sec=12,
            metadata={"source_title": "人工智能如何改变生产力"},
        ),
        Clip(
            id="v2", kind=ClipKind.VIDEO, asset_id="a2", track_id="t1",
            start_sec=12, duration_sec=10,
            metadata={"source_title": "企业数字化转型实践"},
        ),
    ]
    audio_clips = [
        Clip(
            id="au1", kind=ClipKind.AUDIO, asset_id="a3", track_id="t2",
            start_sec=0, duration_sec=22, volume=0.8,
        ),
    ]
    return Timeline(
        id="tl-test",
        duration_sec=22,
        tracks=[
            Track(id="t1", name="视频轨", kind=ClipKind.VIDEO, index=0, clips=vid_clips),
            Track(id="t2", name="音频轨", kind=ClipKind.AUDIO, index=1, clips=audio_clips),
        ],
    )


def _input(constraints: dict[str, Any] | None = None) -> QualityInput:
    return QualityInput(
        context=_context(),
        timeline=_timeline(),
        constraints=constraints or {},
        creative_brief={
            "overview": "讲清楚 AI 对生产力的影响",
            "special_requirements": ["语气积极", "避免专业术语"],
        },
    )


class _StubLLM:
    """可控 structured_output：记录调用，返回预设结果或抛错。"""

    def __init__(self, result: Any = None, fail: bool = False) -> None:
        self.result = result
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    async def structured_output(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("LLM down")
        return self.result if self.result is not None else {}


def _patch_llm(monkeypatch: Any, fake: _StubLLM) -> None:
    from clipwright.services.llm import LLMService

    monkeypatch.setattr(LLMService, "structured_output", fake.structured_output)


# ── 成功路径：LLM 返回问题 → semantic issue 注入 ──────────

@pytest.mark.asyncio
async def test_semantic_qa_enabled_maps_llm_issue(monkeypatch: Any) -> None:
    """enable_semantic_qa=True + LLM 返回 1 条问题 → issues 含 semantic 问题。"""
    fake = _StubLLM(result={"issues": [
        {"severity": "warning", "category": "semantic", "message": "文案与简报不一致"},
    ]})
    _patch_llm(monkeypatch, fake)

    out = await QualityAgent().execute(_input({"enable_semantic_qa": True}), _context())

    semantic = [i for i in out.issues if i.category == "semantic"]
    assert len(semantic) == 1
    assert semantic[0].severity == "warning"
    assert semantic[0].message == "文案与简报不一致"
    assert semantic[0].location == "v1"  # 首个关键片段 ID

    # LLM 以 flash 模型调用，prompt 携带简报 + 视频文案
    assert len(fake.calls) == 1
    assert fake.calls[0]["use_flash"] is True
    user_prompt = fake.calls[0]["user_prompt"]
    assert "AI 对生产力的影响" in user_prompt
    assert "人工智能如何改变生产力" in user_prompt


# ── 失败路径：LLM 异常 / 非 JSON → 静默跳过 ───────────────

@pytest.mark.asyncio
async def test_semantic_qa_llm_exception_silent(monkeypatch: Any) -> None:
    """LLM 抛异常 → 无新增问题、无异常抛出。"""
    fake = _StubLLM(fail=True)
    _patch_llm(monkeypatch, fake)

    out = await QualityAgent().execute(_input({"enable_semantic_qa": True}), _context())

    assert all(i.category != "semantic" for i in out.issues)
    assert fake.calls, "LLM 应被调用"


@pytest.mark.asyncio
async def test_semantic_qa_llm_non_json_silent(monkeypatch: Any) -> None:
    """LLM 返回非 JSON（structured_output fallback 形态）→ 无新增问题、无异常。"""
    fake = _StubLLM(result={"content": "这不是 JSON 的内容"})
    _patch_llm(monkeypatch, fake)

    out = await QualityAgent().execute(_input({"enable_semantic_qa": True}), _context())

    assert all(i.category != "semantic" for i in out.issues)


@pytest.mark.asyncio
async def test_semantic_qa_no_issues_key_silent(monkeypatch: Any) -> None:
    """LLM 返回空 dict / 无 issues 键 → 静默跳过。"""
    fake = _StubLLM(result={})
    _patch_llm(monkeypatch, fake)

    out = await QualityAgent().execute(_input({"enable_semantic_qa": True}), _context())

    assert all(i.category != "semantic" for i in out.issues)


# ── 门控关闭：与基线完全一致，LLM 不被调用 ────────────────

@pytest.mark.asyncio
async def test_semantic_qa_disabled_identical_to_baseline(monkeypatch: Any) -> None:
    """enable_semantic_qa=False / 未配置 → 与无 LLM 基线输出完全一致。"""
    # 基线：不带约束
    fake_base = _StubLLM(result={"issues": [{"severity": "error", "category": "semantic", "message": "x"}]})
    _patch_llm(monkeypatch, fake_base)
    out_base = await QualityAgent().execute(_input(), _context())

    # 关闭门控
    fake_off = _StubLLM(result={"issues": [{"severity": "error", "category": "semantic", "message": "x"}]})
    _patch_llm(monkeypatch, fake_off)
    out_off = await QualityAgent().execute(_input({"enable_semantic_qa": False}), _context())

    assert fake_off.calls == [], "门控关闭时不得调用 LLM"
    assert out_off.decision == out_base.decision
    assert out_off.issues == out_base.issues
    assert all(i.category != "semantic" for i in out_off.issues)


# ── severity 过滤 ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_semantic_qa_invalid_severity_dropped(monkeypatch: Any) -> None:
    """LLM 返回非法 severity → 该问题被丢弃；合法项保留。"""
    fake = _StubLLM(result={"issues": [
        {"severity": "error", "category": "semantic", "message": "合法错误"},
        {"severity": "FATAL", "category": "semantic", "message": "非法级别"},
        {"severity": "", "category": "semantic", "message": "空级别"},
        {"severity": "info", "category": "semantic", "message": "合法提示"},
        {"message": "缺 severity"},
        "not-a-dict",
    ]})
    _patch_llm(monkeypatch, fake)

    out = await QualityAgent().execute(_input({"enable_semantic_qa": True}), _context())

    semantic = [i for i in out.issues if i.category == "semantic"]
    assert len(semantic) == 2
    assert {i.severity for i in semantic} == {"error", "info"}


# ── 无关键片段文案 → 直接返回，不调用 LLM ────────────────

@pytest.mark.asyncio
async def test_semantic_qa_no_clip_copy_skips(monkeypatch: Any) -> None:
    """时间线无可用文案 → 不调用 LLM、无新增问题。"""
    tl = _timeline()
    for track in tl.tracks:
        for clip in track.clips:
            clip.metadata = {}
    fake = _StubLLM(result={"issues": []})
    _patch_llm(monkeypatch, fake)

    inp = QualityInput(
        context=_context(),
        timeline=tl,
        constraints={"enable_semantic_qa": True},
        creative_brief={"overview": "测试"},
    )
    out = await QualityAgent().execute(inp, _context())

    assert fake.calls == [], "无文案时不得调用 LLM"
    assert all(i.category != "semantic" for i in out.issues)
