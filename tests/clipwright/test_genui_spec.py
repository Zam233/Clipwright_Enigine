"""GenUI 展示 spec 的契约测试（`clipwright/schema/genui.py` + 插件一致性）。

这条测试守的是三件事：

1. **不信任模型输出**：未知组件降级成占位文本、危险键剔除、非法动作丢弃、超限截断或整块丢弃；
2. **权威数据不受影响**：spec 洗坏/丢弃时，brief 与 plan 的字段仍在（`CreativeBrief` /
   `ProductionPlan` 的校验器只动 `genui` 一个字段）；
3. **插件与契约不漂移**：`plugins/genui/main.py` 里的组件清单必须与
   `GENUI_COMPONENT_TYPES` 完全一致（插件是给模型看的说明，写错就等于教错）。
"""
from __future__ import annotations

import json

import pytest

from clipwright.schema.genui import (
    GENUI_COMPONENT_TYPES,
    GENUI_VERSION,
    MAX_DEPTH,
    MAX_NODES,
    MAX_SPEC_BYTES,
    MAX_TABLE_COLS,
    MAX_TEXT_CHARS,
    build_brief_genui,
    build_plan_genui,
    sanitize_spec,
    spec_debug_summary,
)
from clipwright.schema.requirements import CreativeBrief, ProductionPlan, brief_prompt_template


# ── 清洗器 ────────────────────────────────────────────────


class TestSanitize:
    def test_valid_spec_passes_through(self):
        spec, problems = sanitize_spec({
            "version": 1,
            "title": "简报",
            "items": [
                {"type": "text", "content": "你好"},
                {"type": "table", "columns": ["a", "b"], "rows": [["1", "2"]]},
            ],
        })
        assert problems == []
        assert spec["version"] == GENUI_VERSION
        assert [n["type"] for n in spec["items"]] == ["text", "table"]

    def test_missing_version_defaults_to_current(self):
        spec, _ = sanitize_spec({"items": [{"type": "text", "content": "x"}]})
        assert spec["version"] == GENUI_VERSION

    def test_unknown_version_rejects_whole_spec(self):
        spec, problems = sanitize_spec({"version": 99, "items": [{"type": "text", "content": "x"}]})
        assert spec is None
        assert any("版本" in p for p in problems)

    def test_unknown_component_degrades_to_placeholder(self):
        spec, problems = sanitize_spec({"items": [
            {"type": "hero", "title": "x"},
            {"type": "text", "content": "兄弟节点必须活下来"},
        ]})
        assert [n["type"] for n in spec["items"]] == ["text", "text"]
        assert "hero" in spec["items"][0]["content"]
        assert spec["items"][1]["content"] == "兄弟节点必须活下来"
        assert any("hero" in p for p in problems)

    def test_forbidden_keys_are_stripped(self):
        spec, _ = sanitize_spec({"items": [
            {"type": "card", "__proto__": {"polluted": True}, "items": [
                {"type": "text", "content": "in", "constructor": "x"},
            ]},
        ]})
        dumped = json.dumps(spec, ensure_ascii=False)
        assert "__proto__" not in dumped and "constructor" not in dumped
        assert spec["items"][0]["items"][0]["type"] == "text"

    def test_action_whitelist_and_payload_cleaning(self):
        spec, problems = sanitize_spec({"items": [
            {"type": "button", "label": "x"},  # 不在白名单 → 占位
            {"type": "card", "items": [{"type": "text", "content": "c"}],
             "action": {"type": "ask", "payload": {"q": "选哪个", "n": 3, "bad": object()}}},
            {"type": "text", "content": "坏动作", "action": {"type": "fetch", "url": "http://evil"}},
        ]})
        assert spec is not None, problems
        assert any("fetch" in p for p in problems)
        by_content = {
            n.get("content"): n for n in spec["items"] if isinstance(n, dict) and n.get("content")
        }
        assert "action" not in by_content["坏动作"], "非白名单动作必须丢弃"
        card = next(n for n in spec["items"] if n["type"] == "card")
        action = card.get("action")
        assert action and action["type"] == "ask"
        # 非 JSON 值被清成 None，而不是让整块爆炸
        assert action["payload"].get("bad") is None

    def test_text_length_is_clipped(self):
        long_text = "字" * (MAX_TEXT_CHARS + 50)
        spec, _ = sanitize_spec({"items": [{"type": "text", "content": long_text}]})
        assert spec["items"][0]["content"].endswith("（已截断）")

    def test_table_columns_are_capped(self):
        cols = [f"c{i}" for i in range(MAX_TABLE_COLS + 5)]
        spec, problems = sanitize_spec({"items": [
            {"type": "table", "columns": cols, "rows": [[str(i) for i in range(len(cols))]]},
        ]})
        assert len(spec["items"][0]["columns"]) == MAX_TABLE_COLS
        assert any("列数" in p for p in problems)

    def test_deep_nesting_is_dropped(self):
        node: dict = {"type": "text", "content": "leaf"}
        for _ in range(MAX_DEPTH + 2):
            node = {"type": "col", "items": [node]}
        spec, problems = sanitize_spec({"items": [node]})
        assert spec is not None
        assert any("嵌套" in p for p in problems)

    def test_node_budget_is_enforced(self):
        items = [{"type": "text", "content": str(i)} for i in range(MAX_NODES + 20)]
        spec, problems = sanitize_spec({"items": items})
        assert len(spec["items"]) == MAX_NODES
        assert any("节点数" in p for p in problems)

    def test_oversized_spec_is_rejected(self):
        # 单个文本字段会先被截到 4000 字，所以"超字节"要靠**节点数 × 单字段上限**堆出来：
        # 40 个 4000 字节点 ≈ 160KB > 64KB → 整块丢弃（前端回落原卡片，不是白屏）。
        filler = "x" * 4000
        items = [{"type": "text", "content": filler} for _ in range(40)]
        spec, problems = sanitize_spec({"items": items})
        assert spec is None
        assert any("字节" in p for p in problems)

    def test_non_object_and_empty_inputs(self):
        assert sanitize_spec(None) == (None, [])
        assert sanitize_spec("") == (None, [])
        assert sanitize_spec([])[0] is None
        assert sanitize_spec({"items": []})[0] is None
        assert sanitize_spec({"items": [{"type": "nope"}]})[0]["items"][0]["type"] == "text"


