"""③ 死键清理：接线与来源优先级测试。"""

from __future__ import annotations

import inspect

from clipwright.agents import edit_agent, material_agent, quality_agent


# ── orientation ────────────────────────────────────────────

def test_orientation_explicit_wins():
    assert material_agent.orientation_from_brief(
        {"asset_ratio": "9:16"}, {"orientation": "landscape"}) == "landscape"


def test_orientation_derived_from_asset_ratio():
    assert material_agent.orientation_from_brief({"asset_ratio": "9:16"}) == "portrait"
    assert material_agent.orientation_from_brief({"asset_ratio": "3:4"}) == "portrait"
    assert material_agent.orientation_from_brief({"asset_ratio": "16:9"}) == "landscape"


def test_orientation_defaults_to_landscape():
    assert material_agent.orientation_from_brief({}) == "landscape"
    assert material_agent.orientation_from_brief(None, {}) == "landscape"


# ── glossary ───────────────────────────────────────────────

def test_glossary_prefers_extra_then_brief():
    assert quality_agent.select_glossary({"glossary": ["a"]}, {"glossary": ["b", "c"]}) == ["b", "c"]
    assert quality_agent.select_glossary({"glossary": ["x", "  "]}, {}) == ["x"]


def test_glossary_empty_when_missing():
    assert quality_agent.select_glossary({}, {}) == []
    assert quality_agent.select_glossary(None, None) == []


# ── edit 侧三处兜底（源码契约，防兜底被删）─────────────────

def _edit_source() -> str:
    return inspect.getsource(edit_agent.EditAgent.execute)


def test_transition_weights_falls_back_to_shot_params():
    src = _edit_source()
    assert 'extra_params.get("transition_weights")' in src
    assert "shot_params" in src and "transition_weights" in src
    # 至少存在一段把两者串起来的表达式
    assert "or (shot_params or {}).get(\"transition_weights\")" in src


def test_enable_color_match_falls_back_to_settings():
    src = _edit_source()
    assert 'extra_params.get("enable_color_match")' in src
    assert "edit_enable_color_match" in src


def test_pv_llm_tools_falls_back_to_settings():
    src = _edit_source()
    assert 'extra_params.get("enable_pv_llm_tools")' in src
    assert "edit_enable_pv_llm_tools" in src


def test_lut_path_falls_back_to_brief():
    src = _edit_source()
    assert 'extra_params.get("lut_path")' in src
    assert "creative_brief" in src and "lut_path" in src


# ── settings 字段存在 ──────────────────────────────────────

def test_edit_switches_exist_in_settings():
    from clipwright.config import settings

    assert getattr(settings, "edit_enable_color_match", None) in (True, False)
    assert getattr(settings, "edit_enable_pv_llm_tools", None) in (True, False)


# ── 审计脚本 strict 通过 ───────────────────────────────────

def test_plumbing_audit_passes_strict():
    import json
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    r = subprocess.run([sys.executable, "-X", "utf8", "scripts/audit_stage_plumbing.py"],
                       capture_output=True, text=True, cwd=str(root))
    assert r.returncode == 0, r.stderr[-500:]
    rep = json.loads(r.stdout)
    assert rep["dead_entries"] == []
    assert rep["extra_params_read_without_writer"] == {}, (
        f"仍有未接线读取键: {rep['extra_params_read_without_writer']}")
