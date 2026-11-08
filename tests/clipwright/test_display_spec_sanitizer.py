"""契约驱动清洗器 `services/display_spec/sanitizer.py` 的行为契约（B2-T2.3/T2.5）。

守两件事：
1. **不信任模型输出**（12 例从 `test_genui_spec.py::TestSanitize` 迁来，语义不变）；
2. **一切由契约决定**（4 例新增：无 `html` 组件 → 降级；`max_nodes=2` → 第 3 个丢；
   `actions=["ask"]` → `confirm` 丢；同一份 raw 在两个契约下结果不同）。

第 2 点是"engine 里没有 genui 专属逻辑"的直接证据：同一份输入换个契约就换个结果。
"""
from __future__ import annotations

import dataclasses
import json

import pytest

from clipwright.services.display_spec import normalize_contract, sanitize_display_spec
from plugins.genui import main as genui_plugin


@pytest.fixture(scope="module")
def brief_contract():
    contract, problems = normalize_contract(genui_plugin._spec_meta("brief"))
    assert contract is not None, problems
    return contract


def _clean(raw, contract):
    return sanitize_display_spec(raw, contract)


class TestSanitizeBasics:
    def test_valid_spec_passes_through(self, brief_contract):
        spec, problems = _clean(
            {
                "version": 1,
                "title": "简报",
                "items": [
                    {"type": "text", "content": "核心目标"},
                    {"type": "table", "columns": ["维度", "内容"], "rows": [["受众", "年轻人"]]},
                ],
            },
            brief_contract,
        )
        assert spec is not None and problems == []
        assert [n["type"] for n in spec["items"]] == ["text", "table"]
        assert spec["title"] == "简报"

    def test_missing_version_defaults_to_contract_version(self, brief_contract):
        spec, _ = _clean({"items": [{"type": "text", "content": "x"}]}, brief_contract)
        assert spec is not None
        assert spec["version"] == brief_contract.version

    def test_unknown_version_rejects_whole_spec(self, brief_contract):
        spec, problems = _clean({"version": 99, "items": [{"type": "text", "content": "x"}]}, brief_contract)
        assert spec is None
        assert any("版本" in p for p in problems)

    def test_unknown_component_degrades_to_placeholder(self, brief_contract):
        spec, _ = _clean(
            {"items": [{"type": "hero", "title": "x"}, {"type": "text", "content": "兄弟节点"}]},
            brief_contract,
        )
        assert spec is not None
        first = spec["items"][0]
        assert first["type"] == "text"
        assert "hero" in first["content"]
        assert spec["items"][1]["content"] == "兄弟节点"

    def test_forbidden_keys_removed(self, brief_contract):
        raw = json.loads(
            '{"items":[{"type":"card","__proto__":{"polluted":true},'
            '"items":[{"type":"text","content":"in"}]}]}'
        )
        spec, _ = _clean(raw, brief_contract)
        assert "__proto__" not in json.dumps(spec, ensure_ascii=False)

    def test_action_whitelist(self, brief_contract):
        spec, problems = _clean(
            {
                "items": [
                    {"type": "text", "content": "坏", "action": {"type": "fetch", "url": "https://evil"}},
                    {"type": "text", "content": "好", "action": {"type": "ask", "payload": {"prompt": "继续"}}},
                ]
            },
            brief_contract,
        )
        assert spec["items"][0].get("action") is None
        assert spec["items"][1]["action"]["type"] == "ask"
        assert any("fetch" in p for p in problems)

    def test_text_truncated(self, brief_contract):
        limit = brief_contract.limit("max_text_chars")
        spec, _ = _clean({"items": [{"type": "text", "content": "字" * (limit + 100)}]}, brief_contract)
        content = spec["items"][0]["content"]
        assert content.endswith("…（已截断）")
        assert len(content) == limit + len("…（已截断）")

    def test_table_columns_capped(self, brief_contract):
        cap = brief_contract.limit("max_table_cols")
        columns = [f"c{i}" for i in range(cap + 5)]
        spec, problems = _clean(
            {"items": [{"type": "table", "columns": columns, "rows": [[*columns]]}]}, brief_contract
        )
        assert len(spec["items"][0]["columns"]) == cap
        assert any("列数" in p for p in problems)

    def test_deep_nesting_dropped(self, brief_contract):
        depth = brief_contract.limit("max_depth")
        node: dict = {"type": "text", "content": "deep"}
        for _ in range(depth + 2):
            node = {"type": "row", "items": [node]}
        spec, problems = _clean({"items": [node]}, brief_contract)
        # 外层 row 还在，但超深的那层被丢弃
        assert any("嵌套" in p for p in problems)

    def test_node_budget(self, brief_contract):
        budget = brief_contract.limit("max_nodes")
        items = [{"type": "text", "content": f"n{i}"} for i in range(budget + 5)]
        spec, problems = _clean({"items": items}, brief_contract)
        assert len(spec["items"]) == budget
        assert any("节点数" in p for p in problems)

    def test_oversized_spec_dropped(self, brief_contract):
        limit = brief_contract.limit("max_text_chars")
        filler = "x" * limit
        items = [{"type": "text", "content": filler} for _ in range(40)]
        spec, problems = _clean({"items": items}, brief_contract)
        assert spec is None
        assert any("字节" in p for p in problems)

    def test_non_object_input(self, brief_contract):
        assert _clean(None, brief_contract) == (None, [])
        assert _clean("", brief_contract) == (None, [])
        assert _clean({"items": []}, brief_contract)[0] is None


