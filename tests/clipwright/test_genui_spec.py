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


# ── B2：清洗器改成契约驱动后，测试进程显式注册 genui 的两条 display_spec 契约 ──
@pytest.fixture(autouse=True, scope="module")
def _register_display_spec_contracts():
    """B2 起 `sanitize_spec` 按**注册表里的契约**清洗；测试不加载插件，因此显式注册。

    这也顺带验证真实路径：插件交付的 meta 能通过 `_validate_data_only` 与
    `normalize_contract`，而不是测试自己造一份"应该长这样"的契约。
    """
    from clipwright.plugins.contribution import ContributionKind, ContributionRegistry
    from plugins.genui import main as genui_plugin

    owner = "genui_test_fixture"
    ContributionRegistry.declared_scope(owner, ["display_spec:brief", "display_spec:plan"])
    report = ContributionRegistry.register(
        owner,
        [c for c in genui_plugin.GenUIPlugin().contributions()
         if c.kind is ContributionKind.DISPLAY_SPEC],
    )
    assert report.ok, f"测试契约注册失败: {report.rejected}"
    try:
        yield
    finally:
        ContributionRegistry.unregister_plugin(owner)



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
        """B2 起组件清单由插件契约提供；schema 常量在 B3 删除前必须与它一致（过渡断言）。"""
        from plugins.genui import main as genui_plugin

        assert set(genui_plugin.CONTRACT["components"]) == set(GENUI_COMPONENT_TYPES), (
            "插件契约的组件清单与 schema 白名单不一致——等于教错"
        )

    def test_plugin_prompt_mentions_every_component(self):
        from plugins.genui import main as genui_plugin

        for component in genui_plugin.CONTRACT["components"]:
            assert component in genui_plugin.PROMPT, f"规范里没提 {component}"

    def test_plugin_declares_prompt_in_manifest(self):
        from pathlib import Path

        manifest = Path("plugins/genui/plugin.yaml").read_text(encoding="utf-8")
        assert "kind: prompt" in manifest
        assert "genui_requirements_prompt" in manifest
        assert "kind: display_spec" in manifest
        assert "id: brief" in manifest and "id: plan" in manifest

    def test_plugin_contract_has_badge_tones(self):
        """迁移必须补上的既有缺陷：契约原来漏了 `badge_tones`，badge 的 accent 无法表达。"""
        from plugins.genui import main as genui_plugin

        assert genui_plugin.CONTRACT["badge_tones"] == ["success", "warn", "danger", "accent"]

    def test_plugin_contribution_shape(self):
        from clipwright.plugins.contribution import ContributionKind
        from plugins.genui.main import GenUIPlugin

        items = GenUIPlugin().contributions()
        assert len(items) == 3, "1 条提示词 + 2 条 display_spec（brief/plan）"
        prompt, brief, plan = items
        assert prompt.kind is ContributionKind.PROMPT
        assert prompt.meta["agent"] == "requirements"
        assert prompt.meta["prompt"] == prompt.object
        for item, spec_id, trust in ((brief, "brief", "sanitize"), (plan, "plan", "recipe_only")):
            assert item.kind is ContributionKind.DISPLAY_SPEC
            assert item.id == spec_id
            assert item.object is None, "纯数据扩展点：object 必须为 None"
            assert item.meta["spec_id"] == spec_id
            assert item.meta["trust"] == trust
            assert set(item.meta["limits"]) >= {"max_nodes", "max_spec_bytes"}
            assert item.meta["recipe"]["nodes"], "配方不能为空（plan 的 recipe_only 依赖它）"

# ── Batch 1 接线（计划 T1.1 / T1.2 / T1.3 / T1.4）─────────────────────────


