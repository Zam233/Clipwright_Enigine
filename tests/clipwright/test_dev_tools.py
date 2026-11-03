"""开发期验收工具单测（R-02 确定性 lint / R-03 逐帧哈希 / R-16 闪光检测）。"""

from __future__ import annotations

import numpy as np
import pytest

from tools.check_flash import (
    FrameMetric,
    analyze,
    frame_metrics,
    large_area_flip,
)
from tools.hash_frames import compare, hash_frames_from_raw
from tools.lint_determinism import scan_paths, scan_text


class TestDeterminismLint:
    def test_math_random_is_flagged(self) -> None:
        hits = scan_text("const x = Math.random();")
        assert hits and "Math.random" in hits[0].hint

    def test_timers_are_flagged(self) -> None:
        hits = scan_text("setTimeout(() => {}, 100);\nsetInterval(f, 16);\nrequestAnimationFrame(f);")
        assert len(hits) == 3

    def test_date_and_fetch_flagged(self) -> None:
        hits = scan_text("const t = Date.now();\nawait fetch('x');\nnew Date();")
        assert len(hits) == 3

    def test_clean_scene_passes(self) -> None:
        code = "function renderFrame(t){ return Math.sin(t) * 0.5; }"
        assert scan_text(code) == []

    def test_waiver_line_is_skipped(self) -> None:
        code = "const t = Date.now(); // deterministic-ok: 仅用于日志"
        assert scan_text(code) == []

    def test_scan_paths_reads_files(self, tmp_path) -> None:
        scene = tmp_path / "scene.js"
        scene.write_text("Math.random();\n", encoding="utf-8")
        other = tmp_path / "notes.txt"
        other.write_text("Math.random();\n", encoding="utf-8")
        hits = scan_paths([tmp_path])
        assert len(hits) == 1
        assert hits[0].path.endswith("scene.js")

    def test_hit_render_contains_location(self) -> None:
        hit = scan_text("let a=1;\nMath.random();", "scene.js")[0]
        assert "scene.js:2" in hit.render()


class TestFrameHash:
    def test_hash_frames_from_raw_splits_and_hashes(self) -> None:
        data = b"\x00" * 6 + b"\xff" * 6
        digests = hash_frames_from_raw(data, 6)
        assert len(digests) == 2
        assert digests[0] != digests[1]

    def test_trailing_partial_frame_ignored(self) -> None:
        assert len(hash_frames_from_raw(b"\x00" * 7, 6)) == 1

    def test_compare_equal(self) -> None:
        result = compare(["a", "b"], ["a", "b"])
        assert result.equal and result.first_diff is None

    def test_compare_diff(self) -> None:
        result = compare(["a", "b"], ["a", "c"])
        assert not result.equal
        assert result.first_diff == 1
        assert "差异" in result.summary()

    def test_hash_video_scales_when_size_differs(self, tmp_path) -> None:
        """真机回归：给错宽高必须缩放解码，帧数要等于真实帧数（不是 4 倍）。"""
        import shutil
        import subprocess

        if shutil.which("ffmpeg") is None:
            pytest.skip("未安装 ffmpeg")
        from tools.hash_frames import hash_video

        clip = tmp_path / "clip.mp4"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
             "color=c=black:s=64x36:d=1:r=10", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)],
            check=True, capture_output=True,
        )
        frames_native = hash_video(clip, 64, 36)
        frames_scaled = hash_video(clip, 32, 18)
        # 900 帧不该被切成 3600 块：两种粒度都必须是 10 帧
        assert len(frames_native) == 10, len(frames_native)
        assert len(frames_scaled) == 10, len(frames_scaled)

    def test_compare_length_mismatch(self) -> None:
        result = compare(["a"], ["a", "b"])
        assert not result.equal
        assert result.diffs == [1]


def _metric(mean_luma: float, bright: float = 0.0, dark: float = 0.0) -> FrameMetric:
    return FrameMetric(mean_luma=mean_luma, bright_ratio=bright, dark_ratio=dark)


class TestFlashDetection:
    """检测器必须先通过"已知答案"校准：故意闪的片必须报违规。"""

    def test_static_frame_has_no_violation(self) -> None:
        report = analyze([_metric(0.3)] * 60, fps=60.0)
        assert report.ok

    def test_known_4hz_flicker_is_flagged(self) -> None:
        """4Hz 黑白交替（每秒 8 次翻转）必须被判定违规。"""
        frames = []
        for index in range(120):  # 2 秒 @60fps
            frames.append(_metric(0.95, bright=0.9) if (index // 8) % 2 == 0 else _metric(0.05, dark=0.9))
        report = analyze(frames, fps=60.0)
        assert not report.ok
        assert report.violations[0]["flips"] > 3

    def test_two_flips_per_second_is_allowed(self) -> None:
        frames = []
        for index in range(120):
            # 每 30 帧换一次（每秒 2 次翻转）
            frames.append(_metric(0.9, bright=0.9) if (index // 30) % 2 == 0 else _metric(0.1, dark=0.9))
        assert analyze(frames, fps=60.0).ok

    def test_small_area_change_is_not_a_flip(self) -> None:
        assert not large_area_flip(_metric(0.1), _metric(0.9, bright=0.05, dark=0.02))

    def test_small_amplitude_change_is_not_a_flip(self) -> None:
        assert not large_area_flip(_metric(0.5, bright=0.5), _metric(0.55, bright=0.5))

    def test_frame_metrics_from_rgb(self) -> None:
        white = np.full((10, 10, 3), 255, dtype=np.uint8)
        black = np.zeros((10, 10, 3), dtype=np.uint8)
        assert frame_metrics(white).bright_ratio == 1.0
        assert frame_metrics(black).dark_ratio == 1.0
        assert frame_metrics(white).mean_luma > 0.99

    def test_frame_metrics_accepts_grey(self) -> None:
        assert 0.0 <= frame_metrics(np.zeros((8, 8), dtype=np.uint8)).mean_luma <= 1.0

    def test_report_summary_mentions_violation_second(self) -> None:
        frames = [_metric(0.95, bright=0.9) if (i // 8) % 2 == 0 else _metric(0.05, dark=0.9) for i in range(60)]
        report = analyze(frames, fps=60.0)
        assert "不通过" in report.summary()
