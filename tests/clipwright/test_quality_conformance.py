"""A-09 接线：质检里的类型同一性条目（PRD F12 / V16）。"""

from __future__ import annotations

import pytest

from clipwright.agents.quality_agent import _check_type_conformance
from clipwright.category.base import BaseCategoryPlugin
from clipwright.category.registry import CategoryRegistry
from clipwright.schema.category_spec import SignatureTrait
from clipwright.schema.timeline import Clip, ClipKind, Timeline, Track


class _TraitsPlugin(BaseCategoryPlugin):
    plugin_id = "trait_demo"
    display_name = "Traits"

    def __init__(self, traits: list[SignatureTrait] | None = None) -> None:
        self._traits = traits or []

    def translate_persona(self, params):  # type: ignore[no-untyped-def]
        return {}

    def get_shot_params(self, translated):  # type: ignore[no-untyped-def]
        return {}

    def get_signature_traits(self):  # type: ignore[no-untyped-def]
        return self._traits


class _Ctx:
    def __init__(self, category: str, extra: dict | None = None) -> None:
        self.category_plugin_id = category
        self.extra_params = extra or {}


def _timeline(durations: list[float]) -> Timeline:
    start = 0.0
    clips = []
    for index, duration in enumerate(durations):
        clips.append(
            Clip(
                id=f"c{index}", kind=ClipKind.VIDEO, asset_id=f"a{index}",
                track_id="v", start_sec=start, duration_sec=duration,
                source_offset_sec=0, speed=1, volume=1, opacity=1, keyframes=[],
            )
        )
        start += duration
    return Timeline(
        id="tl", width=1920, height=1080, fps=30, duration_sec=start,
        tracks=[Track(id="v", name="v", kind=ClipKind.VIDEO, index=0, clips=clips)],
    )


@pytest.fixture(autouse=True)
def _isolate():
    CategoryRegistry.clear()
    yield
    CategoryRegistry.clear()


class TestQualityConformance:
    def test_no_category_is_noop(self) -> None:
        assert _check_type_conformance(_timeline([2.0]), _Ctx("")) == []

    def test_type_without_traits_is_noop(self) -> None:
        CategoryRegistry.register(_TraitsPlugin())
        assert _check_type_conformance(_timeline([2.0]), _Ctx("trait_demo")) == []

    def test_strict_failure_becomes_error(self) -> None:
        CategoryRegistry.register(
            _TraitsPlugin([SignatureTrait(name="avg_shot_ms", mode="require_ge", value=2000, enforce="strict")])
        )
        issues = _check_type_conformance(_timeline([1.0]), _Ctx("trait_demo"))
        errors = [i for i in issues if i.severity == "error"]
        assert errors and errors[0].category == "type_conformance"

    def test_soft_failure_becomes_warning(self) -> None:
        CategoryRegistry.register(
            _TraitsPlugin([SignatureTrait(name="avg_shot_ms", mode="require_ge", value=2000, enforce="soft")])
        )
        issues = _check_type_conformance(_timeline([1.0]), _Ctx("trait_demo"))
        levels = {i.severity for i in issues}
        assert "warning" in levels and "error" not in levels

    def test_passing_trait_only_reports_judge_skipped(self) -> None:
        CategoryRegistry.register(
            _TraitsPlugin([SignatureTrait(name="avg_shot_ms", mode="require_ge", value=1000, enforce="strict")])
        )
        issues = _check_type_conformance(_timeline([2.0]), _Ctx("trait_demo"))
        assert len(issues) == 1
        assert issues[0].severity == "info"
        assert "未做 LLM 同一性复核" in issues[0].message

    def test_unknown_type_is_noop(self) -> None:
        assert _check_type_conformance(_timeline([2.0]), _Ctx("missing")) == []

    def test_timeline_none_is_noop(self) -> None:
        CategoryRegistry.register(_TraitsPlugin())
        assert _check_type_conformance(None, _Ctx("trait_demo")) == []
