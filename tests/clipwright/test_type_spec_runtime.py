"""类型规范运行期适配单测（PRD Part I F4 / F2、A-04 / A-05 / A-07）。

覆盖三件事：
1. 旧钩子归一：`list[dict]` 必须渲染成人类可读文本，而不是 Python repr（修 A1/A2）；
2. 规则收口：`resolve_shot_params` 让 strict 规则压过用户值；
3. 契约加固：`TypeDefinition` 未知字段 400、悬空数字被拒、`/resolve` 干跑可用。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from clipwright.category.base import BaseCategoryPlugin
from clipwright.category.dynamic import DynamicCategoryPlugin
from clipwright.category.registry import CategoryRegistry
from clipwright.services.type_spec_runtime import (
    bounds_of,
    clamp_profile,
    load_spec,
    resolve_params,
    resolve_shot_params,
    stage_text,
)

# ── 测试用类型 ──────────────────────────────────────────────────────────────


class _LegacyPlugin(BaseCategoryPlugin):
    """模拟第三方插件：旧钩子返回 list[dict]（A1/A2 的真实形态）。"""

    plugin_id = "legacy_demo"
    display_name = "Legacy"

    def translate_persona(self, params):  # type: ignore[no-untyped-def]
        return {"shot_params": {"min_shot_ms": 1000}}

    def get_shot_params(self, translated):  # type: ignore[no-untyped-def]
        return {"min_shot_ms": 1000}

    def get_structure_template(self):  # type: ignore[no-untyped-def]
        return [
            {"role": "headline", "weight": 1.0, "desc": "新闻标题"},
            {"role": "report", "weight": 3.0, "desc": "事件报道"},
        ]

    def get_annotation_templates(self):  # type: ignore[no-untyped-def]
        return [{"type": "lower_third", "position": "bottom-left", "style": "name_bar"}]

    def get_mg_style_guidance(self) -> str:
        return "正式、权威"


class _RuledPlugin(_LegacyPlugin):
    """带规则与指标的类型。"""

    plugin_id = "ruled_demo"

    def get_param_rules(self):  # type: ignore[no-untyped-def]
        from clipwright.schema.category_spec import ParamRule

        return [
            ParamRule(key="min_shot_ms", mode="floor", value=1200, enforce="strict", reason="看清"),
            ParamRule(key="max_shot_ms", mode="ceiling", value=5000, enforce="strict"),
        ]

    def get_signature_traits(self):  # type: ignore[no-untyped-def]
        from clipwright.schema.category_spec import SignatureTrait

        return [SignatureTrait(name="avg_shot_ms", mode="require_le", value=1500)]


@pytest.fixture(autouse=True)
def _isolate_registry():
    CategoryRegistry.clear()
    yield
    CategoryRegistry.clear()


class TestLegacyAdapter:
    """A-04：旧钩子 → 提示词包，`list[dict]` 不得出现 repr。"""

    def test_list_of_dicts_rendered_as_text(self) -> None:
        bundle = _LegacyPlugin().get_prompt_bundle()
        structure = bundle.text_of("structure")
        assert "新闻标题" in structure
        assert "权重 1.0" in structure
        assert "{" not in structure and "'role'" not in structure

    def test_annotation_dicts_are_rendered(self) -> None:
        bundle = _LegacyPlugin().get_prompt_bundle()
        assert "lower_third" in bundle.text_of("structure")
        assert "bottom-left" in bundle.text_of("structure")

    def test_mg_guidance_becomes_animation_slot(self) -> None:
        bundle = _LegacyPlugin().get_prompt_bundle()
        assert bundle.text_of("animation") == "正式、权威"

    def test_no_legacy_output_means_no_slots(self) -> None:
        class _Silent(_LegacyPlugin):
            plugin_id = "silent"

            def get_structure_template(self):  # type: ignore[no-untyped-def]
                return ""

            def get_annotation_templates(self):  # type: ignore[no-untyped-def]
                return []

            def get_mg_style_guidance(self) -> str:
                return ""

        assert _Silent().get_prompt_bundle().normalized().slots == {}


class TestRuntimeResolution:
    """A-05：规则在运行期收口。"""

    def test_strict_floor_clamps_shot_params(self) -> None:
        CategoryRegistry.register(_RuledPlugin())
        shot, resolution = resolve_shot_params(
            {"shot_params": {"min_shot_ms": 300}}, "ruled_demo"
        )
        assert shot["min_shot_ms"] == 1200
        assert resolution.has_conflict("C1")

    def test_no_rules_keeps_params_untouched(self) -> None:
        CategoryRegistry.register(_LegacyPlugin())
        shot, resolution = resolve_shot_params(
            {"shot_params": {"min_shot_ms": 300, "base_shot_ms": 800}}, "legacy_demo"
        )
        assert shot == {"min_shot_ms": 300, "base_shot_ms": 800}
        assert not resolution.conflicts

    def test_unknown_type_is_noop(self) -> None:
        assert resolve_shot_params({"shot_params": {"a": 1}}, "nope")[0] == {"a": 1}

    def test_resolve_params_applies_platform_floor(self) -> None:
        from clipwright.schema.category_spec import ParamRule

        CategoryRegistry.register(_LegacyPlugin())
        resolution = resolve_params(
            {"min_shot_ms": 100},
            "legacy_demo",
            platform_rules=[ParamRule(key="min_shot_ms", mode="floor", value=1000)],
        )
        assert resolution.param("min_shot_ms") == 1000
        assert resolution.source_of("min_shot_ms") == "L0_platform"

    def test_stage_text_contains_identity_and_stage(self) -> None:
        CategoryRegistry.register(_RuledPlugin())
        text = stage_text("ruled_demo", "structure")
        assert "新闻标题" in text

    def test_load_spec_reads_rules_and_traits(self) -> None:
        CategoryRegistry.register(_RuledPlugin())
        spec = load_spec("ruled_demo")
        assert len(spec.rules) == 2
        assert spec.traits[0].name == "avg_shot_ms"


class TestClampProfile:
    def test_clamps_into_bounds(self) -> None:
        assert clamp_profile({"base_shot_ms": 200}, lo=1200, hi=5000)["base_shot_ms"] == 1200
        assert clamp_profile({"base_shot_ms": 9000}, lo=1200, hi=5000)["base_shot_ms"] == 5000

    def test_leaves_inside_value(self) -> None:
        profile = {"base_shot_ms": 3000, "transition_weights": {"hard_cut": 1.0}}
        assert clamp_profile(profile, lo=1200, hi=5000) == profile

    def test_ignores_invalid_base(self) -> None:
        assert clamp_profile({"base_shot_ms": None}, lo=1, hi=2) == {"base_shot_ms": None}
        assert clamp_profile({"base_shot_ms": True}, lo=1, hi=2) == {"base_shot_ms": True}

    def test_bounds_of(self) -> None:
        assert bounds_of({"min_shot_ms": 1, "max_shot_ms": 2}) == (1, 2)
        assert bounds_of(None) == (None, None)


class TestDynamicPluginSpec:
    """A-07：YAML 里的规范能被动态类型加载。"""

    def _config(self) -> dict:
        return {
            "id": "my_mv",
            "name": "我的 MV",
            "shot_params": {"min_shot_sec": 1.0, "max_shot_sec": 5.0},
            "prompts": {
                "genre": {"text": "整片是一场对话", "priority": 100},
                "edit": "硬切为主",
            },
            "param_rules": [
                {"key": "min_shot_ms", "mode": "floor", "value": 1200, "enforce": "strict"}
            ],
            "signature_traits": [
                {"name": "avg_shot_ms", "mode": "require_ge", "value": 2000}
            ],
            "declares": {"neighbors": ["knowledge_longform"], "requires": ["scene_script"]},
        }

    def test_reads_prompts_rules_traits_declares(self) -> None:
        plugin = DynamicCategoryPlugin(self._config())
        bundle = plugin.get_prompt_bundle()
        assert bundle.text_of("genre") == "整片是一场对话"
        assert bundle.text_of("edit") == "硬切为主"
        assert plugin.get_param_rules()[0].value == 1200
        assert plugin.get_signature_traits()[0].name == "avg_shot_ms"
        assert plugin.get_declares().neighbors == ["knowledge_longform"]
        assert plugin.get_declares().requires == ["scene_script"]

    def test_invalid_rules_are_skipped_not_crashing(self) -> None:
        config = self._config()
        config["param_rules"] = [{"key": "", "mode": "floor"}]
        plugin = DynamicCategoryPlugin(config)
        assert plugin.get_param_rules() == []

    def test_empty_config_falls_back_to_legacy_adapter(self) -> None:
        plugin = DynamicCategoryPlugin({"id": "plain"})
        assert plugin.get_prompt_bundle().normalized().slots == {}


class TestTypeDefinitionContract:
    """A-06：未知字段 400、悬空数字被拒、resolve 干跑。"""

    def _payload(self, **overrides) -> dict:
        base = {
            "id": "demo",
            "name": "Demo",
            "shot_params": {
                "min_shot_sec": 1.0,
                "max_shot_sec": 5.0,
                "transition_type": "cut",
                "transition_duration_sec": 0.5,
                "cut_on_beat": False,
            },
            "prompts": {"genre": {"text": "评测片"}},
        }
        base.update(overrides)
        return base

    def test_unknown_field_rejected(self) -> None:
        from clipwright.api.type_maker import TypeDefinition

        with pytest.raises(ValidationError):
            TypeDefinition(**self._payload(bogus=1))

    def test_unknown_slot_rejected(self) -> None:
        from clipwright.api.type_maker import TypeDefinition

        with pytest.raises(ValidationError):
            TypeDefinition(**self._payload(prompts={"nope": {"text": "x"}}))

    def test_global_alias_accepted(self) -> None:
        from clipwright.api.type_maker import TypeDefinition

        definition = TypeDefinition(**self._payload(prompts={"global": {"text": "x"}}))
        assert definition.prompt_bundle.text_of("genre") == "x"

    def test_dangling_number_rejected_by_validate(self) -> None:
        from clipwright.api.type_maker import spec_issues

        definition = _make_definition(prompts={"genre": {"text": "禁止 800ms 内快切"}})
        codes = [issue.code for issue in spec_issues(definition)]
        assert "dangling_number" in codes

    def test_rule_makes_number_executable(self) -> None:
        from clipwright.api.type_maker import spec_issues

        definition = _make_definition(
            prompts={"genre": {"text": "最短镜头按 min_shot_ms 规则"}},
            param_rules=[{"key": "min_shot_ms", "mode": "floor", "value": 1200}],
        )
        assert spec_issues(definition) == []


def _make_definition(**overrides):
    from clipwright.api.type_maker import TypeDefinition

    base = {
        "id": "demo",
        "name": "Demo",
        "shot_params": {
            "min_shot_sec": 1.0,
            "max_shot_sec": 5.0,
            "transition_type": "cut",
            "transition_duration_sec": 0.5,
            "cut_on_beat": False,
        },
    }
    base.update(overrides)
    return TypeDefinition(**base)
