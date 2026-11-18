"""Engine prompts 单一来源回归：不允许再回落到 config/局部常量。"""
from __future__ import annotations

from pathlib import Path

import yaml

from clipwright.prompts.engine.mg import (
    MG_CRITIQUE_CHECK_ITEMS,
    MG_DESIGNER_SYSTEM,
    MG_VISUAL_RUBRIC,
)
from clipwright.prompts.engine.pv import PV_ENGINE_ORCHESTRATOR_SYSTEM
from clipwright.prompts.engine.text import KINETIC_TYPOGRAPHY_SYSTEM

ROOT = Path(__file__).resolve().parents[2]


def test_engine_prompts_are_nonempty() -> None:
    assert len(MG_DESIGNER_SYSTEM) > 2000
    assert len(MG_CRITIQUE_CHECK_ITEMS) > 100
    assert len(MG_VISUAL_RUBRIC) > 200
    assert len(KINETIC_TYPOGRAPHY_SYSTEM) > 30
    assert len(PV_ENGINE_ORCHESTRATOR_SYSTEM) > 30


def test_mg_config_no_longer_duplicates_system_prompt() -> None:
    cfg = yaml.safe_load(
        (ROOT / "clipwright" / "animation" / "mg" / "config.yaml").read_text(encoding="utf-8")
    )
    assert "system_template" not in cfg["prompt"]


def test_generator_uses_engine_constants() -> None:
    from clipwright.animation.mg import generator

    assert generator._CRITIQUE_CHECK_ITEMS == MG_CRITIQUE_CHECK_ITEMS
    assert generator.MG_DESIGNER_SYSTEM == MG_DESIGNER_SYSTEM


def test_visual_eval_uses_engine_rubric() -> None:
    from clipwright.animation.mg import visual_eval

    assert visual_eval._RUBRIC == MG_VISUAL_RUBRIC