class TestEnsureSpecWiring:
    """T1.2 / T1.3：服务层与 Agent 共用的两个公共函数。"""

    def test_ensure_brief_spec_fills_and_keeps_fields(self):
        from clipwright.schema.genui import ensure_brief_spec

        brief = {"title": "T", "overview": "O", "key_elements": ["a"]}
        out = ensure_brief_spec(brief)
        assert out is brief
        assert out["genui"]["items"], "没给 spec 时应确定性补一份"
        assert out["title"] == "T" and out["overview"] == "O"

    def test_ensure_brief_spec_sanitizes_model_spec(self):
        from clipwright.schema.genui import ensure_brief_spec

        brief = {"title": "T", "genui": {"version": 1, "items": [{"type": "hero"}]}}
        out = ensure_brief_spec(brief)
        assert out["genui"]["items"][0]["type"] == "text"  # 未知组件降级

    def test_ensure_brief_spec_replaces_hopeless_spec(self):
        from clipwright.schema.genui import ensure_brief_spec

        brief = {"title": "T", "genui": {"version": 99, "items": []}}
        out = ensure_brief_spec(brief)
        assert out["genui"]["version"] == 1 and out["genui"]["items"]

    def test_ensure_plan_spec_ignores_model_spec(self):
        """规划书**只**用确定性构造器：模型给的 genui 一律不看。"""
        from clipwright.schema.genui import ensure_plan_spec

        plan = {
            "markdown_content": "# m",
            "total_duration_sec": 6,
            "genui": {"version": 1, "items": [{"type": "text", "content": "模型编的"}]},
        }
        spec = ensure_plan_spec(plan, [{"title": "开场", "duration_sec": 6}])
        assert spec is plan["genui"]
        dumped = json.dumps(spec, ensure_ascii=False)
        assert "模型编的" not in dumped
        assert any(n["type"] == "table" for n in spec["items"])

    def test_ensure_plan_spec_without_scenes(self):
        from clipwright.schema.genui import ensure_plan_spec

        plan = {"markdown_content": "# m"}
        assert ensure_plan_spec(plan, []) == {}
        assert plan["genui"] == {}


class TestPromptPlaceholderAndInjection:
    """T1.4：占位符回来了，且生产简报路径真的消费插件提示词。"""

    def test_prompts_constant_has_placeholder(self):
        from clipwright import prompts

        assert "{{BRIEF_TEMPLATE}}" in prompts.CREATIVE_BRIEF_SYSTEM

    def test_agent_renders_placeholder_at_runtime(self):
        from clipwright.agents.requirements_agent import _brief_system_prompt

        rendered = _brief_system_prompt()
        assert "{{BRIEF_TEMPLATE}}" not in rendered
        assert "| 字段 | 类型 | 说明 |" in rendered
        assert "genui" in rendered, "字段表应包含新的 genui 字段"

    def test_service_prompt_consumes_plugin_registry(self, monkeypatch):
        from clipwright.plugins.prompt_registry import PluginPromptRegistry
        from clipwright.services import requirements_service as rs

        monkeypatch.setattr(
            PluginPromptRegistry, "get_for_agent",
            classmethod(lambda cls, agent: ["FAKE_GENUI_RULES"]),
        )
        prompt = rs._brief_system_prompt("CTX")
        assert "FAKE_GENUI_RULES" in prompt
        # 顺序：base + context + 插件段（插件段追加在最后，不插进正文中间）
        assert "CTX" in prompt
        assert prompt.rstrip().endswith("FAKE_GENUI_RULES")
        assert "字段" in prompt

    def test_service_prompt_without_plugins(self, monkeypatch):
        from clipwright.plugins.prompt_registry import PluginPromptRegistry
        from clipwright.services import requirements_service as rs

        monkeypatch.setattr(
            PluginPromptRegistry, "get_for_agent",
            classmethod(lambda cls, agent: []),
        )
        prompt = rs._brief_system_prompt("")
        assert "FAKE_GENUI_RULES" not in prompt


