"""③ 动效指标进 critique：单测（客观门禁 + 强制修复 + 不误伤强动效）。"""

from __future__ import annotations

import pytest

from clipwright.animation.mg import type_generation as TG
from clipwright.animation.mg.generator import MGGenerator


def _weak_mg() -> dict:
    """弱动效：位移 6px（0.3% 画布）、关键帧只覆盖入场 17%。"""
    return {
        "duration_sec": 6.0,
        "elements": [
            {"type": "text", "content": "{a}", "keyframes": [
                {"time": 0, "opacity": 0, "translate_y": 6},
                {"time": 1, "opacity": 1, "translate_y": 0},
                {"time": 6, "opacity": 1, "translate_y": 0}]},
            {"type": "text", "content": "{b}", "keyframes": [
                {"time": 0, "opacity": 0}, {"time": 1, "opacity": 1},
                {"time": 6, "opacity": 1}]},
        ],
    }


def _strong_mg() -> dict:
    return {
        "duration_sec": 6.0,
        "elements": [
            {"type": "text", "content": "{a}", "keyframes": [
                {"time": 0, "opacity": 0, "translate_x": -140},
                {"time": 1, "opacity": 1, "translate_x": 0},
                {"time": 5.6, "translate_x": 40},
                {"time": 6, "translate_x": 40}]},
            {"type": "shape", "keyframes": [
                {"time": 0, "opacity": 0, "scale": 0.4},
                {"time": 1, "opacity": 1, "scale": 1.15},
                {"time": 5.6, "scale": 1.0}, {"time": 6, "scale": 1.0}]},
        ],
    }


def test_objective_findings_flags_weak_motion():
    rep = TG.objective_motion_findings(_weak_mg(), 6.0, width=1920)
    assert rep["ok"] is False
    assert rep["score"] <= TG.MOTION_FAIL_SCORE
    joined = " ".join(rep["issues"])
    assert "位移过小" in joined
    assert "入场阶段" in joined
    assert any("画布宽" in s for s in rep["suggestions"])


def test_objective_findings_passes_strong_motion():
    rep = TG.objective_motion_findings(_strong_mg(), 6.0, width=1920)
    assert rep["ok"] is True
    assert rep["score"] == 100
    assert rep["issues"] == []


def test_thresholds_configurable(monkeypatch):
    from clipwright.config import settings

    monkeypatch.setattr(settings, "animation_motion_min_shift_ratio", 0.5, raising=False)
    rep = TG.objective_motion_findings(_strong_mg(), 6.0, width=1920)
    assert rep["ok"] is False, "阈值调高后强动效也不达标（可配置性验证）"


class _FakeGen(MGGenerator):
    """绕过 LLMService 初始化，只测门禁与修复编排。"""

    def __init__(self):  # noqa: D107
        self._config = {"critique": {"min_score": 60, "enabled": True}}
        self.repair_calls: list[dict] = []

    async def _critique_quality(self, *a, **k):  # noqa: D102
        return {"score": 95, "issues": [], "suggestions": []}  # LLM 认为很好

    async def _visual_eval_stage(self, *a, **k):  # noqa: D102
        return None

    async def _call_llm_critique_repair(self, mg_def, critique, *a, **k):  # noqa: D102
        self.repair_calls.append({"critique": critique, "mg_def": mg_def})
        return _strong_mg()

    async def _build_success(self, mg_def, method, **k):  # noqa: D102
        return {"success": True, "mg_def": mg_def, "method": method, "html": "<div/>"}


@pytest.mark.asyncio
async def test_weak_motion_forces_repair_even_when_llm_score_high():
    gen = _FakeGen()
    out = await gen._finalize_with_critique(
        _weak_mg(), "llm", "desc", "text", {}, {}, {},
        motion_gate=True,
    )
    assert out["method"] == "critique_repair"
    assert gen.repair_calls, "应触发一次定向修复"
    crit = gen.repair_calls[0]["critique"]
    assert crit["score"] <= 60
    assert any("位移过小" in i for i in crit["issues"])
    assert out["mg_def"] is not None


@pytest.mark.asyncio
async def test_strong_motion_is_not_repaired():
    gen = _FakeGen()
    out = await gen._finalize_with_critique(
        _strong_mg(), "llm", "desc", "text", {}, {}, {},
        motion_gate=True,
    )
    assert out["method"] == "llm"
    assert not gen.repair_calls, "达标片段不应触发修复（避免额外成本）"


@pytest.mark.asyncio
async def test_gate_works_even_when_critique_disabled():
    class _NoCritique(_FakeGen):
        async def _critique_quality(self, *a, **k):  # noqa: D102
            return None

    gen = _NoCritique()
    out = await gen._finalize_with_critique(
        _weak_mg(), "llm", "desc", "text", {}, {}, {},
        motion_gate=True,
    )
    assert gen.repair_calls, "critique 关闭时客观门禁仍应生效"
    assert out["method"] == "critique_repair"


@pytest.mark.asyncio
async def test_strong_motion_accepted_when_critique_disabled():
    class _NoCritique(_FakeGen):
        async def _critique_quality(self, *a, **k):  # noqa: D102
            return None

    gen = _NoCritique()
    out = await gen._finalize_with_critique(
        _strong_mg(), "llm", "desc", "text", {}, {}, {},
        motion_gate=True,
    )
    assert out["method"] == "llm"
    assert not gen.repair_calls