class TestContractDrivesBehaviour:
    """同一份 raw、不同契约 → 不同结果（证明没有硬编码的 genui 知识）。"""

    def test_component_not_in_contract_degrades(self, brief_contract):
        narrow = dataclasses.replace(
            brief_contract,
            components=tuple(c for c in brief_contract.components if c != "table"),
            containers=tuple(c for c in brief_contract.containers if c != "card"),
        )
        spec, _ = _clean(
            {"items": [{"type": "table", "columns": ["a"], "rows": [["1"]]}]}, narrow
        )
        assert spec["items"][0]["type"] == "text"
        assert "table" in spec["items"][0]["content"]

    def test_node_limit_comes_from_contract(self, brief_contract):
        tight = dataclasses.replace(
            brief_contract, limits={**brief_contract.limits, "max_nodes": 2}
        )
        items = [{"type": "text", "content": f"n{i}"} for i in range(4)]
        spec, _ = _clean({"items": items}, tight)
        assert len(spec["items"]) == 2
        # 原契约下 4 个都保留（对照）
        loose_spec, _ = _clean({"items": items}, brief_contract)
        assert len(loose_spec["items"]) == 4

    def test_action_kinds_come_from_contract(self, brief_contract):
        ask_only = dataclasses.replace(brief_contract, actions=("ask",))
        spec, problems = _clean(
            {"items": [{"type": "text", "content": "x", "action": {"type": "confirm"}}]},
            ask_only,
        )
        assert "action" not in spec["items"][0]
        assert any("confirm" in p for p in problems)

    def test_same_raw_different_contract(self, brief_contract):
        raw = {"items": [{"type": "table", "columns": ["a", "b"], "rows": [["1", "2"]]}]}
        one_col = dataclasses.replace(
            brief_contract, limits={**brief_contract.limits, "max_table_cols": 1}
        )
        assert len(_clean(raw, brief_contract)[0]["items"][0]["columns"]) == 2
        assert len(_clean(raw, one_col)[0]["items"][0]["columns"]) == 1


class TestSelectAction:
    """`select` 的载荷必须是非空字符串选项组（迁自旧文件，随清洗器一起保留）。"""

    def test_keeps_options_and_dedups(self, brief_contract):
        spec, _ = sanitize_display_spec(
            {"items": [{"type": "text", "content": "选一个",
                        "action": {"type": "select", "payload": {"options": ["A", "B", "A", " ", 3]}}}]},
            brief_contract,
        )
        assert spec["items"][0]["action"] == {"type": "select", "payload": {"options": ["A", "B"]}}

    def test_caps_options_and_clips_each(self, brief_contract):
        cap = brief_contract.limit("max_select_options")
        chars = brief_contract.limit("max_option_chars")
        long = "x" * (chars + 50)
        options = [long] + [f"opt{i}" for i in range(cap + 4)]
        spec, _ = sanitize_display_spec(
            {"items": [{"type": "text", "content": "选", "action": {"type": "select", "payload": {"options": options}}}]},
            brief_contract,
        )
        cleaned = spec["items"][0]["action"]["payload"]["options"]
        assert len(cleaned) == cap
        assert all(len(o) <= chars for o in cleaned)
        assert not any("已截断" in o for o in cleaned), "选项是纯截断，不带标记"

    def test_dropped_without_options(self, brief_contract):
        for payload in (None, {}, {"options": []}, {"options": ["", "  "]}, {"options": "A"}):
            action = {"type": "select"} if payload is None else {"type": "select", "payload": payload}
            spec, problems = sanitize_display_spec(
                {"items": [{"type": "text", "content": "选", "action": action}]}, brief_contract
            )
            assert "action" not in spec["items"][0]
            assert any("select" in p for p in problems)

    def test_ask_and_confirm_unaffected(self, brief_contract):
        for kind in ("ask", "confirm"):
            spec, _ = sanitize_display_spec(
                {"items": [{"type": "text", "content": "x",
                            "action": {"type": kind, "payload": {"prompt": "y"}}}]},
                brief_contract,
            )
            assert spec["items"][0]["action"]["type"] == kind
