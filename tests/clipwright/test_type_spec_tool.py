"""类型规范查询工具单测（PRD Part I F10 / A-11）。"""

from __future__ import annotations

import pytest

from clipwright.category.base import BaseCategoryPlugin
from clipwright.category.registry import CategoryRegistry
from clipwright.schema.category_spec import ParamRule, PromptBundle, PromptSlot, SignatureTrait
from clipwright.tool.surface import build_surface
from clipwright.tool.type_spec_tool import GetTypeSpecTool


class _SpecPlugin(BaseCategoryPlugin):
    plugin_id = "spec_demo"
    display_name = "Spec"

    def translate_persona(self, params):  # type: ignore[no-untyped-def]
        return {}

    def get_shot_params(self, translated):  # type: ignore[no-untyped-def]
        return {}

    def get_prompt_bundle(self):  # type: ignore[no-untyped-def]
        return PromptBundle(
            slots={
                "genre": PromptSlot(text="整片是一场对话", priority=100),
                "anti_patterns": PromptSlot(text="不是 KTV 字幕"),
                "edit": PromptSlot(text="硬切为主"),
            }
        )

    def get_param_rules(self):  # type: ignore[no-untyped-def]
        return [ParamRule(key="min_shot_ms", mode="floor", value=1200, enforce="strict")]

    def get_signature_traits(self):  # type: ignore[no-untyped-def]
        return [SignatureTrait(name="avg_shot_ms", mode="require_ge", value=2000, enforce="strict")]


@pytest.fixture(autouse=True)
def _isolate():
    CategoryRegistry.clear()
    yield
    CategoryRegistry.clear()


@pytest.mark.asyncio
class TestGetTypeSpec:
    async def test_returns_full_spec(self) -> None:
        CategoryRegistry.register(_SpecPlugin())
        result = await GetTypeSpecTool().execute(category_id="spec_demo")
        assert result.status == "success"
        out = result.output
        assert out["found"] is True
        assert out["prompts"]["genre"] == "整片是一场对话"
        assert "整片是一场对话" in out["identity_text"]
        assert out["param_rules"][0]["key"] == "min_shot_ms"
        assert out["signature_traits"][0]["name"] == "avg_shot_ms"
        assert out["strict_rules"] == ["min_shot_ms"]
        assert out["strict_traits"] == ["avg_shot_ms"]

    async def test_stage_text(self) -> None:
        CategoryRegistry.register(_SpecPlugin())
        result = await GetTypeSpecTool().execute(category_id="spec_demo", stage="edit")
        assert "硬切为主" in result.output["stage_text"]

    async def test_unknown_type_returns_found_false(self) -> None:
        result = await GetTypeSpecTool().execute(category_id="missing")
        assert result.status == "success"
        assert result.output["found"] is False

    async def test_empty_category_is_error(self) -> None:
        result = await GetTypeSpecTool().execute(category_id="  ")
        assert result.status == "error"
        assert result.output["found"] is False


class TestSurfaceIntegration:
    def test_tool_is_in_surface_as_read(self) -> None:
        entries = {entry.name: entry for entry in build_surface()}
        assert "get_type_spec" in entries
        assert entries["get_type_spec"].tier == "read"

    def test_dangerous_never_in_surface(self) -> None:
        names = {entry.name for entry in build_surface()}
        assert "run_shell" not in names
        assert "video_download" not in names