class TestPlanTranslateWiring:
    """T1.3：`_translate_plan` 末尾补 genui，两条主路径共用。"""

    def _service(self, monkeypatch, llm_result):
        from clipwright.services import requirements_service as rs

        svc = rs.RequirementsService.__new__(rs.RequirementsService)
        svc._llm = object()

        async def _noop(*args, **kwargs):
            return None

        monkeypatch.setattr(rs.RequirementsService, "_record_llm_usage", _noop)

        async def _fake_llm(*args, **kwargs):
            return dict(llm_result)

        monkeypatch.setattr(rs, "llm_call_with_retry", _fake_llm)
        return svc

    async def test_attaches_genui_without_touching_markdown(self, monkeypatch):
        svc = self._service(monkeypatch, {
            "summary": "ok", "markdown_content": "# 规划书\n\n正文保留",
        })
        scenes = [
            {"title": "开场", "duration_sec": 4, "keywords": ["a"]},
            {"title": "正文", "duration_sec": 8, "keywords": ["b"]},
        ]
        result = await svc._translate_plan(scenes, {"title": "T"}, script_text="")
        assert result["markdown_content"].startswith("# 规划书")
        assert "正文保留" in result["markdown_content"]
        assert result["genui"]["items"], "规划书必须带确定性 spec"
        assert result["raw_scenes"] == scenes

    async def test_model_genui_is_ignored(self, monkeypatch):
        svc = self._service(monkeypatch, {
            "summary": "ok", "markdown_content": "# 规划书\n\n正文",
            "genui": {"version": 1, "items": [{"type": "text", "content": "模型编的展示"}]},
        })
        result = await svc._translate_plan(
            [{"title": "开场", "duration_sec": 4}], {"title": "T"},
        )
        assert "模型编的展示" not in json.dumps(result["genui"], ensure_ascii=False)

    async def test_fallback_path_also_has_genui(self, monkeypatch):
        """LLM 两次都没正文 → 确定性兜底，但 spec 仍然要有。"""
        svc = self._service(monkeypatch, {})
        result = await svc._translate_plan([{"title": "开场", "duration_sec": 4}], {})
        assert result["markdown_content"]
        assert result["genui"]["items"]


class TestOldSessionBackfill:
    """T2.1：读路径补 spec——只补不存，入参零改动。"""

    @staticmethod
    def _session() -> dict:
        return {
            "id": "s_old",
            "creative_brief": {"title": "T", "overview": "O"},
            "production_plan": {
                "markdown_content": "# 规划书",
                "raw_scenes": [{"title": "开场", "duration_sec": 6}],
            },
        }

    def test_fills_both_specs_without_mutating_input(self):
        import copy

        from clipwright.services.requirements_service import _with_genui_specs

        session = self._session()
        snapshot = copy.deepcopy(session)
        out = _with_genui_specs(session)
        assert out["creative_brief"]["genui"]["items"]
        assert out["production_plan"]["genui"]["items"]
        assert session == snapshot, "读路径不得原地修改会话对象"
        assert out is not session
        assert out["creative_brief"] is not session["creative_brief"]
        assert out["production_plan"] is not session["production_plan"]

    def test_plan_backfill_ignores_model_genui(self):
        from clipwright.services.requirements_service import _with_genui_specs

        session = self._session()
        session["production_plan"]["genui"] = {
            "version": 1, "items": [{"type": "text", "content": "模型编的"}],
        }
        out = _with_genui_specs(session)
        assert "模型编的" not in json.dumps(out["production_plan"]["genui"], ensure_ascii=False)

    def test_missing_or_empty_fields_are_safe(self):
        from clipwright.services.requirements_service import _with_genui_specs

        assert _with_genui_specs({"id": "x"}) == {"id": "x"}
        assert _with_genui_specs(None) is None
        out = _with_genui_specs({"id": "x", "creative_brief": None, "production_plan": None})
        assert out == {"id": "x", "creative_brief": None, "production_plan": None}

    def test_plan_without_scenes_yields_empty_spec(self):
        from clipwright.services.requirements_service import _with_genui_specs

        out = _with_genui_specs({"id": "x", "production_plan": {"markdown_content": "# m"}})
        assert out["production_plan"]["genui"] == {}


