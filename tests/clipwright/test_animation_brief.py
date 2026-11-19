from clipwright.animation.brief import STYLE_LIBRARY, build_brief, style_prompt_block, validate_brief
from clipwright.animation.prompting import build_animation_plan, compose_mg_system_prompt


def test_style_library_has_eight_structured_styles() -> None:
    assert len(STYLE_LIBRARY) >= 8
    for style in STYLE_LIBRARY.values():
        assert style.signals
        assert len(style.palette) >= 2
        assert style.easing


def test_brief_is_valid_and_prompted() -> None:
    brief = build_brief("数据增长对比", {"display_name": "知识区"}, 5.0)
    assert validate_brief(brief) == []
    block = style_prompt_block(brief)
    assert "swiss-editorial" in block or "data-story" in block
    assert "forbidden" in block


def test_plan_carries_brief_and_beats() -> None:
    plan = build_animation_plan("数据增长 120%", {"display_name": "知识区"}, 5.0)
    assert plan.brief
    assert plan.beats
    assert "Brief / 风格帧" in compose_mg_system_prompt(plan)
