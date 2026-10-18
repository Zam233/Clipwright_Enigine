"""提示词优先级裁决器单测（PRD Part I F2 / V2 / V5 / V18，A-03）。"""

from __future__ import annotations

from clipwright.schema.category_spec import ParamRule, PromptBundle, PromptSlot, SignatureTrait
from clipwright.services.prompt_authority import PromptAuthority


def _bundle(**slots: str) -> PromptBundle:
    return PromptBundle(slots={k: PromptSlot(text=v, priority=50) for k, v in slots.items()})


def _authority(**kwargs) -> PromptAuthority:
    kwargs.setdefault("prompts", _bundle(genre="参数说话的评测片"))
    return PromptAuthority(**kwargs)


class TestRuleEnforcement:
    def test_strict_floor_overrides_user_value(self) -> None:
        """V2：用户传 300，被 strict floor 顶回 1200，并产生 C1。"""
        authority = _authority(
            rules=[ParamRule(key="min_shot_ms", mode="floor", value=1200, enforce="strict")]
        )
        resolution = authority.resolve(user={"min_shot_ms": 300})
        assert resolution.param("min_shot_ms") == 1200
        assert resolution.source_of("min_shot_ms") == "L2_prompt"
        assert resolution.has_conflict("C1")
        conflict = resolution.conflicts[0]
        assert conflict.user_value == 300
        assert conflict.winner_layer == "L2_prompt"

    def test_strict_floor_keeps_integer_type_and_clean_message(self) -> None:
        """真机取证回归：floor 改写后仍是 int，冲突文案不出现「1200.0」。"""
        authority = _authority(
            rules=[ParamRule(key="min_shot_ms", mode="floor", value=1200, enforce="strict")]
        )
        resolution = authority.resolve(user={"min_shot_ms": 300})
        value = resolution.param("min_shot_ms")
        assert value == 1200
        assert isinstance(value, int) and not isinstance(value, bool)
        assert "1200.0" not in resolution.conflicts[0].message

    def test_soft_floor_keeps_user_value_and_records_c2(self) -> None:
        authority = _authority(
            rules=[ParamRule(key="min_shot_ms", mode="floor", value=1200, enforce="soft")]
        )
        resolution = authority.resolve(user={"min_shot_ms": 300})
        assert resolution.param("min_shot_ms") == 300
        assert resolution.has_conflict("C2")
        assert not resolution.has_conflict("C1")

    def test_soft_rule_fills_missing_value(self) -> None:
        authority = _authority(
            rules=[ParamRule(key="min_shot_ms", mode="floor", value=1200, enforce="soft")]
        )
        resolution = authority.resolve()
        assert resolution.param("min_shot_ms") == 1200
        assert not resolution.conflicts

    def test_strict_ceiling_still_wins(self) -> None:
        authority = _authority(
            rules=[ParamRule(key="max_shot_ms", mode="ceiling", value=4000, enforce="strict")]
        )
        assert authority.resolve(user={"max_shot_ms": 9000}).param("max_shot_ms") == 4000

    def test_nested_dotted_key(self) -> None:
        authority = _authority(
            rules=[
                ParamRule(
                    key="transition_weights.hard_cut",
                    mode="floor",
                    value=0.8,
                    enforce="strict",
                )
            ]
        )
        resolution = authority.resolve(user={"transition_weights": {"hard_cut": 0.2}})
        assert resolution.params["transition_weights"] == {"hard_cut": 0.8}

    def test_forbid_removes_value(self) -> None:
        authority = _authority(
            rules=[ParamRule(key="transition_weights.glitch", mode="forbid", value=1.0)]
        )
        resolution = authority.resolve(user={"transition_weights": {"glitch": 1.0}})
        assert "glitch" not in resolution.params["transition_weights"]

    def test_require_turns_flag_on(self) -> None:
        authority = _authority(
            rules=[ParamRule(key="cut_on_beat", mode="require", value=True)]
        )
        assert authority.resolve().param("cut_on_beat") is True


