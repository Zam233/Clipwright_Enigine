"""V45/F8：`prompt_resolution` 事件留痕单测。"""

from __future__ import annotations

from clipwright.category.base import BaseCategoryPlugin
from clipwright.category.registry import CategoryRegistry
from clipwright.schema.category_spec import ParamRule, PromptBundle, PromptSlot
from clipwright.services.prompt_authority import PromptAuthority
from clipwright.services.type_spec_runtime import (
    emit_prompt_resolution,
    resolve_shot_params,
)


class _P(BaseCategoryPlugin):
    plugin_id = "trace_demo"
    display_name = "Trace"

    def translate_persona(self, params):  # type: ignore[no-untyped-def]
        return {}

    def get_shot_params(self, translated):  # type: ignore[no-untyped-def]
        return {}

    def get_prompt_bundle(self):  # type: ignore[no-untyped-def]
        return PromptBundle(slots={"genre": PromptSlot(text="整片是一场对话")})

    def get_param_rules(self):  # type: ignore[no-untyped-def]
        return [
            ParamRule(key="min_shot_ms", mode="floor", value=1200, enforce="strict", reason="看清")
        ]


class TestEmitPromptResolution:
    def test_payload_has_provenance_and_conflicts(self) -> None:
        authority = PromptAuthority(
            prompts=PromptBundle(slots={"genre": PromptSlot(text="x")}),
            rules=[ParamRule(key="min_shot_ms", mode="floor", value=1200, enforce="strict")],
            category_id="demo",
        )
        resolution = authority.resolve(user={"min_shot_ms": 300})
        payload = emit_prompt_resolution(
            "p" * 32, "edit_agent", resolution, category_id="demo", stage="edit"
        )
        assert payload["category_id"] == "demo"
        assert payload["stage"] == "edit"
        assert payload["params"]["min_shot_ms"]["value"] == 1200
        assert payload["params"]["min_shot_ms"]["source_layer"] == "L2_prompt"
        assert payload["conflicts"][0]["code"] == "C1"
        assert payload["slots"]

    def test_payload_serializable(self) -> None:
        import json

        authority = PromptAuthority(category_id="demo")
        resolution = authority.resolve(user={})
        payload = emit_prompt_resolution("p" * 32, "x", resolution)
        assert json.dumps(payload, ensure_ascii=False)  # 不抛 = 可序列化

    def test_deformation_recorded(self) -> None:
        from clipwright.schema.category_spec import SignatureTrait

        authority = PromptAuthority(
            traits=[SignatureTrait(name="avg_shot_ms", mode="require_le", value=1500)],
            category_id="demo",
        )
        resolution = authority.resolve(user={"min_shot_ms": 5000})
        payload = emit_prompt_resolution("p" * 32, "x", resolution)
        assert payload["deformation"] is not None
        assert payload["deformation"]["code"] == "C9"

    def test_emit_never_raises(self) -> None:
        """trace 不可用时只 warning，不打断出片（D6）。"""
        authority = PromptAuthority(category_id="demo")
        resolution = authority.resolve(user={})
        assert emit_prompt_resolution("", "", resolution) is not None


class TestEditAgentEmits:
    def test_edit_agent_calls_emit(self) -> None:
        import inspect

        from clipwright.agents import edit_agent

        source = inspect.getsource(edit_agent.EditAgent.execute)
        assert "emit_prompt_resolution(" in source
        # 必须在拿到裁决结果之后才记（调用点顺序，不看 import 行）
        resolve_at = source.index("shot_params, _type_resolution = resolve_shot_params(")
        emit_at = source.index("emit_prompt_resolution(\n")
        assert resolve_at < emit_at


class TestResolveWithRules:
    def test_rules_applied_and_trace_present(self) -> None:
        CategoryRegistry.clear()
        CategoryRegistry.register(_P())
        try:
            shot, resolution = resolve_shot_params({"shot_params": {"min_shot_ms": 300}}, "trace_demo")
            assert shot["min_shot_ms"] == 1200
            assert resolution.trace["min_shot_ms"]["source_layer"] == "L2_prompt"
        finally:
            CategoryRegistry.clear()
