"""PRD: animation prompt skill modules / plan tests."""
from __future__ import annotations

from clipwright.animation.prompting import (
    build_animation_plan,
    compose_mg_system_prompt,
    rule_by_id,
    validate_animation_plan,
    validate_content_coverage,
    validate_placeholder_types,
    validate_layout_overlaps,
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
    assert len(system) < 6500
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


def test_critique_prompt_contains_continuity_checks() -> None:
    from clipwright.animation.mg.generator import MGGenerator

    class _Resp:
        content = '{"score": 90, "issues": [], "suggestions": []}'

    gen = MGGenerator()

    # 直接验证 critique template 的检查项，避免依赖完整生成链。
    text = gen._critique_system_template("标题", {})
    assert "连续性与运动检查" in text
    assert "idle" in text
    assert "carrier" in text


def test_content_coverage_catches_missing_step() -> None:
    mg = {
        "params": {"step1": {"default": "选题"}, "step2": {"default": "脚本"}, "step3": {"default": "剪辑"}},
        "elements": [{"type": "text", "content": "{step1}"}, {"type": "text", "content": "{step2}"}],
    }
    errors = validate_content_coverage(mg, "选题|脚本|剪辑")
    assert errors == ["内容段「剪辑」只在 params 中，未落到任何元素"]


def test_placeholder_type_catches_color_used_as_text() -> None:
    mg = {
        "params": {"accent": {"type": "color", "default": "快 3 倍"}},
        "elements": [{"type": "text", "content": "{accent}"}],
    }
    assert validate_placeholder_types(mg)


def test_layout_guard_catches_line_through_text() -> None:
    mg = {"elements": [
        {"type": "text", "y": 450, "font_size": 150, "content": "T"},
        {"type": "line", "y": 565, "width": 600, "content": ""},
    ]}
    assert validate_layout_overlaps(mg)


def test_layout_quote_unifies_font_size_and_centers() -> None:
    from clipwright.animation.mg.layout import normalize_mg_layout
    mg = {
        "width": 1920, "height": 1080, "shot_type": "quote",
        "params": {"a": {"default": "让每一帧"}, "b": {"default": "都有"}, "c": {"default": "信息量"}},
        "elements": [
            {"type": "text", "content": "{a}", "x": "center", "y": 326, "font_size": 56},
            {"type": "text", "content": "{b}", "x": "center", "y": 433, "font_size": 136},
            {"type": "text", "content": "{c}", "x": "center", "y": 644, "font_size": 72},
        ],
    }
    out, warnings = normalize_mg_layout(mg)
    sizes = {e["font_size"] for e in out["elements"]}
    assert len(sizes) == 1
    assert all(e["x"] == "center" for e in out["elements"])
    assert warnings


def test_layout_flow_distributes_nodes_and_arrows() -> None:
    from clipwright.animation.mg.layout import normalize_mg_layout
    mg = {
        "width": 1920, "height": 1080, "shot_type": "flow",
        "params": {"a": {"default": "选题"}, "b": {"default": "脚本"}, "c": {"default": "剪辑"}},
        "elements": [
            {"type": "text", "content": "{a}", "x": 344, "y": 520, "font_size": 56},
            {"type": "text", "content": "→", "x": 650, "y": 506, "font_size": 56},
            {"type": "text", "content": "{b}", "x": 904, "y": 520, "font_size": 56},
            {"type": "text", "content": "→", "x": 1200, "y": 506, "font_size": 56},
            {"type": "text", "content": "{c}", "x": 1464, "y": 520, "font_size": 56},
        ],
    }
    out, _ = normalize_mg_layout(mg)
    starts = [e["x"] for e in out["elements"] if e.get("content") in ("{a}", "{b}", "{c}")]
    assert starts == sorted(starts)
    assert starts[0] > 0 and starts[-1] < 1920


def test_layout_clamps_text_into_safe_area() -> None:
    from clipwright.animation.mg.layout import normalize_mg_layout
    mg = {"width": 1920, "height": 1080, "params": {}, "elements": [
        {"type": "text", "content": "这是一个非常非常非常非常非常非常非常非常长的标题", "x": -200, "y": -200, "font_size": 200},
    ]}
    out, _ = normalize_mg_layout(mg)
    e = out["elements"][0]
    assert e["x"] >= 1920 * 0.025
    assert e["y"] >= 1080 * 0.025
    assert e["font_size"] < 200