class TestContractSingleSource:
    """B2：契约**单一真相在插件**（`plugins/genui/contract.json`）；engine 旧常量在 B3 删除前必须与之逐项相等（过渡断言）。

    为什么需要它：组件白名单/上限原来靠"两仓手抄"，改一边忘另一边就是半渲染或
    前后端判断不一致。JSON 本身不含逻辑，只做**对账基准**。
    """

    @staticmethod
    def _contract():
        """插件契约是唯一真相；engine 旧文件在 B3 删除前只做过渡对账。"""
        import json
        from pathlib import Path

        return json.loads(
            Path("plugins/genui/contract.json").read_text(encoding="utf-8")
        )

    def test_version(self):
        from clipwright.schema.genui import GENUI_VERSION

        assert self._contract()["version"] == GENUI_VERSION

    def test_component_and_container_sets(self):
        from clipwright.schema.genui import (
            GENUI_COMPONENT_TYPES,
            GENUI_CONTAINER_TYPES,
        )

        contract = self._contract()
        assert set(contract["components"]) == set(GENUI_COMPONENT_TYPES)
        assert len(contract["components"]) == len(GENUI_COMPONENT_TYPES), "白名单不得有重复项"
        assert set(contract["containers"]) == set(GENUI_CONTAINER_TYPES)

    def test_actions_tones_text_sizes(self):
        from clipwright.schema.genui import (
            GENUI_ACTION_KINDS,
            GENUI_TEXT_SIZES,
            GENUI_TONES,
        )

        contract = self._contract()
        assert set(contract["actions"]) == set(GENUI_ACTION_KINDS)
        assert set(contract["tones"]) == set(GENUI_TONES)
        assert set(contract["text_sizes"]) == set(GENUI_TEXT_SIZES)

    def test_limits(self):
        from clipwright.schema import genui as g

        limits = self._contract()["limits"]
        assert limits["max_nodes"] == g.MAX_NODES
        assert limits["max_depth"] == g.MAX_DEPTH
        assert limits["max_text_chars"] == g.MAX_TEXT_CHARS
        assert limits["max_spec_bytes"] == g.MAX_SPEC_BYTES
        assert limits["max_array_items"] == g.MAX_ARRAY_ITEMS
        assert limits["max_table_cols"] == g.MAX_TABLE_COLS
        assert limits["max_action_payload_chars"] == g.MAX_ACTION_PAYLOAD_CHARS
        assert limits["max_select_options"] == g.MAX_SELECT_OPTIONS
        assert limits["max_option_chars"] == g.MAX_OPTION_CHARS


