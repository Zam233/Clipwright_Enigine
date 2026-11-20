"""修复回归：动效先补后判 + keyframe x/y 归一，不再产生虚假 mg_motion_weak。"""

from __future__ import annotations

import pytest

from clipwright.animation.mg.generator import MGGenerator
from clipwright.animation.mg.type_generation import (
    enforce_motion_floor,
    ensure_motion_floor,
    objective_motion_findings,
)
from clipwright.animation.mg.validator import repair_mg_json
from clipwright.services.degradation import degradation_scope


def _weak_mg() -> dict:
    return {
        "animation_id": "t_weak",
        "duration_sec": 4.0,
        "width": 1920,
        "height": 1080,
        "elements": [
            {"id": "e1", "type": "text", "content": "左边", "x": 200, "y": 300,
             "font_size": 64,
             "keyframes": [{"time": 0, "opacity": 0}, {"time": 4, "opacity": 1}]},
            {"id": "e2", "type": "text", "content": "右边", "x": 1200, "y": 300,
             "font_size": 56,
             "keyframes": [{"time": 0, "opacity": 0}, {"time": 4, "opacity": 1}]},
        ],
        "style": {"background": "transparent"},
    }


def test_ensure_motion_floor_meets_shift_ratio():
    before = objective_motion_findings(_weak_mg(), 4.0, width=1920)
    assert not before["ok"]
    fixed = ensure_motion_floor(_weak_mg(), width=1920)
    after = objective_motion_findings(fixed, 4.0, width=1920)
    assert after["ok"], after
    assert after["motion"]["max_shift"] >= 1920 * 0.04


def test_enforce_motion_floor_reports_changed_and_ok():
    fixed, findings, changed = enforce_motion_floor(_weak_mg(), width=1920)
    assert changed is True
    assert findings is not None and findings["ok"]
    assert fixed is not _weak_mg()


def test_validator_normalizes_x_y_keyframes():
    mg = _weak_mg()
    mg["elements"][0]["keyframes"] = [
        {"time": 0, "x": 0, "y": 10},
        {"time": 1, "x": 120, "y": 30},
    ]
    repaired, fixes = repair_mg_json(mg)
    kfs = repaired["elements"][0]["keyframes"]
    assert any("x" in k for k in kfs) is False
    assert kfs[1].get("translate_x") == 120
    assert kfs[1].get("translate_y") == 30


@pytest.mark.asyncio
async def test_llm_weak_motion_is_floored_before_any_degradation(monkeypatch):
    """LLM 路径：1px 微动 → 先补到 5% → 不记录 mg_motion_weak。"""
    sink: list = []
    gen = MGGenerator()

    async def _critique_none(*_a, **_k):
        return None

    async def _success(*_a, **kw):
        return {"ok": True, "mg_def": kw.get("mg_def") or _a[0], "html": ""}

    monkeypatch.setattr(gen, "_critique_quality", _critique_none)
    monkeypatch.setattr(gen, "_build_success", _success)

    with degradation_scope(sink):
        out = await gen._finalize_with_critique(
            _weak_mg(), "llm", "测试", "左边|右边", {}, {"clip_id": "c_test", "title": "t"},
            {}, width=1920, height=1080,
        )
    assert out is not None
    kinds = [getattr(d, "kind", "") for d in sink]
    assert "mg_motion_weak" not in kinds, f"补运动后不应再记录弱动效：{kinds}"
    # 补完的 mg_def 必须带真实位移
    fixed = out["mg_def"]
    findings = objective_motion_findings(fixed, 4.0, width=1920)
    assert findings["ok"]