# ── 确定性构造器 ──────────────────────────────────────────


class TestBuilders:
    def test_build_brief_genui_from_fields(self):
        spec = build_brief_genui({
            "title": "T", "overview": "O",
            "target_audience": "年轻人", "duration_estimate": "90s",
            "key_elements": ["片头"], "special_requirements": ["不出现品牌 logo"],
        })
        assert spec and spec["version"] == GENUI_VERSION
        kinds = [n["type"] for n in spec["items"]]
        assert "table" in kinds and "list" in kinds
        assert any(n["type"] == "callout" for n in spec["items"])

    def test_build_brief_genui_empty_returns_none(self):
        assert build_brief_genui({}) is None
        assert build_brief_genui({"title": "  "}) is None

    def test_build_plan_genui_from_scenes(self):
        spec = build_plan_genui(
            {"total_duration_sec": 12, "material_summary": "3 场已接地"},
            [
                {"title": "开场", "duration_sec": 4, "keywords": ["a", "b"]},
                {"title": "正文", "duration_sec": 8, "keywords": ["c"]},
            ],
        )
        assert spec and spec["items"][0]["type"] == "row"
        table = next(n for n in spec["items"] if n["type"] == "table")
        assert len(table["rows"]) == 2
        assert table["rows"][0][1] == "开场"
        assert any(n["type"] == "callout" for n in spec["items"])

    def test_build_plan_genui_without_scenes_returns_none(self):
        assert build_plan_genui({}, []) is None


# ── 模型接线（权威数据不受影响）───────────────────────────


