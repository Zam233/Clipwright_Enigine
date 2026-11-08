"""A-08：类型声明（declares）取代 Agent 硬编码集合的单测。

覆盖四处迁移：
- ``semantic_qa_default``（quality_agent）
- ``required_animation_markers``（structure_agent 图表规则）
- ``outro_animation`` / ``opening_animation``（structure_agent）
- ``search_hint``（material_agent）

原则：**声明优先，未声明时回落旧硬编码**（D4 的一个周期兜底）。
"""

from __future__ import annotations

import pytest

from clipwright.category.base import BaseCategoryPlugin
from clipwright.category.registry import CategoryRegistry
from clipwright.services.type_spec_runtime import (
    declare_flag,
    required_markers,
    search_hint,
)


class _SpecPlugin(BaseCategoryPlugin):
    plugin_id = "declared_demo"
    display_name = "Declared"

    def __init__(self, **declares) -> None:  # type: ignore[no-untyped-def]
        from clipwright.schema.category_spec import TypeDeclares

        self._declares = TypeDeclares(**declares)

    def translate_persona(self, params):  # type: ignore[no-untyped-def]
        return {}

    def get_shot_params(self, translated):  # type: ignore[no-untyped-def]
        return {}

    def get_declares(self):  # type: ignore[no-untyped-def]
        return self._declares


@pytest.fixture(autouse=True)
def _isolate():
    CategoryRegistry.clear()
    yield
    CategoryRegistry.clear()


class TestDeclareFlag:
    def test_declaration_wins_over_legacy(self) -> None:
        CategoryRegistry.register(_SpecPlugin(semantic_qa_default=False))
        assert declare_flag("declared_demo", "semantic_qa_default", True) is False

    def test_missing_declaration_falls_back(self) -> None:
        CategoryRegistry.register(_SpecPlugin())
        assert declare_flag("declared_demo", "semantic_qa_default", True) is True
        assert declare_flag("declared_demo", "outro_animation", False) is False

    def test_unknown_type_falls_back(self) -> None:
        assert declare_flag("nope", "opening_animation", True) is True

    def test_explicit_true_declaration(self) -> None:
        CategoryRegistry.register(_SpecPlugin(outro_animation=True))
        assert declare_flag("declared_demo", "outro_animation", False) is True


class TestMarkers:
    def test_required_markers_listed(self) -> None:
        CategoryRegistry.register(_SpecPlugin(required_animation_markers=["data_chart"]))
        assert required_markers("declared_demo") == ["data_chart"]

    def test_no_declaration_is_empty(self) -> None:
        CategoryRegistry.register(_SpecPlugin())
        assert required_markers("declared_demo") == []


class TestSearchHint:
    def test_declaration_wins(self) -> None:
        CategoryRegistry.register(_SpecPlugin(search_hint="优先搜产品特写"))
        assert search_hint("declared_demo", "旧字典值") == "优先搜产品特写"

    def test_legacy_used_when_missing(self) -> None:
        CategoryRegistry.register(_SpecPlugin())
        assert search_hint("declared_demo", "旧字典值") == "旧字典值"


class TestAgentsUseDeclares:
    """真实 Agent 侧的最小验证（不跑管线，只调纯规则入口）。"""

    def test_quality_semantic_default_follows_declares(self) -> None:
        CategoryRegistry.register(_SpecPlugin(semantic_qa_default=True))
        assert declare_flag("declared_demo", "semantic_qa_default", False) is True

    def test_structure_chart_rule_respects_declaration(self) -> None:
        from clipwright.agents.structure_agent import StructureAgent

        CategoryRegistry.register(_SpecPlugin(required_animation_markers=["data_chart"]))
        scenes = [
            {"title": "开场", "description": "口播"},
            {"title": "实测", "description": "续航数据 提升 下降"},
        ]
        injected = StructureAgent._ensure_chart_coverage(scenes, "declared_demo")
        assert injected == 1
        assert "mg_dynamic" in scenes[1]["description"]

    def test_structure_chart_rule_off_without_declaration(self) -> None:
        from clipwright.agents.structure_agent import StructureAgent

        CategoryRegistry.register(_SpecPlugin())
        scenes = [{"title": "a", "description": "x"}, {"title": "b", "description": "数据 对比"}]
        assert StructureAgent._ensure_chart_coverage(scenes, "declared_demo") == -1

    def test_legacy_set_still_works(self) -> None:
        """旧集合未迁完前必须保持原行为（零回归）。"""
        from clipwright.agents.structure_agent import StructureAgent

        scenes = [{"title": "a", "description": "x"}, {"title": "b", "description": "跑分 对比"}]
        assert StructureAgent._ensure_chart_coverage(scenes, "digital_review") == 1

    def test_outro_declaration(self) -> None:
        from clipwright.agents.structure_agent import StructureAgent

        CategoryRegistry.register(_SpecPlugin(outro_animation=True))
        scenes = [{"title": "a", "description": "x"}, {"title": "总结", "description": "收束"}]
        assert StructureAgent._ensure_outro_animation(scenes, "declared_demo") == 1
        assert "[文字动画]" in scenes[-1]["description"]

    def test_outro_off_without_declaration(self) -> None:
        from clipwright.agents.structure_agent import StructureAgent

        CategoryRegistry.register(_SpecPlugin())
        scenes = [{"title": "a", "description": "x"}, {"title": "总结", "description": "收束"}]
        assert StructureAgent._ensure_outro_animation(scenes, "declared_demo") == -1

    def test_opening_strip_respects_declaration(self) -> None:
        from clipwright.agents.structure_agent import StructureAgent

        CategoryRegistry.register(_SpecPlugin(opening_animation=True))
        scenes = [{"title": "开场", "description": "[逻辑动画]mg_dynamic:{}"}]
        kept = StructureAgent._strip_opening_animation_markers(scenes, "declared_demo")
        assert "[逻辑动画]" in kept[0]["description"]

    def test_opening_stripped_by_default(self) -> None:
        from clipwright.agents.structure_agent import StructureAgent

        CategoryRegistry.register(_SpecPlugin())
        scenes = [{"title": "开场", "description": "[逻辑动画]mg_dynamic:{}"}]
        stripped = StructureAgent._strip_opening_animation_markers(scenes, "declared_demo")
        assert "[逻辑动画]" not in stripped[0]["description"]

    def test_material_hint_uses_declares(self) -> None:
        from clipwright.agents.material_agent import _category_search_hint

        CategoryRegistry.register(_SpecPlugin(search_hint="优先搜同类产品特写"))
        assert _category_search_hint("declared_demo") == "优先搜同类产品特写"

    def test_material_hint_legacy(self) -> None:
        from clipwright.agents.material_agent import _category_search_hint

        assert "数码评测" in _category_search_hint("digital_review")
