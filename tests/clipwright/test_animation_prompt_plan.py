"""PRD: animation prompt skill modules / plan tests."""
from __future__ import annotations

from clipwright.animation.prompting import (
    build_animation_plan,
    compose_mg_system_prompt,
    rule_by_id,
    validate_animation_plan,
    build_timeline_plan,
)


def test_plan_selects_category_and_two_three_rules() -> None:
    plan = build_animation_plan("近三年营收增长柱状图，数据对比")
    assert plan.category in ("charts", "comparison")
    assert 2 <= len(plan.rule_ids) <= 3
    assert plan.global_current == "left"
    assert plan.transition_budget == 2


def test_plan_energy_high() -> None:
    plan = build_animation_plan("快节奏冲击标题", {})
    assert plan.energy == "high"


def test_rules_are_short_and_have_skill_source() -> None:
    for rid in ("headline-slam", "count-up", "carrier-title", "hold-beat"):
        rule = rule_by_id(rid)
        assert rule is not None
        assert rule.skill
        assert len(rule.snippet) < 200


def test_modular_prompt_is_compact_and_contains_gates() -> None:
    plan = build_animation_plan("数据增长对比")
    prompt = compose_mg_system_prompt(plan)
    assert len(prompt) < 6000
    assert "全片运动契约" in prompt
    assert "headline-slam" in prompt or "data-bars" in prompt
    assert "动画编排约束" in prompt
    assert "输出硬性要求" in prompt
    assert prompt.index("动画编排约束") < prompt.index("输出硬性要求")


def test_validate_plan_rejects_bad_budget_and_rules() -> None:
    plan = build_animation_plan("标题")
    assert validate_animation_plan(plan) == []
    bad = plan.as_dict()
    bad["transition_budget"] = 9
    bad["rule_ids"] = ["nope"]
    errors = validate_animation_plan(bad)
    assert any("transition_budget" in e for e in errors)
    assert any("2-3" in e for e in errors)


def test_generator_prompt_uses_compact_modules() -> None:
    import asyncio
    from clipwright.animation.mg.generator import MGGenerator

    class _Resp:
        content = "{}"

    class _LLM:
        def __init__(self):
            self.calls = []

        async def generate(self, messages, **kwargs):
            self.calls.append(messages)
            return _Resp()

    gen = MGGenerator()
    fake = _LLM()
    gen._llm = fake
    asyncio.run(gen._call_llm("数据增长对比标题", "A|B", {}, {}, {}))
    system = fake.calls[0][0]["content"]
    assert len(system) < 6000
    assert "全片运动契约" in system
    assert "动画编排约束" in system


def test_multi_scene_plan_has_shots_and_seams() -> None:
    plan = build_timeline_plan([
        {"clip_id": "c1", "description": "标题开场", "start_sec": 0},
        {"clip_id": "c2", "description": "数据增长对比", "start_sec": 4},
    ])
    assert len(plan.shots) == 2
    assert len(plan.seams) == 1
    assert plan.shots[0]["clip_id"] == "c1"
    assert plan.seams[0]["vector"] == "left"


def test_legacy_prompt_fallback_still_available() -> None:
    from clipwright.animation.mg.generator import MGGenerator

    gen = MGGenerator()
    old = gen._design_system_template("标题", {})
    assert len(old) < 6000
    gen._config["prompt"]["modular"] = False
    legacy = gen._design_system_template("标题", {})
    assert len(legacy) > 10000
    assert "Motion Design Principles" in legacy
