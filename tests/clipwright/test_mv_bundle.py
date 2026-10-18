"""工程包与交付打包单测（PRD C12 / R-18 / R-19，验收 V32/V37）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clipwright.schema.category_spec import (
    MaterialStrategy,
    PlanDocument,
    PlanSection,
)
from clipwright.services.mv_bundle import (
    DIST_FILES,
    load_bundle,
    pack_dist,
    render_assets_gap,
    render_credits,
    render_selftest,
    render_storyboard,
    validate_bundle,
)
from tools.check_sync import check_sync


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _make_bundle(root: Path, *, with_plan: bool = True) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _write(
        root / "mv.json",
        {
            "id": "demo_mv",
            "song": {"bpm": 130, "beat_zero_sec": 0.344, "preroll_sec": 5, "duration_sec": 6},
            "segments": [
                {"id": "A", "t0": 0, "t1": 2, "kind": "chat", "scene": "01_boot"},
                {"id": "B", "t0": 2, "t1": 6, "kind": "plate", "scene": "02_plate"},
            ],
            "fonts": {"serif": "Noto Serif TC", "mono": "JetBrains Mono"},
            "gates": {"determinism": True, "beat": True, "flash": True},
        },
    )
    _write(root / "cues.json", {"grid": {"bpm": 130, "beat_zero_sec": 0.344}, "items": [
        {"name": "line_0", "t": 1.267, "source": "init", "snapped": "half", "note": "text(0)"},
        {"name": "line_1", "t": 4.267, "source": "measured", "snapped": "half"},
    ]})
    _write(root / "report.json", {
        "deterministic": True, "flash_violations": [], "frames": 180, "render_sec": 25.7,
    })
    _write(root / "brief.json", {"title": "示例", "core_message": "记住这句话"})
    if with_plan:
        plan = PlanDocument(
            type="lyric_mv",
            target_duration_sec=6,
            sections=[PlanSection(id="A", t0=0, t1=6, purpose="建立舞台")],
            material_strategy=MaterialStrategy(gaps=["手部特写 x2"]),
        )
        _write(root / "plan.json", plan.model_dump())
    return root


class TestLoadBundle:
    def test_loads_all_parts(self, tmp_path) -> None:
        _make_bundle(tmp_path)
        bundle = load_bundle(tmp_path)
        assert bundle.mv_id == "demo_mv"
        assert bundle.cues is not None and len(bundle.cues.items) == 2
        assert bundle.plan is not None
        assert bundle.report["frames"] == 180

    def test_missing_required_files_reported(self, tmp_path) -> None:
        bundle = load_bundle(tmp_path)
        assert "mv.json" in bundle.missing and "cues.json" in bundle.missing

    def test_bad_cues_recorded_not_raised(self, tmp_path) -> None:
        _make_bundle(tmp_path)
        (tmp_path / "cues.json").write_text("{not json", encoding="utf-8")
        bundle = load_bundle(tmp_path)
        assert any("parse" in name for name in bundle.missing)
        assert bundle.cues is None

    def test_missing_dir_raises(self, tmp_path) -> None:
        with pytest.raises(FileNotFoundError):
            load_bundle(tmp_path / "nope")

    def test_gates_default_true(self, tmp_path) -> None:
        bundle = load_bundle(tmp_path)
        assert bundle.gates() == {"determinism": True, "beat": True, "flash": True}


class TestValidateBundle:
    def test_clean_bundle_passes(self, tmp_path) -> None:
        _make_bundle(tmp_path)
        assert validate_bundle(load_bundle(tmp_path)) == []

    def test_missing_file_reported(self, tmp_path) -> None:
        _make_bundle(tmp_path)
        (tmp_path / "cues.json").unlink()
        codes = [issue["code"] for issue in validate_bundle(load_bundle(tmp_path))]
        assert "bundle_missing_file" in codes

    def test_segment_gap_detected(self, tmp_path) -> None:
        _make_bundle(tmp_path)
        payload = json.loads((tmp_path / "mv.json").read_text(encoding="utf-8"))
        payload["segments"][1]["t0"] = 3.0
        _write(tmp_path / "mv.json", payload)
        codes = [issue["code"] for issue in validate_bundle(load_bundle(tmp_path))]
        assert "bundle_segment_gap" in codes

    def test_bad_segment_detected(self, tmp_path) -> None:
        _make_bundle(tmp_path)
        payload = json.loads((tmp_path / "mv.json").read_text(encoding="utf-8"))
        payload["segments"][0]["t1"] = 0
        _write(tmp_path / "mv.json", payload)
        codes = [issue["code"] for issue in validate_bundle(load_bundle(tmp_path))]
        assert "bundle_bad_segment" in codes

    def test_zero_bpm_detected(self, tmp_path) -> None:
        _make_bundle(tmp_path)
        _write(tmp_path / "cues.json", {"grid": {"bpm": 0}, "items": []})
        codes = [issue["code"] for issue in validate_bundle(load_bundle(tmp_path))]
        assert "bundle_no_grid" in codes


class TestRenderers:
    def test_storyboard_lists_segments_and_cues(self, tmp_path) -> None:
        _make_bundle(tmp_path)
        text = render_storyboard(load_bundle(tmp_path))
        assert "| A |" in text and "02_plate" in text
        assert "line_0" in text and "init" in text

    def test_storyboard_deterministic(self, tmp_path) -> None:
        _make_bundle(tmp_path)
        bundle = load_bundle(tmp_path)
        assert render_storyboard(bundle) == render_storyboard(bundle)

    def test_selftest_reports_missing_numbers_loudly(self, tmp_path) -> None:
        _make_bundle(tmp_path)
        (tmp_path / "report.json").unlink()
        text = render_selftest(load_bundle(tmp_path))
        assert "未提供" in text
        assert "仍需人工确认" in text

    def test_assets_gap_from_plan(self, tmp_path) -> None:
        _make_bundle(tmp_path)
        assert "手部特写 x2" in render_assets_gap(load_bundle(tmp_path))

    def test_assets_gap_fallback(self, tmp_path) -> None:
        assert "未登记" in render_assets_gap(load_bundle(tmp_path))

    def test_credits_lists_fonts(self, tmp_path) -> None:
        _make_bundle(tmp_path)
        text = render_credits(load_bundle(tmp_path))
        assert "Noto Serif TC" in text and "OFL" in text


class TestPackDist:
    def test_pack_writes_expected_files(self, tmp_path) -> None:
        bundle_dir = _make_bundle(tmp_path / "bundle")
        result = pack_dist(load_bundle(bundle_dir), tmp_path / "dist")
        for name in ("storyboard.md", "cues.json", "plan.md", "brief.md",
                     "report.json", "selftest.md", "assets_gap.md", "CREDITS.md"):
            assert name in result["files"], name
            assert (tmp_path / "dist" / name).exists()
        assert result["missing"] == ["preview.mp4"]

    def test_pack_copies_preview_when_given(self, tmp_path) -> None:
        bundle_dir = _make_bundle(tmp_path / "bundle")
        preview = tmp_path / "preview_1080p60.mp4"
        preview.write_bytes(b"fake")
        result = pack_dist(load_bundle(bundle_dir), tmp_path / "dist", preview=preview)
        assert "preview.mp4" in result["files"]
        assert result["missing"] == []

    def test_pack_without_plan_skips_plan_md(self, tmp_path) -> None:
        bundle_dir = _make_bundle(tmp_path / "bundle", with_plan=False)
        result = pack_dist(load_bundle(bundle_dir), tmp_path / "dist")
        assert "plan.md" not in result["files"]
        assert "plan.md" in result["missing"]

    def test_dist_file_list_documented(self) -> None:
        assert "storyboard.md" in DIST_FILES and "selftest.md" in DIST_FILES


class TestCheckSync:
    def test_exact_match_passes(self) -> None:
        assert check_sync(10.0, 10.0).ok

    def test_preroll_expected_in_video(self) -> None:
        report = check_sync(15.0, 10.0, preroll_sec=5.0)
        assert report.ok
        assert report.expected_video_sec == pytest.approx(15.0)

    def test_missing_preroll_fails(self) -> None:
        report = check_sync(10.0, 10.0, preroll_sec=5.0)
        assert not report.ok
        assert report.delta_sec == pytest.approx(-5.0)
        assert "不同步" in report.summary()

    def test_tolerance_boundary(self) -> None:
        assert check_sync(10.04, 10.0).ok
        assert not check_sync(10.06, 10.0).ok

    def test_negative_values_rejected(self) -> None:
        with pytest.raises(ValueError):
            check_sync(-1.0, 10.0)

    def test_as_dict_rounds(self) -> None:
        payload = check_sync(10.0, 9.999).as_dict()
        assert payload["ok"] is True
        assert payload["delta_sec"] == round(0.001, 3)
