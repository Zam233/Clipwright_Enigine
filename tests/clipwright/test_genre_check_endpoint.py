"""A-10：`/genre-check` 干跑逻辑单测（PRD F12 / V16）。"""

from __future__ import annotations

import pytest

from clipwright.api.type_maker import run_genre_check
from clipwright.category.base import BaseCategoryPlugin
from clipwright.category.registry import CategoryRegistry
from clipwright.schema.category_spec import SignatureTrait


class _TraitsPlugin(BaseCategoryPlugin):
    plugin_id = "gc_demo"
    display_name = "GC"

    def __init__(self, traits: list[SignatureTrait] | None = None) -> None:
        self._traits = traits or []

    def translate_persona(self, params):  # type: ignore[no-untyped-def]
        return {}

    def get_shot_params(self, translated):  # type: ignore[no-untyped-def]
        return {}

    def get_signature_traits(self):  # type: ignore[no-untyped-def]
        return self._traits


def _timeline_payload(durations: list[float]) -> dict:
    clips = []
    start = 0.0
    for index, duration in enumerate(durations):
        clips.append(
            {
                "id": f"c{index}",
                "kind": "video",
                "asset_id": f"a{index}",
                "track_id": "v",
                "start_sec": start,
                "duration_sec": duration,
                "source_offset_sec": 0,
                "speed": 1,
                "volume": 1,
                "opacity": 1,
                "keyframes": [],
            }
        )
        start += duration
    return {
        "id": "tl",
        "width": 1920,
        "height": 1080,
        "fps": 30,
        "duration_sec": start,
        "tracks": [{"id": "v", "name": "v", "kind": "video", "index": 0, "clips": clips}],
    }


@pytest.fixture(autouse=True)
def _isolate():
    CategoryRegistry.clear()
    yield
    CategoryRegistry.clear()


class TestGenreCheck:
    def test_missing_timeline_rejected(self) -> None:
        with pytest.raises(ValueError):
            run_genre_check("gc_demo", {})

    def test_invalid_timeline_rejected(self) -> None:
        with pytest.raises(ValueError):
            run_genre_check("gc_demo", {"timeline": {"id": 1}})

    def test_no_traits_reports_zero_declared(self) -> None:
        CategoryRegistry.register(_TraitsPlugin())
        result = run_genre_check("gc_demo", {"timeline": _timeline_payload([2.0])})
        assert result["traits_declared"] == 0
        assert result["ok"] is True
        assert result["judge"] == "skipped"

    def test_strict_failure_reported(self) -> None:
        CategoryRegistry.register(
            _TraitsPlugin(
                [SignatureTrait(name="avg_shot_ms", mode="require_ge", value=2000, enforce="strict")]
            )
        )
        result = run_genre_check("gc_demo", {"timeline": _timeline_payload([1.0])})
        assert result["ok"] is False
        assert result["mismatches"]
        assert result["measured"]["avg_shot_ms"] == pytest.approx(1000)

    def test_fast_cut_type_passes(self) -> None:
        CategoryRegistry.register(
            _TraitsPlugin(
                [SignatureTrait(name="avg_shot_ms", mode="require_le", value=1500, enforce="strict")]
            )
        )
        result = run_genre_check("gc_demo", {"timeline": _timeline_payload([1.0, 1.0])})
        assert result["ok"] is True

    def test_bpm_enables_beat_metric(self) -> None:
        CategoryRegistry.register(
            _TraitsPlugin(
                [SignatureTrait(name="beat_align_ratio", mode="require_ge", value=1.0, enforce="strict")]
            )
        )
        payload = {"timeline": _timeline_payload([0.5, 0.5]), "bpm": 120, "beat_zero_sec": 0.0}
        result = run_genre_check("gc_demo", payload)
        assert result["measured"]["beat_align_ratio"] == pytest.approx(1.0)
        assert result["ok"] is True

    def test_unknown_type_is_noop(self) -> None:
        result = run_genre_check("nope", {"timeline": _timeline_payload([2.0])})
        assert result["traits_declared"] == 0
        assert result["ok"] is True

    def test_summary_mentions_no_llm_review(self) -> None:
        CategoryRegistry.register(_TraitsPlugin())
        result = run_genre_check("gc_demo", {"timeline": _timeline_payload([2.0])})
        assert "未做 LLM 同一性复核" in result["summary"]
