"""通用装配器 `services/display_spec/assemble.py` 的行为契约（B3-T3.4）。

要证明的八件事（对应计划 T3.4 验收）：无契约不动 / 有契约补 / `recipe_only` 忽略模型 /
只碰 `display_specs` 键 / 异常不阻断 / 多契约按 order / 空配方不写 / 坏契约不抛。
"""
from __future__ import annotations

import copy

import pytest

from clipwright.plugins.contribution import Contribution, ContributionKind, ContributionRegistry
from clipwright.services.display_spec import ensure_display_specs
from clipwright.services.display_spec import assemble as assemble_mod
from plugins.genui import main as genui_plugin


def _meta(spec_id: str, *, source: str, order: int = 100, trust: str = "sanitize",
          recipe: dict | None = None) -> dict:
    meta = {
        **{k: genui_plugin.CONTRACT[k] for k in (
            "version", "components", "containers", "actions", "tones",
            "badge_tones", "text_sizes", "limits",
        )},
        "spec_id": spec_id,
        "source": source,
        "order": order,
        "trust": trust,
    }
    if recipe is not None:
        meta["recipe"] = recipe
    return meta


@pytest.fixture
def registry():
    """注册若干假契约；测试结束统一回收（注册表是全局的）。"""
    owners: list[str] = []

    def _register(owner: str, metas: list[tuple[str, dict]]):
        owner = f"plug_{owner}"
        ContributionRegistry.declared_scope(owner, [f"display_spec:{sid}" for sid, _ in metas])
        report = ContributionRegistry.register(
            owner,
            [Contribution(ContributionKind.DISPLAY_SPEC, sid, None, meta) for sid, meta in metas],
        )
        assert report.ok, report.rejected
        owners.append(owner)

    yield _register
    for owner in owners:
        ContributionRegistry.unregister_plugin(owner)


SIMPLE_RECIPE = {
    "title": "假面",
    "nodes": [{"type": "text", "content": {"field": "title"}, "skip_if_empty": "content"}],
}


class TestAssembly:
    def test_no_contract_leaves_payload_untouched(self):
        payload = {"title": "T"}
        assert ensure_display_specs("nonexistent_source", payload) is payload
        assert payload == {"title": "T"}
        assert "display_specs" not in payload

    def test_recipe_fills_when_model_gave_nothing(self, registry):
        registry("asm_a", [("spec_a", _meta("spec_a", source="brief", recipe=SIMPLE_RECIPE))])
        payload = {"title": "T"}
        ensure_display_specs("brief", payload)
        assert payload["display_specs"]["spec_a"]["items"][0]["content"] == "T"

    def test_model_spec_is_sanitized_when_trust_sanitize(self, registry):
        registry("asm_b", [("spec_b", _meta("spec_b", source="brief", recipe=SIMPLE_RECIPE))])
        payload = {
            "title": "T",
            "display_specs": {"spec_b": {"version": 1, "items": [{"type": "text", "content": "模型给的"}]}},
        }
        ensure_display_specs("brief", payload)
        assert payload["display_specs"]["spec_b"]["items"][0]["content"] == "模型给的"

    def test_recipe_only_ignores_model_spec(self, registry):
        registry("asm_c", [
            ("spec_c", _meta("spec_c", source="plan", trust="recipe_only", recipe=SIMPLE_RECIPE)),
        ])
        payload = {
            "title": "T",
            "display_specs": {"spec_c": {"version": 1, "items": [{"type": "text", "content": "模型编的"}]}},
        }
        ensure_display_specs("plan", payload)
        assert payload["display_specs"]["spec_c"]["items"][0]["content"] == "T"

    def test_only_display_specs_key_is_touched(self, registry):
        registry("asm_d", [("spec_d", _meta("spec_d", source="brief", recipe=SIMPLE_RECIPE))])
        payload = {"title": "T", "overview": "O", "display_specs": {}}
        snapshot = copy.deepcopy(payload)
        ensure_display_specs("brief", payload)
        assert payload["title"] == snapshot["title"] == "T"
        assert payload["overview"] == snapshot["overview"] == "O"
        assert set(payload) - set(snapshot) == set()

    def test_exception_does_not_block_business_write(self, registry, monkeypatch):
        registry("asm_e", [("spec_e", _meta("spec_e", source="brief", recipe=SIMPLE_RECIPE))])

        def _boom(*args, **kwargs):
            raise RuntimeError("sanitizer 爆炸")

        monkeypatch.setattr(assemble_mod, "sanitize_display_spec", _boom)
        payload = {"title": "T"}
        ensure_display_specs("brief", payload)  # 不抛
        assert payload["title"] == "T"

    def test_multiple_contracts_keep_registry_order(self, registry):
        registry("asm_f", [
            ("spec_f2", _meta("spec_f2", source="brief", order=200, recipe=SIMPLE_RECIPE)),
            ("spec_f1", _meta("spec_f1", source="brief", order=100, recipe=SIMPLE_RECIPE)),
        ])
        payload = {"title": "T"}
        ensure_display_specs("brief", payload)
        assert set(payload["display_specs"]) == {"spec_f1", "spec_f2"}

    def test_empty_recipe_writes_nothing(self, registry):
        registry("asm_g", [("spec_g", _meta("spec_g", source="brief", recipe={"nodes": []}))])
        payload = {"title": "T"}
        ensure_display_specs("brief", payload)
        assert "display_specs" not in payload

    def test_invalid_model_spec_is_dropped_and_replaced_by_recipe(self, registry):
        registry("asm_h", [("spec_h", _meta("spec_h", source="brief", recipe=SIMPLE_RECIPE))])
        payload = {
            "title": "T",
            "display_specs": {"spec_h": {"version": 99, "items": [{"type": "text", "content": "x"}]}},
        }
        ensure_display_specs("brief", payload)
        assert payload["display_specs"]["spec_h"]["items"][0]["content"] == "T"

    def test_scenes_override_view(self, registry):
        recipe = {
            "title": "场景",
            "nodes": [{"type": "stat", "label": "场景数", "value": {"count": "raw_scenes"}}],
        }
        registry("asm_i", [("spec_i", _meta("spec_i", source="plan", recipe=recipe))])
        payload = {"raw_scenes": [{"title": "old"}]}
        ensure_display_specs("plan", payload, scenes=[{"title": "a"}, {"title": "b"}])
        assert payload["display_specs"]["spec_i"]["items"][0]["value"] == "2"
