"""类型规范契约单测（PRD Part I F1–F5 / F14，A-02）。"""

from __future__ import annotations

import pytest

from clipwright.schema.category_spec import (
    MAX_PROMPT_CHARS_TOTAL,
    ParamRule,
    PlanSchema,
    PromptBundle,
    PromptSlot,
    SignatureTrait,
    TypeDeclares,
    WAIVER_MARKER,
    find_dangling_numbers,
    get_path,
    set_path,
    validate_bundle,
    validate_param_rules,
    validate_traits_against_genre,
)


def _bundle(**slots: str) -> PromptBundle:
    return PromptBundle(slots={k: PromptSlot(text=v, priority=50) for k, v in slots.items()})


class TestSlots:
    def test_global_is_alias_of_genre(self) -> None:
        bundle = _bundle(global_="")
        bundle = PromptBundle(slots={"global": PromptSlot(text="这是评测片")})
        assert bundle.normalized().slots.keys() == {"genre"}
        assert bundle.text_of("genre") == "这是评测片"

    def test_empty_slots_are_dropped(self) -> None:
        bundle = _bundle(genre="x", structure="   ")
        assert set(bundle.normalized().slots) == {"genre"}

    def test_identity_text_order_is_fixed(self) -> None:
        bundle = PromptBundle(
            slots={
                "lexicon": PromptSlot(text="术语"),
                "anti_patterns": PromptSlot(text="不是段子"),
                "genre": PromptSlot(text="论证驱动"),
            }
        )
        assert bundle.identity_text().splitlines() == ["论证驱动", "不是段子", "术语"]

    def test_effective_text_truncates_by_sentence(self) -> None:
        slot = PromptSlot(text="第一句。第二句很长很长。第三句。", max_chars=12)
        assert slot.effective_text().endswith("。")
        assert len(slot.effective_text()) <= 12

    def test_effective_text_hard_cut_without_punctuation(self) -> None:
        slot = PromptSlot(text="abcdefghijklmnop", max_chars=5)
        assert slot.effective_text() == "abcde"


class TestDanglingNumbers:
    def test_number_without_rule_is_flagged(self) -> None:
        issues = find_dangling_numbers(_bundle(edit="禁止 800ms 内快切"), [])
        assert [i.code for i in issues] == ["dangling_number"]

    def test_number_with_rule_mention_passes(self) -> None:
        rules = [ParamRule(key="min_shot_ms", mode="floor", value=1200, reason="看清参数页")]
        issues = find_dangling_numbers(_bundle(edit="最短镜头按 min_shot_ms 规则"), rules)
        assert issues == []

    def test_number_with_rule_value_mention_passes(self) -> None:
        rules = [ParamRule(key="min_shot_ms", mode="floor", value=1200)]
        assert find_dangling_numbers(_bundle(edit="镜头不短于 1200 毫秒"), rules) == []

    def test_waiver_marker_exempts_slot(self) -> None:
        text = f"约 3 秒的呼吸感 {WAIVER_MARKER}"
        assert find_dangling_numbers(_bundle(edit=text), []) == []

    def test_text_without_numbers_is_clean(self) -> None:
        assert find_dangling_numbers(_bundle(edit="以硬切为主"), []) == []


class TestParamRules:
    def test_impossible_range_is_error(self) -> None:
        rules = [
            ParamRule(key="min_shot_ms", mode="floor", value=3000),
            ParamRule(key="min_shot_ms", mode="ceiling", value=1000),
        ]
        assert [i.code for i in validate_param_rules(rules)] == ["impossible_range"]

    def test_forbid_and_require_conflict(self) -> None:
        rules = [
            ParamRule(key="cut_on_beat", mode="forbid", value=True),
            ParamRule(key="cut_on_beat", mode="require", value=True),
        ]
        assert "forbid_require_conflict" in [i.code for i in validate_param_rules(rules)]

    def test_blank_key_rejected(self) -> None:
        with pytest.raises(Exception):
            ParamRule(key="   ", mode="floor", value=1)


class TestTraits:
    def test_slow_genre_with_short_shots_is_contradiction(self) -> None:
        prompts = _bundle(genre="缓慢长镜、不急于推进")
        traits = [SignatureTrait(name="avg_shot_ms", mode="require_le", value=800)]
        issues = validate_traits_against_genre(prompts, traits)
        assert [i.code for i in issues] == ["trait_genre_contradiction"]

    def test_consistent_genre_and_traits_pass(self) -> None:
        prompts = _bundle(genre="快速踩点、密集切换")
        traits = [SignatureTrait(name="avg_shot_ms", mode="require_le", value=800)]
        assert validate_traits_against_genre(prompts, traits) == []

    def test_require_between_needs_upper(self) -> None:
        traits = [SignatureTrait(name="chart_count", mode="require_between", value=1)]
        assert "trait_missing_upper" in [
            i.code for i in validate_traits_against_genre(_bundle(genre="x"), traits)
        ]

    def test_check_respects_mode(self) -> None:
        assert SignatureTrait(name="a", mode="require_ge", value=2).check(3)
        assert not SignatureTrait(name="a", mode="require_ge", value=2).check(1)
        assert SignatureTrait(name="a", mode="require_le", value=2).check(2)
        between = SignatureTrait(name="a", mode="require_between", value=1, value_max=3)
        assert between.check(2)
        assert not between.check(4)


class TestValidateBundle:
    def test_genre_required(self) -> None:
        issues = validate_bundle(_bundle(structure="骨架"))
        assert "genre_required" in [i.code for i in issues]

    def test_unknown_slot(self) -> None:
        bundle = PromptBundle(slots={"genre": PromptSlot(text="x"), "nope": PromptSlot(text="y")})
        assert "unknown_slot" in [i.code for i in validate_bundle(bundle)]

    def test_total_length_limit(self) -> None:
        bundle = _bundle(genre="x" * MAX_PROMPT_CHARS_TOTAL, structure="y" * 10)
        assert "prompt_too_long" in [i.code for i in validate_bundle(bundle)]

    def test_clean_bundle_passes(self) -> None:
        rules = [ParamRule(key="min_shot_ms", mode="floor", value=1200)]
        bundle = _bundle(genre="参数说话的评测片", edit="最短镜头按 min_shot_ms")
        assert validate_bundle(bundle, rules) == []


class TestPaths:
    def test_get_and_set_nested(self) -> None:
        data: dict = {}
        set_path(data, "transition_weights.hard_cut", 0.8)
        assert get_path(data, "transition_weights.hard_cut") == 0.8
        assert get_path(data, "transition_weights.missing") is None

    def test_type_declares_defaults(self) -> None:
        declares = TypeDeclares()
        assert declares.plan_schema is None
        assert declares.neighbors == []

    def test_plan_schema_defaults(self) -> None:
        schema = PlanSchema()
        assert schema.required == ["sections", "material_strategy"]
        assert schema.custom_allowed is True
