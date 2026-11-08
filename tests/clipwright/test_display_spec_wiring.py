"""展示层接线的行为契约（B3：指纹排除 / 装配 / 字段形状 / 读路径归一）。

本文件对应编码计划 B3 的 T3.1（指纹）、T3.4（装配）、T3.5（字段）、T3.6（读路径与接线）。
"""
from __future__ import annotations

import copy

from clipwright.services.requirements_service import _brief_hash


class TestBriefHash:
    """T3.1：**整个展示容器**不得进简报指纹（C10）。

    老键 `genui`（2026-11-03~07 落库）与新容器 `display_specs` 都要排除；只改业务字段才换指纹。
    """

    def test_genui_is_excluded(self):
        assert _brief_hash({"title": "T"}) == _brief_hash(
            {"title": "T", "genui": {"version": 1, "items": [{"type": "text", "content": "x"}]}}
        )

    def test_display_specs_is_excluded(self):
        assert _brief_hash({"title": "T"}) == _brief_hash(
            {"title": "T", "display_specs": {"brief": {"version": 1, "items": [{"type": "text"}]}}}
        )

    def test_both_keys_injected_still_excluded(self):
        payload = {
            "title": "T",
            "genui": {"version": 1, "items": [{"type": "text", "content": "old"}]},
            "display_specs": {"brief": {"version": 1, "items": [{"type": "text", "content": "new"}]}},
        }
        assert _brief_hash({"title": "T"}) == _brief_hash(payload)
        assert _brief_hash(payload) != _brief_hash(payload | {"title": "T2"})

    def test_real_field_change_still_changes_hash(self):
        assert _brief_hash({"title": "T"}) != _brief_hash({"title": "T2"})

    def test_spec_value_change_does_not_change_hash(self):
        base = {"title": "T", "display_specs": {"brief": {"items": [{"type": "text", "content": "a"}]}}}
        other = {"title": "T", "display_specs": {"brief": {"items": [{"type": "text", "content": "b"}]}}}
        assert _brief_hash(base) == _brief_hash(other)

    def test_empty_inputs(self):
        assert _brief_hash({}) == ""
        assert _brief_hash(None) == ""

    def test_display_only_payload_has_no_hash(self):
        assert _brief_hash({"genui": {"items": [{"type": "text", "content": "x"}]}}) == ""
        assert _brief_hash({"display_specs": {"brief": {"items": []}}}) == ""


# ── T3.5 / T3.6：字段形状、读路径归一、规划书接线 ──────────────────────

import pytest

from clipwright.plugins.contribution import Contribution, ContributionKind, ContributionRegistry
from plugins.genui import main as genui_plugin


@pytest.fixture(scope="module", autouse=True)
def _register_contracts():
    """本模块的读路径/接线用例需要"插件已安装"的状态：显式注册它的两条契约。"""
    owner = "wiring_fixture"
    ContributionRegistry.declared_scope(owner, ["display_spec:brief", "display_spec:plan"])
    report = ContributionRegistry.register(
        owner,
        [c for c in genui_plugin.GenUIPlugin().contributions()
         if c.kind is ContributionKind.DISPLAY_SPEC],
    )
    assert report.ok, report.rejected
    try:
        yield
    finally:
        ContributionRegistry.unregister_plugin(owner)


class TestSchemaShape:
    """T3.5：模型层只做形状收口（不 import 插件知识）。"""

    def test_fields_renamed(self):
        from clipwright.schema.requirements import CreativeBrief, ProductionPlan

        assert "genui" not in CreativeBrief.model_fields
        assert "display_specs" in CreativeBrief.model_fields
        assert "genui" not in ProductionPlan.model_fields
        assert "display_specs" in ProductionPlan.model_fields

    def test_accepts_well_formed_container(self):
        from clipwright.schema.requirements import CreativeBrief

        brief = CreativeBrief(title="T", display_specs={"brief": {"version": 1, "items": []}})
        assert list(brief.display_specs) == ["brief"]

    @pytest.mark.parametrize(
        "bad",
        [
            {"": {}},
            {"brief": "not-a-dict"},
            "not-a-dict",
            {f"s{i}": {} for i in range(9)},
        ],
    )
    def test_rejects_bad_shape(self, bad):
        from pydantic import ValidationError

        from clipwright.schema.requirements import CreativeBrief

        with pytest.raises(ValidationError):
            CreativeBrief(display_specs=bad)


class TestReadPath:
    """T3.6：老键 `genui` 在读路径归一，只补不存。"""

    def test_legacy_key_is_normalized(self):
        from clipwright.services.requirements_service import _with_display_specs

        session = {
            "id": "s1",
            "creative_brief": {"title": "T", "genui": {"version": 1, "items": [{"type": "text", "content": "旧"}]}},
        }
        out = _with_display_specs(session)
        assert "brief" in out["creative_brief"]["display_specs"]
        assert out["creative_brief"]["display_specs"]["brief"]["items"][0]["content"] == "旧"

    def test_new_shape_wins_over_legacy(self):
        from clipwright.services.requirements_service import _with_display_specs

        session = {
            "id": "s1",
            "creative_brief": {
                "title": "T",
                "display_specs": {"brief": {"version": 1, "items": [{"type": "text", "content": "新"}]}},
                "genui": {"version": 1, "items": [{"type": "text", "content": "旧"}]},
            },
        }
        out = _with_display_specs(session)
        assert out["creative_brief"]["display_specs"]["brief"]["items"][0]["content"] == "新"

    def test_input_is_not_mutated(self):
        import copy

        from clipwright.services.requirements_service import _with_display_specs

        session = {"id": "s1", "creative_brief": {"title": "T"}}
        snapshot = copy.deepcopy(session)
        out = _with_display_specs(session)
        assert session == snapshot
        assert out is not session

    def test_plan_uses_scenes_from_payload(self):
        from clipwright.services.requirements_service import _with_display_specs

        session = {
            "id": "s1",
            "production_plan": {
                "markdown_content": "# m",
                "raw_scenes": [{"title": "A", "duration_sec": 5}, {"title": "B", "duration_sec": 7}],
            },
        }
        out = _with_display_specs(session)
        plan_spec = out["production_plan"]["display_specs"]["plan"]
        # 场景表 2 行 + 统计行 + 无 material_summary（不生成 callout）
        values = [n["type"] for n in plan_spec["items"]]
        assert "table" in values and "row" in values


class TestTranslatePlanWiring:
    """T3.6：`_translate_plan` 走通用装配器；模型给的 spec 一律不看（recipe_only）。"""

    @staticmethod
    def _service(monkeypatch, llm_result):
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

    async def test_attaches_plan_spec_and_keeps_markdown(self, monkeypatch):
        svc = self._service(monkeypatch, {"summary": "ok", "markdown_content": "# 规划书\n\n正文保留"})
        scenes = [{"title": "开场", "duration_sec": 4}, {"title": "正文", "duration_sec": 8}]
        result = await svc._translate_plan(scenes, {"title": "T"}, script_text="")
        assert "正文保留" in result["markdown_content"]
        assert result["display_specs"]["plan"]["items"]
        assert result["raw_scenes"] == scenes

    async def test_model_spec_is_ignored(self, monkeypatch):
        svc = self._service(monkeypatch, {
            "summary": "ok", "markdown_content": "# 规划书",
            "genui": {"version": 1, "items": [{"type": "text", "content": "模型编的展示"}]},
        })
        result = await svc._translate_plan([{"title": "开场", "duration_sec": 4}], {"title": "T"})
        dumped = str(result["display_specs"])
        assert "模型编的展示" not in dumped