class TestSchemaWiring:
    def test_brief_sanitizes_genui_but_keeps_fields(self):
        brief = CreativeBrief(
            title="标题", overview="概述",
            genui={"version": 1, "items": [{"type": "text", "content": "ok"}]},
        )
        assert brief.title == "标题" and brief.genui["items"][0]["type"] == "text"

    def test_brief_invalid_genui_becomes_empty_not_fatal(self):
        brief = CreativeBrief(title="标题", genui={"version": 42, "items": []})
        assert brief.genui == {}
        assert brief.title == "标题"

    def test_plan_sanitizes_genui(self):
        plan = ProductionPlan(scene_count=2, genui={"items": [{"type": "bad"}]})
        assert plan.scene_count == 2
        assert plan.genui["items"][0]["type"] == "text"

    def test_prompt_template_lists_genui_field(self):
        """字段表由模型生成：新字段必须自动出现在提示词里（否则模型不知道能写）。"""
        template = brief_prompt_template()
        assert "genui" in template
        assert f'"genui"' in template or '"genui":' in template

    def test_spec_debug_summary(self):
        assert spec_debug_summary(None) == {"present": False}
        spec, _ = sanitize_spec({"items": [{"type": "text", "content": "x"}]})
        summary = spec_debug_summary(spec)
        assert summary["present"] is True and summary["top_level"] == 1


# ── agent 兜底 ────────────────────────────────────────────


class TestAgentFallback:
    def test_ensure_brief_genui_derives_when_missing(self):
        from clipwright.agents.requirements_agent import RequirementsAgent

        brief = {"title": "T", "overview": "O", "key_elements": ["a"], "genui": {}}
        out = RequirementsAgent._ensure_brief_genui(brief)
        assert out["genui"]["items"], "模型没给 spec 时应从字段确定性生成"

    def test_ensure_brief_genui_replaces_invalid(self):
        from clipwright.agents.requirements_agent import RequirementsAgent

        brief = {"title": "T", "overview": "O", "genui": {"version": 77, "items": []}}
        out = RequirementsAgent._ensure_brief_genui(brief)
        assert out["genui"]["version"] == GENUI_VERSION

    def test_plan_genui_derives_for_fallback_plan(self):
        from clipwright.agents.requirements_agent import RequirementsAgent

        spec = RequirementsAgent._plan_genui(
            {"markdown_content": "# x", "total_duration_sec": 6},
            [{"title": "s1", "duration_sec": 6}],
        )
        assert spec["items"]

    @pytest.mark.asyncio
    async def test_translate_scenes_attaches_genui_without_llm(self):
        """翻译失败走降级分支时，spec 仍要由场景确定性生成（不依赖模型）。"""
        from clipwright.agents.requirements_agent import RequirementsAgent

        agent = RequirementsAgent.__new__(RequirementsAgent)

        async def _boom(*args, **kwargs):
            raise RuntimeError("llm down")

        agent._llm = type("_LLM", (), {"structured_output": staticmethod(_boom)})()
        out = await agent.translate_scenes(
            [{"title": "开场", "duration_sec": 4, "keywords": ["k"]}], brief={"title": "T"},
            pipeline_id="p",
        )
        assert out["markdown_content"]
        assert out["genui"]["items"], "降级规划书也必须带展示 spec"


# ── 插件一致性 ────────────────────────────────────────────


class TestPluginContract:
    def test_plugin_components_match_schema(self):
        from plugins.genui import main as genui_plugin

        assert set(genui_plugin.COMPONENTS) == set(GENUI_COMPONENT_TYPES), (
            "插件教给模型的组件清单与 schema 白名单不一致——等于教错"
        )

    def test_plugin_prompt_mentions_every_component(self):
        from plugins.genui import main as genui_plugin

        for component in genui_plugin.COMPONENTS:
            assert component in genui_plugin.PROMPT, f"规范里没提 {component}"

    def test_plugin_declares_prompt_in_manifest(self):
        from pathlib import Path

        manifest = Path("plugins/genui/plugin.yaml").read_text(encoding="utf-8")
        assert "kind: prompt" in manifest
        assert "genui_requirements_prompt" in manifest

    def test_plugin_contribution_shape(self):
        from clipwright.plugins.contribution import ContributionKind
        from plugins.genui.main import GenUIPlugin

        items = GenUIPlugin().contributions()
        assert len(items) == 1
        item = items[0]
        assert item.kind is ContributionKind.PROMPT
        assert item.meta["agent"] == "requirements"
        assert item.meta["prompt"] == item.object