class TestLayers:
    def test_user_overrides_category_and_persona(self) -> None:
        authority = _authority()
        resolution = authority.resolve(
            defaults={"base_shot_ms": 5000},
            persona={"base_shot_ms": 3000},
            category_params={"base_shot_ms": 4000},
            user={"base_shot_ms": 2500},
        )
        assert resolution.param("base_shot_ms") == 2500
        assert resolution.source_of("base_shot_ms") == "L1_user"

    def test_platform_floor_beats_everything(self) -> None:
        authority = _authority(
            platform_rules=[
                ParamRule(key="min_shot_ms", mode="floor", value=1000, reason="平台底线")
            ]
        )
        resolution = authority.resolve(user={"min_shot_ms": 200})
        assert resolution.param("min_shot_ms") == 1000
        assert resolution.source_of("min_shot_ms") == "L0_platform"
        assert resolution.has_conflict("C6")

    def test_none_values_do_not_overwrite(self) -> None:
        resolution = _authority().resolve(defaults={"a": 1}, user={"a": None})
        assert resolution.param("a") == 1


class TestDeformation:
    def test_trait_unsatisfiable_by_params_is_deformation(self) -> None:
        """V18：鬼畜要求 avg_shot_ms ≤1500，却配 min_shot_ms=5000 → C9。"""
        authority = _authority(
            traits=[SignatureTrait(name="avg_shot_ms", mode="require_le", value=1500)]
        )
        resolution = authority.resolve(category_params={"min_shot_ms": 5000})
        assert resolution.deformation is not None
        assert resolution.deformation["code"] == "C9"
        assert "不提供豁免" in resolution.deformation["note"]

    def test_feasible_traits_have_no_deformation(self) -> None:
        authority = _authority(
            traits=[SignatureTrait(name="avg_shot_ms", mode="require_le", value=1500)]
        )
        resolution = authority.resolve(category_params={"min_shot_ms": 200, "max_shot_ms": 1200})
        assert resolution.deformation is None
        assert resolution.traits[0].feasible is True

    def test_min_greater_than_max_is_deformation(self) -> None:
        resolution = _authority().resolve(user={"min_shot_ms": 8000, "max_shot_ms": 3000})
        assert resolution.deformation is not None

    def test_unknown_trait_is_not_judged(self) -> None:
        authority = _authority(traits=[SignatureTrait(name="chart_count", mode="require_ge", value=1)])
        resolution = authority.resolve()
        assert resolution.traits[0].feasible is None
        assert resolution.deformation is None


class TestPrompts:
    def test_identity_and_stage_text(self) -> None:
        bundle = PromptBundle(
            slots={
                "genre": PromptSlot(text="整片是一场对话"),
                "anti_patterns": PromptSlot(text="不是 KTV 字幕"),
                "edit": PromptSlot(text="硬切为主"),
            }
        )
        authority = PromptAuthority(prompts=bundle, stage="edit")
        assert "整片是一场对话" in authority.stage_text()
        assert "不是 KTV 字幕" in authority.stage_text()
        assert "硬切为主" in authority.stage_text()

    def test_budget_drops_low_priority_slots(self) -> None:
        bundle = PromptBundle(
            slots={
                "genre": PromptSlot(text="g" * 700, priority=100),
                "edit": PromptSlot(text="e" * 700, priority=10),
            }
        )
        resolution = PromptAuthority(prompts=bundle).resolve()
        assert "genre" in resolution.prompts
        assert "edit" in resolution.dropped_slots

    def test_slots_carry_source_and_chars(self) -> None:
        resolution = PromptAuthority(
            prompts=_bundle(genre="x"), category_id="lyric_mv"
        ).resolve()
        slot = resolution.slots[0]
        assert slot["source"] == "category:lyric_mv"
        assert slot["chars"] == 1
        assert slot["truncated"] is False
