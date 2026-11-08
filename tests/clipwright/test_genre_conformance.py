"""指标采集与同一性校验单测（PRD F12 / A-09，验收 V16/V17）。"""

from __future__ import annotations

import pytest

from clipwright.schema.category_spec import SignatureTrait
from clipwright.schema.timeline import Clip, ClipKind, Timeline, Track
from clipwright.services.genre_conformance import (
    STATUS_SKIP,
    check_conformance,
    evaluate_traits,
)
from clipwright.services.mv_cues import build_grid
from clipwright.services.trait_collector import KNOWN_TRAITS, collect_traits


def _clip(kind: ClipKind, start: float, duration: float, asset: str = "", **kwargs) -> Clip:
    return Clip(
        id=f"c{start}-{asset}",
        kind=kind,
        asset_id=asset,
        track_id="t",
        start_sec=start,
        duration_sec=duration,
        source_offset_sec=0,
        speed=1,
        volume=1,
        opacity=1,
        keyframes=kwargs.pop("keyframes", []),
        **kwargs,
    )


def _timeline(clips: list[Clip], text: list[Clip] | None = None, images: list[Clip] | None = None) -> Timeline:
    tracks = [Track(id="v", name="v", kind=ClipKind.VIDEO, index=0, clips=clips)]
    if text:
        tracks.append(Track(id="t", name="t", kind=ClipKind.TEXT, index=1, clips=text))
    if images:
        tracks.append(Track(id="i", name="i", kind=ClipKind.IMAGE, index=2, clips=images))
    return Timeline(id="tl", width=1920, height=1080, fps=30, duration_sec=10, tracks=tracks)


class TestCollectTraits:
    def test_all_known_traits_present(self) -> None:
        traits = collect_traits(_timeline([_clip(ClipKind.VIDEO, 0, 2, "a")]))
        assert set(KNOWN_TRAITS) <= set(traits)
        assert all(isinstance(v, float) for v in traits.values())

    def test_avg_and_base_shot_ms(self) -> None:
        clips = [
            _clip(ClipKind.VIDEO, 0, 2, "a"),
            _clip(ClipKind.VIDEO, 2, 4, "b"),
            _clip(ClipKind.VIDEO, 6, 4, "c"),
        ]
        traits = collect_traits(_timeline(clips))
        assert traits["avg_shot_ms"] == pytest.approx((2000 + 4000 + 4000) / 3)
        assert traits["base_shot_ms"] == pytest.approx(4000)

    def test_speed_affects_effective_duration(self) -> None:
        clip = _clip(ClipKind.VIDEO, 0, 4, "a")
        clip.speed = 2.0
        assert collect_traits(_timeline([clip]))["avg_shot_ms"] == pytest.approx(2000)

    def test_repeat_shot_ratio_counts_duplicates(self) -> None:
        clips = [
            _clip(ClipKind.VIDEO, 0, 1, "loop"),
            _clip(ClipKind.VIDEO, 1, 1, "loop"),
            _clip(ClipKind.VIDEO, 2, 1, "loop"),
            _clip(ClipKind.VIDEO, 3, 1, "other"),
        ]
        assert collect_traits(_timeline(clips))["repeat_shot_ratio"] == pytest.approx(0.5)

    def test_empty_timeline_is_all_zero(self) -> None:
        traits = collect_traits(_timeline([]))
        assert all(value == 0.0 for value in traits.values())

    def test_text_overlay_ratio(self) -> None:
        timeline = _timeline(
            [_clip(ClipKind.VIDEO, 0, 10, "a")],
            text=[_clip(ClipKind.TEXT, 0, 2, "", text="hi")],
        )
        assert collect_traits(timeline)["text_overlay_ratio"] == pytest.approx(0.2)

    def test_illustration_ratio(self) -> None:
        timeline = _timeline(
            [_clip(ClipKind.VIDEO, 0, 10, "a")],
            images=[_clip(ClipKind.IMAGE, 0, 5)],
        )
        assert collect_traits(timeline)["illustration_clip_ratio"] == pytest.approx(0.5)

    def test_chart_count_and_segments_from_scenes(self) -> None:
        scenes = [
            {"title": "开场", "description": "口播"},
            {"title": "数据", "description": "[逻辑动画]mg_dynamic:{}"},
            {"title": "对比", "description": "跑分对比"},
        ]
        traits = collect_traits(_timeline([]), scenes=scenes)
        assert traits["chart_count"] == 1
        assert traits["argument_segment_count"] == 3

    def test_beat_align_ratio_uses_grid(self) -> None:
        clips = [
            _clip(ClipKind.VIDEO, 0, 0.5, "a"),
            _clip(ClipKind.VIDEO, 0.5, 0.5, "b"),
            _clip(ClipKind.VIDEO, 1.0, 0.5, "c"),
        ]
        grid = build_grid(120, 0.0)
        assert collect_traits(_timeline(clips), grid=grid)["beat_align_ratio"] == pytest.approx(1.0)

    def test_keyframe_density(self) -> None:
        clip = _clip(ClipKind.VIDEO, 0, 10, "a", keyframes=[{}, {}, {}, {}])
        assert collect_traits(_timeline([clip]))["keyframe_density"] == pytest.approx(0.4)


