"""#9 成片时长校验 + #10 HyperFrames 稳定性/Windows 专项参数。

- #9：预期时长 = 时间线时长 − 转场累计侵蚀；超容差（max(0.3s, 1%)）记 warning，
  `CLIPWRIGHT_STRICT_DURATION=1` 时直接判失败（CI 验收用）。
- #10：`--frames-cache-dir` / 三类超时 / `--low-memory-mode` / `--browser-gpu`
  全部由环境变量驱动，缺省一个 flag 都不加（行为零变化）。
"""

from __future__ import annotations

import asyncio

import pytest

from clipwright.animation.hyperframes_renderer import HyperframesRenderer
from clipwright.services.render import RenderService


class TestExpectedDuration:
    def test_no_transition_equals_timeline(self) -> None:
        assert RenderService.expected_output_duration(15.0) == pytest.approx(15.0)
        assert RenderService.expected_output_duration(15.0, [], transitions_applied=True) == pytest.approx(15.0)

    def test_transition_erosion_deducted(self) -> None:
        # 3 段视频、后两段各 0.5s 转场 → 累计侵蚀 1.0s
        segs = [
            {"start_sec": 0.0},
            {"start_sec": 5.0, "transition_in": "fade", "transition_duration_sec": 0.5},
            {"start_sec": 10.0, "transition_in": "wipeleft", "transition_duration_sec": 0.5},
        ]
        assert RenderService.expected_output_duration(15.0, segs, transitions_applied=True) == pytest.approx(14.0)

    def test_erosion_ignored_when_hard_cut(self) -> None:
        """未走 xfade（硬切拼接）时不做扣减——转场字段存在但没生效。"""
        segs = [{}, {"transition_duration_sec": 0.5}]
        assert RenderService.expected_output_duration(15.0, segs, transitions_applied=False) == pytest.approx(15.0)

    def test_zero_or_invalid_inputs(self) -> None:
        assert RenderService.expected_output_duration(0) == 0.0
        assert RenderService.expected_output_duration("bad") == 0.0  # type: ignore[arg-type]

    def test_erosion_never_negative_duration(self) -> None:
        segs = [{}, {"transition_duration_sec": 5.0}]
        assert RenderService.expected_output_duration(1.0, segs, transitions_applied=True) == 0.0

    def test_tolerates_garbage_segment_values(self) -> None:
        segs = [{}, {"transition_duration_sec": "x"}, {"transition_duration_sec": None}]
        assert RenderService.expected_output_duration(10.0, segs, transitions_applied=True) == pytest.approx(10.0)


class TestDurationDeviation:
    def test_within_tolerance_returns_none(self) -> None:
        # 容差 = max(0.3, 1%)；14.0 → 0.3s
        assert RenderService.duration_deviation(14.2, 14.0) is None
        # 转场侵蚀被正确扣除后不再误报（旧实现拿原始 duration 比对会误报 1.0s）
        assert RenderService.duration_deviation(14.0, 14.0) is None

    def test_beyond_tolerance_returns_diff(self) -> None:
        assert RenderService.duration_deviation(13.0, 14.0) == pytest.approx(1.0)
        assert RenderService.duration_deviation(16.0, 14.0) == pytest.approx(2.0)

    def test_long_video_uses_one_percent(self) -> None:
        # 120s → 容差 1.2s
        assert RenderService.duration_deviation(121.0, 120.0) is None
        assert RenderService.duration_deviation(122.5, 120.0) == pytest.approx(2.5)

    def test_zero_expected_skips_check(self) -> None:
        assert RenderService.duration_deviation(10.0, 0.0) is None
        assert RenderService.duration_deviation("bad", 10.0) is None  # type: ignore[arg-type]


class TestExtraRenderFlags:
    def _flags(self, monkeypatch, **env) -> list[str]:
        for key in (
            "CLIPWRIGHT_HF_FRAMES_CACHE_DIR", "CLIPWRIGHT_HF_BROWSER_TIMEOUT",
            "CLIPWRIGHT_HF_PROTOCOL_TIMEOUT", "CLIPWRIGHT_HF_PLAYER_READY_TIMEOUT",
            "CLIPWRIGHT_HF_LOW_MEMORY", "CLIPWRIGHT_HF_BROWSER_GPU",
        ):
            monkeypatch.delenv(key, raising=False)
        for key, val in env.items():
            monkeypatch.setenv(key, val)
        return HyperframesRenderer._extra_render_flags()

    def test_default_adds_nothing(self, monkeypatch) -> None:
        assert self._flags(monkeypatch) == []

    def test_frames_cache_dir(self, monkeypatch) -> None:
        flags = self._flags(monkeypatch, CLIPWRIGHT_HF_FRAMES_CACHE_DIR=r"D:\hf_cache")
        assert flags == ["--frames-cache-dir", r"D:\hf_cache"]

    def test_timeouts(self, monkeypatch) -> None:
        flags = self._flags(
            monkeypatch,
            CLIPWRIGHT_HF_BROWSER_TIMEOUT="120",
            CLIPWRIGHT_HF_PROTOCOL_TIMEOUT="600000",
            CLIPWRIGHT_HF_PLAYER_READY_TIMEOUT="90000",
        )
        assert flags == [
            "--browser-timeout", "120",
            "--protocol-timeout", "600000",
            "--player-ready-timeout", "90000",
        ]

    def test_low_memory_and_gpu(self, monkeypatch) -> None:
        assert self._flags(monkeypatch, CLIPWRIGHT_HF_LOW_MEMORY="1") == ["--low-memory-mode"]
        assert self._flags(monkeypatch, CLIPWRIGHT_HF_BROWSER_GPU="0") == ["--no-browser-gpu"]
        assert self._flags(monkeypatch, CLIPWRIGHT_HF_BROWSER_GPU="true") == ["--browser-gpu"]

    def test_flags_reach_render_command(self, monkeypatch, tmp_path) -> None:
        captured: list[list[str]] = []

        class _R:
            returncode = 0
            stdout = b""
            stderr = b""

        async def _run(cmd, **_kw):
            captured.append([str(c) for c in cmd])
            return _R()

        monkeypatch.setenv("CLIPWRIGHT_HF_FRAMES_CACHE_DIR", "D:/hf_cache")
        monkeypatch.setenv("CLIPWRIGHT_HF_LOW_MEMORY", "1")
        asyncio.run(HyperframesRenderer.render_mg_html_to_mov(
            "<html></html>", str(tmp_path), str(tmp_path / "out.mov"), fps=30,
            run_cmd=_run, validate=lambda _p: True,
        ))
        cmd = captured[0]
        assert "--frames-cache-dir" in cmd and cmd[cmd.index("--frames-cache-dir") + 1] == "D:/hf_cache"
        assert "--low-memory-mode" in cmd
