"""声明式配方解释器 `services/display_spec/recipe.py` 的行为契约（B3-T3.2/T3.3）。

两条纪律：

1. **只认 11 个算子**：没有 `eval`/`exec`/`getattr`，未知算子抛 `RecipeError`（插件的问题）；
2. **输出必须过清洗器**：配方产出仍要走契约清洗——所以"配方里写一个非法组件"只会得到占位文本，
   不会让非法节点进库。

`TestGenuiRecipes` 用**硬编码的期望结构**（不 import 旧模块）盯住插件配方的等价性：迁移前
`build_brief_genui` / `build_plan_genui` 的输出，现在必须由"配方 + 通用解释器"逐字节复现。
"""
from __future__ import annotations

import pytest

from clipwright.services.display_spec import normalize_contract
from clipwright.services.display_spec.recipe import (
    RecipeError,
    build_spec_from_recipe,
)
from plugins.genui import main as genui_plugin


@pytest.fixture(scope="module")
def brief_contract():
    contract, problems = normalize_contract(genui_plugin._spec_meta("brief"))
    assert contract is not None, problems
    return contract


@pytest.fixture(scope="module")
def plan_contract():
    contract, problems = normalize_contract(genui_plugin._spec_meta("plan"))
    assert contract is not None, problems
    return contract


def _one_node(recipe_node, view, contract):
    spec = build_spec_from_recipe({"title": "T", "nodes": [recipe_node]}, view, contract)
    assert spec is not None
    return spec["items"]


class TestOperators:
    def test_literal(self, brief_contract):
        items = _one_node({"type": "text", "content": "固定文本"}, {}, brief_contract)
        assert items[0]["content"] == "固定文本"

    def test_field(self, brief_contract):
        items = _one_node({"type": "text", "content": {"field": "title"}}, {"title": "T"}, brief_contract)
        assert items[0]["content"] == "T"

    def test_field_missing_is_empty_and_skipped(self, brief_contract):
        # 缺值 + skip_if_empty → 节点不产出
        spec = build_spec_from_recipe(
            {"nodes": [{"type": "text", "content": {"field": "nope"}, "skip_if_empty": "content"}]},
            {}, brief_contract,
        )
        assert spec is None

    def test_field_with_limit(self, brief_contract):
        items = _one_node(
            {"type": "list", "items": {"field": "key_elements", "limit": 2}},
            {"key_elements": ["a", "b", "c"]},
            brief_contract,
        )
        assert items[0]["items"] == ["a", "b"]

    def test_foreach_with_index_plus_one_and_item(self, brief_contract):
        items = _one_node(
            {
                "type": "table",
                "columns": ["#", "名"],
                "rows": {
                    "foreach": {"field": "scenes", "item": "s", "index": "i"},
                    "row": [{"index_plus_one": "i"}, {"item": "s.title"}],
                },
            },
            {"scenes": [{"title": "A"}, {"title": "B"}]},
            brief_contract,
        )
        assert items[0]["rows"] == [["1", "A"], ["2", "B"]]

    def test_join(self, brief_contract):
        items = _one_node(
            {"type": "callout", "content": {"join": {"field": "tags", "sep": "、", "limit": 2}}},
            {"tags": ["a", "b", "c", 3]},
            brief_contract,
        )
        assert items[0]["content"] == "a、b"

    def test_pairs(self, brief_contract):
        items = _one_node(
            {
                "type": "table",
                "columns": ["维度", "内容"],
                "rows": {"pairs": [{"label": "受众", "field": "audience"}, {"label": "空", "field": "nope"}]},
            },
            {"audience": "年轻人"},
            brief_contract,
        )
        assert items[0]["rows"] == [["受众", "年轻人"]]

    def test_count(self, brief_contract):
        items = _one_node(
            {"type": "stat", "label": "场景数", "value": {"count": "scenes"}},
            {"scenes": [1, 2, 3]},
            brief_contract,
        )
        assert items[0]["value"] == "3"

    def test_sum_of(self, brief_contract):
        items = _one_node(
            {"type": "stat", "label": "总时长", "value": {"sum_of": {"field": "scenes", "value": "d"}}},
            {"scenes": [{"d": 5}, {"d": "7"}, {"d": "x"}]},
            brief_contract,
        )
        assert items[0]["value"] == "12.0"

    def test_fmt_sec(self, brief_contract):
        items = _one_node(
            {"type": "stat", "label": "时长", "value": {"fmt_sec": {"field": "d"}}},
            {"d": 12.4},
            brief_contract,
        )
        assert items[0]["value"] == "12s"

    def test_fmt_sec_invalid(self, brief_contract):
        items = _one_node(
            {"type": "stat", "label": "时长", "value": {"fmt_sec": {"field": "d"}}},
            {"d": "abc"},
            brief_contract,
        )
        assert items[0]["value"] == "0s"

    def test_coalesce_treats_zero_as_present(self, brief_contract):
        items = _one_node(
            {"type": "stat", "label": "x", "value": {"coalesce": [{"field": "a"}, {"field": "b"}]}},
            {"a": 0, "b": 5},
            brief_contract,
        )
        assert items[0]["value"] == "0"

    def test_nested_foreach(self, brief_contract):
        items = _one_node(
            {
                "type": "table",
                "columns": ["组", "项"],
                "rows": {
                    "foreach": {"field": "groups", "item": "g", "index": "i"},
                    "row": [{"item": "g.name"}, {"join": {"item": "g.items", "sep": "、"}}],
                },
            },
            {"groups": [{"name": "A", "items": ["x", "y"]}, {"name": "B", "items": ["z"]}]},
            brief_contract,
        )
        assert items[0]["rows"] == [["A", "x、y"], ["B", "z"]]

    def test_when_hides_node(self, brief_contract):
        spec = build_spec_from_recipe(
            {
                "nodes": [
                    {"type": "text", "content": "标题", "when": {"field": "items", "non_empty": True}},
                ]
            },
            {"items": []},
            brief_contract,
        )
        assert spec is None

    def test_unknown_operator_raises(self, brief_contract):
        with pytest.raises(RecipeError):
            build_spec_from_recipe(
                {"nodes": [{"type": "text", "content": {"evil_eval": "1+1"}}]},
                {}, brief_contract,
            )

    def test_illegal_component_is_sanitized(self, brief_contract):
        """配方不是免检通道：未知组件只会得到占位文本（邻接的合法节点保留）。"""
        spec = build_spec_from_recipe(
            {"nodes": [
                {"type": "mermaid", "content": "graph TD"},
                {"type": "text", "content": "兄弟节点"},
            ]},
            {}, brief_contract,
        )
        assert spec["items"][0]["type"] == "text"
        assert "mermaid" in spec["items"][0]["content"]
        assert spec["items"][1]["content"] == "兄弟节点"