class TestEvaluateTraits:
    def test_require_ge_pass_and_fail(self) -> None:
        traits = [SignatureTrait(name="chart_count", mode="require_ge", value=1)]
        assert evaluate_traits(traits, {"chart_count": 2})[0].passed
        assert not evaluate_traits(traits, {"chart_count": 0})[0].passed

    def test_require_le_and_between(self) -> None:
        le = SignatureTrait(name="avg_shot_ms", mode="require_le", value=1500)
        assert evaluate_traits([le], {"avg_shot_ms": 1200})[0].passed
        between = SignatureTrait(name="chart_count", mode="require_between", value=1, value_max=3)
        assert evaluate_traits([between], {"chart_count": 2})[0].passed
        assert not evaluate_traits([between], {"chart_count": 5})[0].passed

    def test_missing_metric_defaults_to_zero(self) -> None:
        result = evaluate_traits(
            [SignatureTrait(name="chart_count", mode="require_ge", value=1)], {}
        )[0]
        assert result.value == 0.0
        assert not result.passed


class TestConformance:
    def test_strict_failure_blocks(self) -> None:
        report = check_conformance(
            [SignatureTrait(name="cut_on_beat_ratio", mode="require_ge", value=0.95, enforce="strict")],
            {"cut_on_beat_ratio": 0.5},
        )
        assert not report.ok
        assert report.failed_strict
        assert report.mismatches

    def test_soft_failure_warns_but_passes(self) -> None:
        report = check_conformance(
            [SignatureTrait(name="avg_shot_ms", mode="require_ge", value=2000, enforce="soft")],
            {"avg_shot_ms": 1000},
        )
        assert report.ok
        assert report.warnings and not report.mismatches

    def test_judge_skipped_by_default(self) -> None:
        """D7：默认关裁判，且必须显式标 skipped。"""
        report = check_conformance([], {})
        assert report.judge == STATUS_SKIP
        assert "默认关闭" in report.judge_detail["reason"]
        assert "未做 LLM 同一性复核" in report.summary()

    def test_judge_enabled_without_callback_still_skipped(self) -> None:
        report = check_conformance([], {}, enable_genre_judge=True)
        assert report.judge == STATUS_SKIP
        assert "未注入" in report.judge_detail["reason"]

    def test_judge_mismatch_marks_not_ok(self) -> None:
        report = check_conformance(
            [],
            {},
            enable_genre_judge=True,
            judge=lambda traits, measured, report: {"mismatch": True, "reasons": ["像知识区"]},
        )
        assert report.judge == "mismatch"
        assert not report.ok
        assert "像知识区" in report.mismatches[0]

    def test_judge_exception_degrades(self) -> None:
        def boom(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("llm down")

        report = check_conformance([], {}, enable_genre_judge=True, judge=boom)
        assert report.judge == STATUS_SKIP
        assert "已降级" in report.judge_detail["reason"]

    def test_summary_reports_counts(self) -> None:
        traits = [
            SignatureTrait(name="chart_count", mode="require_ge", value=1),
            SignatureTrait(name="avg_shot_ms", mode="require_ge", value=2000),
        ]
        report = check_conformance(traits, {"chart_count": 2, "avg_shot_ms": 3000})
        assert "类型符合度 2/2" in report.summary()

    def test_end_to_end_from_timeline(self) -> None:
        """鬼畜式指标：高重复 + 短镜头 → 通过；知识区指标 → 不通过。"""
        clips = [
            _clip(ClipKind.VIDEO, i * 1.0, 1.0, "loop" if i % 2 == 0 else "x")
            for i in range(6)
        ]
        measured = collect_traits(_timeline(clips))
        kichiku = check_conformance(
            [
                SignatureTrait(name="avg_shot_ms", mode="require_le", value=1500, enforce="strict"),
                SignatureTrait(name="repeat_shot_ratio", mode="require_ge", value=0.2, enforce="strict"),
            ],
            measured,
        )
        assert kichiku.ok, kichiku.mismatches
        knowledge = check_conformance(
            [SignatureTrait(name="avg_shot_ms", mode="require_ge", value=2000, enforce="strict")],
            measured,
        )
        assert not knowledge.ok
