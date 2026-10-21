"""四个内置类型的规范单测（Part I：内置类型也必须"声明得出来、验得了"）。

第 29 轮发现四个内置类型 prompts/param_rules/traits 全空，`load_spec()` 只能从
旧钩子合成两个槽位——机制的运行时其实是空转的。本文件锁住"补完之后是好的"：
规范自洽、键在白名单、指标名是采集器真会算的、旧钩子内容不丢。
"""

from __future__ import annotations

import pytest

from clipwright.category.digital_review import DigitalReviewPlugin
from clipwright.category.kichiku_fastcut import KichikuFastcutPlugin
from clipwright.category.knowledge_longform import KnowledgeLongformPlugin
from clipwright.category.vlog_daily import VlogDailyPlugin
from clipwright.schema.category_spec import rule_key_allowed, validate_bundle
from clipwright.services.trait_collector import KNOWN_TRAITS

PLUGINS = [
    KnowledgeLongformPlugin(),
    KichikuFastcutPlugin(),
    DigitalReviewPlugin(),
    VlogDailyPlugin(),
]
IDS = [plugin.plugin_id for plugin in PLUGINS]


@pytest.mark.parametrize("plugin", PLUGINS, ids=IDS)
class TestBuiltinSpecs:
    def test_bundle_validates(self, plugin) -> None:
        issues = validate_bundle(
            plugin.get_prompt_bundle(), plugin.get_param_rules(), plugin.get_signature_traits()
        )
        assert issues == [], [i.model_dump() for i in issues]

    def test_identity_and_core_stages_declared(self, plugin) -> None:
        slots = set(plugin.get_prompt_bundle().normalized().slots)
        assert {"genre", "anti_patterns", "lexicon"} <= slots
        assert {"structure", "edit", "quality"} <= slots

    def test_genre_is_specific_not_generic(self, plugin) -> None:
        genre = plugin.get_prompt_bundle().text_of("genre")
        assert len(genre) >= 20
        # 每个类型的身份描述都要说清"这是什么片"，而不是复述 display_name
        assert plugin.display_name not in genre or len(genre) > 30

    def test_prompt_budget(self, plugin) -> None:
        assert plugin.get_prompt_bundle().total_chars() <= 4000

    def test_param_rule_keys_whitelisted(self, plugin) -> None:
        rules = plugin.get_param_rules()
        assert rules, "内置类型至少要声明一条参数规则"
        assert all(rule_key_allowed(rule.key) for rule in rules)

    def test_traits_use_measured_names(self, plugin) -> None:
        traits = plugin.get_signature_traits()
        assert traits, "内置类型至少要声明一条可测指标"
        for trait in traits:
            assert trait.name in KNOWN_TRAITS, f"{trait.name} 不是采集器会算的指标"
            assert trait.reason, "指标必须写清为什么"

    def test_legacy_hooks_still_work(self, plugin) -> None:
        """新契约不推翻旧钩子：结构模板/标注模板仍是原来那份能力。"""
        assert plugin.get_structure_template()
        assert plugin.get_annotation_templates()


class TestLoadSpec:
    @pytest.mark.parametrize("plugin", PLUGINS, ids=IDS)
    def test_load_spec_sees_declared_spec(self, plugin) -> None:
        from clipwright.category.registry import CategoryRegistry
        from clipwright.services.type_spec_runtime import load_spec

        added = CategoryRegistry.get(plugin.plugin_id) is None
        if added:
            CategoryRegistry.register(plugin)
        try:
            spec = load_spec(plugin.plugin_id)
            assert spec.prompts.normalized().slots, "load_spec 应能取到槽位"
            assert spec.traits, "load_spec 应能取到指标"
            assert spec.rules, "load_spec 应能取到参数规则"
        finally:
            if added:
                CategoryRegistry.unregister(plugin.plugin_id)


class TestTypesAreDistinguishable:
    """四个类型的身份描述不能互相串味（最简单的"假规范"检查）。"""

    def test_genre_texts_are_distinct(self) -> None:
        genres = [plugin.get_prompt_bundle().text_of("genre") for plugin in PLUGINS]
        assert len(set(genres)) == len(genres)

    def test_each_type_has_its_own_anti_patterns(self) -> None:
        anti = [plugin.get_prompt_bundle().text_of("anti_patterns") for plugin in PLUGINS]
        assert all(anti)
        assert len(set(anti)) == len(anti)