class TestFallbackAcrossGenres:
    """T6.2 的**确定性部分**：3 类选题哪怕模型不给 spec，兜底也要出可用结构。

    真模型产出质量仍需人看（计划 §4.3）；这里锁的是"兜底不出空洞/不出占位"。
    """

    BRIEFS = {
        "知识区长片": {
            "title": "科技史：从蒸汽机到大模型",
            "overview": "用三次工业革命串起技术演进",
            "target_audience": "对科技史感兴趣的泛知识用户",
            "core_message": "每一次效率跃迁都重塑了人的工作",
            "style_direction": "冷静克制的纪录片质感",
            "structure_suggestion": "三幕：蒸汽—电力—智能",
            "duration_estimate": "8 分钟",
            "key_elements": ["蒸汽机剖面", "大模型推理示意"],
            "special_requirements": ["不出现未公开数据"],
        },
        "Vlog": {
            "title": "一个人去北海道看雪的七天",
            "overview": "独行视角的冬日小城记录",
            "target_audience": "喜欢慢节奏旅行内容的年轻人",
            "core_message": "安静也是一种陪伴",
            "style_direction": "低饱和、自然光",
            "structure_suggestion": "按天推进，结尾回到出发地",
            "duration_estimate": "6 分钟",
            "key_elements": ["雪夜街道", "便利店关东煮"],
            "special_requirements": ["不要配热血 BGM"],
        },
        "数码评测": {
            "title": "三款降噪耳机横评，谁最能隔绝地铁噪音",
            "overview": "地铁实测 + 参数对照",
            "target_audience": "通勤党、准备换耳机的人",
            "core_message": "降噪看的是低频段而不是参数表",
            "style_direction": "干净利落的数据风",
            "structure_suggestion": "结论前置，再逐项拆解",
            "duration_estimate": "5 分钟",
            "key_elements": ["地铁车厢实拍", "降噪曲线"],
            "special_requirements": ["必须标注价格与测试环境"],
        },
    }

    @pytest.mark.parametrize("genre", list(BRIEFS))
    def test_fallback_is_usable_for_every_genre(self, genre: str):
        from clipwright.schema.genui import build_brief_genui

        brief = dict(self.BRIEFS[genre])
        spec = build_brief_genui(brief)
        assert spec, f"{genre}: 兜底不应为空"
        types = [n["type"] for n in spec["items"]]
        assert "text" in types, f"{genre}: 至少一段文本"
        assert ("table" in types) or ("list" in types), f"{genre}: 至少一个 table 或 list"
        dumped = json.dumps(spec, ensure_ascii=False)
        assert "暂不支持" not in dumped and "渲染失败" not in dumped
        # 权威字段必须能在 spec 里找到（否则卡片与字段自相矛盾）
        assert str(brief["title"]) in dumped
        assert str(brief["duration_estimate"]) in dumped
        # 兜底 spec 自己也得过后端清洗器（否则写入即降级）
        from clipwright.schema.genui import sanitize_spec

        cleaned, problems = sanitize_spec(spec, source="test")
        assert cleaned is not None and not problems, f"{genre}: 兜底产出被清洗器拒绝：{problems}"


class TestSelectAction:
    """T4.2：`select` 的载荷必须是非空字符串选项组；否则整个动作丢弃。"""

    def _clean(self, action):
        from clipwright.schema.genui import sanitize_spec

        spec, problems = sanitize_spec({"version": 1, "items": [
            {"type": "text", "content": "选一个", "action": action},
        ]})
        return spec, problems

    def test_keeps_options_and_dedups(self):
        spec, _ = self._clean({"type": "select", "payload": {"options": ["A", "B", "A", " ", 3]}})
        assert spec["items"][0]["action"] == {"type": "select", "payload": {"options": ["A", "B"]}}

    def test_caps_options_and_clips_each(self):
        from clipwright.schema.genui import MAX_OPTION_CHARS, MAX_SELECT_OPTIONS

        long = "x" * (MAX_OPTION_CHARS + 50)
        # 长选项放**最前**：否则总数封顶会先截断，压根走不到"单选项截断"分支
        options = [long] + [f"opt{i}" for i in range(MAX_SELECT_OPTIONS + 4)]
        spec, _ = self._clean({"type": "select", "payload": {"options": options}})
        cleaned = spec["items"][0]["action"]["payload"]["options"]
        assert len(cleaned) == MAX_SELECT_OPTIONS
        assert all(len(o) <= MAX_OPTION_CHARS for o in cleaned)

    def test_dropped_without_options(self):
        for payload in (None, {}, {"options": []}, {"options": ["", "  "]}, {"options": "A"}):
            action = {"type": "select"} if payload is None else {"type": "select", "payload": payload}
            spec, problems = self._clean(action)
            assert spec is not None, "节点本身应保留"
            assert "action" not in spec["items"][0], f"{payload}: select 无可用选项时应丢弃动作"
            assert any("select" in p for p in problems), problems

    def test_ask_and_confirm_unaffected(self):
        for kind in ("ask", "confirm"):
            spec, _ = self._clean({"type": kind, "label": "确认", "payload": {"prompt": "x"}})
            assert spec["items"][0]["action"]["type"] == kind
            assert spec["items"][0]["action"]["payload"] == {"prompt": "x"}
