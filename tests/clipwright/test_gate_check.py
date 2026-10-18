"""门 4 预演脚本单测（V21/V22/V26/V37/V42/V59 + 人工项提醒）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.gate_check import MANUAL_ITEMS, run_gate_check


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _bundle(root: Path, report: dict | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _write(root / "mv.json", {
        "id": "gate_demo",
        "segments": [{"id": "A", "t0": 0, "t1": 6, "kind": "plate", "scene": "s1"}],
    })
    _write(root / "cues.json", {"grid": {"bpm": 130, "beat_zero_sec": 0.344}, "items": []})
    _write(root / "report.json", report or {
        "deterministic": True, "flash_violations": [], "frames": 180, "frames_ok": True,
    })
    return root


def _dist(root: Path, *, with_plan: bool = True, gap: str = "- 手部特写 x2") -> Path:
    from clipwright.services.mv_bundle import DIST_FILES

    root.mkdir(parents=True, exist_ok=True)
    for name in DIST_FILES:
        if name == "plan.md" and not with_plan:
            continue
        (root / name).write_text(gap if name == "assets_gap.md" else "x", encoding="utf-8")
    return root


class TestGateCheck:
    def test_all_pass(self, tmp_path) -> None:
        report = run_gate_check(_bundle(tmp_path / "b"), _dist(tmp_path / "d"))
        assert report.ok, report.summary()
        ids = {c.id for c in report.checks}
        assert {"V21", "V22", "V26", "V37", "V42"} <= ids

    def test_non_deterministic_fails(self, tmp_path) -> None:
        report = run_gate_check(
            _bundle(tmp_path / "b", {"deterministic": False, "flash_violations": [], "frames": 10}),
            _dist(tmp_path / "d"),
        )
        assert not report.ok
        assert "V22" in [c.id for c in report.checks if not c.ok]

    def test_flash_violation_fails(self, tmp_path) -> None:
        report = run_gate_check(
            _bundle(tmp_path / "b", {"deterministic": True, "flash_violations": [{"second": 3}], "frames": 10}),
            _dist(tmp_path / "d"),
        )
        assert not report.ok
        assert "V26" in report.as_dict()["failed"]

    def test_no_frames_fails(self, tmp_path) -> None:
        report = run_gate_check(
            _bundle(tmp_path / "b", {"deterministic": True, "flash_violations": [], "frames": 0}),
            _dist(tmp_path / "d"),
        )
        assert "V21" in report.as_dict()["failed"]

    def test_missing_dist_file_fails(self, tmp_path) -> None:
        dist = _dist(tmp_path / "d")
        (dist / "storyboard.md").unlink()
        report = run_gate_check(_bundle(tmp_path / "b"), dist)
        assert "V37" in report.as_dict()["failed"]

    def test_plan_meets_dual_document_check(self, tmp_path) -> None:
        report = run_gate_check(_bundle(tmp_path / "b"), _dist(tmp_path / "d", with_plan=True))
        assert "V59" in [c.id for c in report.checks if c.ok]

    def test_placeholder_gap_fails(self, tmp_path) -> None:
        report = run_gate_check(_bundle(tmp_path / "b"), _dist(tmp_path / "d", gap="未登记"))
        assert "V42" in report.as_dict()["failed"]

    def test_beat_hit_rate_checked_when_present(self, tmp_path) -> None:
        report = run_gate_check(
            _bundle(tmp_path / "b", {
                "deterministic": True, "flash_violations": [], "frames": 10, "beat_hit_rate": 0.9,
            }),
            _dist(tmp_path / "d"),
        )
        assert "V24" in report.as_dict()["failed"]

    def test_sync_checked_when_durations_present(self, tmp_path) -> None:
        report = run_gate_check(
            _bundle(tmp_path / "b", {
                "deterministic": True, "flash_violations": [], "frames": 10,
                "video_sec": 10.0, "audio_sec": 10.0, "preroll_sec": 5.0,
            }),
            _dist(tmp_path / "d"),
        )
        assert "V32" in report.as_dict()["failed"]

    def test_manual_items_listed(self, tmp_path) -> None:
        report = run_gate_check(_bundle(tmp_path / "b"))
        ids = [item["id"] for item in report.manual]
        assert "V41" in ids and "V20" in ids
        assert len(MANUAL_ITEMS) == len(ids)

    def test_works_without_dist(self, tmp_path) -> None:
        report = run_gate_check(_bundle(tmp_path / "b"))
        assert report.ok
        assert "V37" not in [c.id for c in report.checks]

    def test_summary_marks_failures(self, tmp_path) -> None:
        report = run_gate_check(
            _bundle(tmp_path / "b", {"deterministic": False, "flash_violations": [], "frames": 1})
        )
        assert "不通过" in report.summary()