BRIEF_SAMPLE = {
    "title": "T", "overview": "O",
    "target_audience": "年轻人", "core_message": "C",
    "style_direction": "S", "structure_suggestion": "ST",
    "duration_estimate": "90s",
    "key_elements": ["a", "b"], "special_requirements": ["x", "y"],
}

BRIEF_EXPECTED = {
    "version": 1,
    "title": "创意简报",
    "items": [
        {"type": "text", "content": "T", "size": "h2"},
        {"type": "text", "content": "O", "size": "body"},
        {"type": "table", "columns": ["维度", "内容"], "rows": [
            ["目标受众", "年轻人"], ["核心信息", "C"], ["风格方向", "S"],
            ["结构建议", "ST"], ["预估时长", "90s"],
        ]},
        {"type": "text", "content": "必须出现的关键元素", "size": "h3"},
        {"type": "list", "items": ["a", "b"]},
        {"type": "callout", "tone": "warning", "title": "特殊要求与红线", "content": "x；y"},
    ],
}

PLAN_SAMPLE = {
    "scene_count": 2, "total_duration_sec": 12, "material_summary": "MS",
    "raw_scenes": [
        {"title": "A", "duration_sec": 5, "keywords": ["k1", "k2"]},
        {"title": "B", "duration_sec": 7, "keywords": ["k3"]},
    ],
}

PLAN_EXPECTED = {
    "version": 1,
    "title": "制作规划书",
    "items": [
        {"type": "row", "items": [
            {"type": "stat", "label": "场景数", "value": "2"},
            {"type": "stat", "label": "总时长", "value": "12s"},
        ]},
        {"type": "table", "columns": ["#", "场景", "时长", "关键词"], "rows": [
            ["1", "A", "5s", "k1、k2"], ["2", "B", "7s", "k3"],
        ]},
        {"type": "callout", "tone": "info", "content": "MS"},
    ],
}


class TestGenuiRecipes:
    """插件配方必须逐字节复现迁移前两个构造函数���输出（等价性口径写在这里）。"""

    def test_brief_recipe_matches_legacy_output(self, brief_contract):
        spec = build_spec_from_recipe(
            genui_plugin.RECIPE["brief"], BRIEF_SAMPLE, brief_contract,
            title=genui_plugin.RECIPE["brief"]["title"],
        )
        assert spec == BRIEF_EXPECTED

    def test_plan_recipe_matches_legacy_output(self, plan_contract):
        spec = build_spec_from_recipe(
            genui_plugin.RECIPE["plan"], PLAN_SAMPLE, plan_contract,
            title=genui_plugin.RECIPE["plan"]["title"],
        )
        assert spec == PLAN_EXPECTED

    def test_recipe_has_expected_node_counts(self):
        assert len(genui_plugin.RECIPE["brief"]["nodes"]) == 6
        assert len(genui_plugin.RECIPE["plan"]["nodes"]) == 3

    def test_non_dict_scene_row_is_counted_by_recipe(self, brief_contract, plan_contract):
        """已知差异（计划 T3.3 要求显式记录）：旧实现按 dict 行计 scene_count，recipe 按 list 长度。

        这里固定住 recipe 的口径：非 dict 项也计入 `count`，但不会产出场景行（`item` 取不到值 → 空串）。
        """
        view = {
            "raw_scenes": [{"title": "A", "duration_sec": 5}, "not-a-dict"],
            "material_summary": "",
        }
        spec = build_spec_from_recipe(genui_plugin.RECIPE["plan"], view, plan_contract)
        stats = spec["items"][0]["items"]
        assert stats[0]["value"] == "2"  # count 计入非 dict 项
        rows = spec["items"][1]["rows"]
        assert rows[0][1] == "A"
        assert rows[1][1] == ""  # 非 dict 项产出一行空值，而不是被过滤掉
