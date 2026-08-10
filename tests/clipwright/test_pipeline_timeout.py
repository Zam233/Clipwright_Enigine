"""B7: 超时公式统一 + SSE max_wall 动态化。"""

from __future__ import annotations

from clipwright.api.pipeline import pipeline_timeout_sec


class TestPipelineTimeoutSec:
    """统一公式 max(1800, audio×6, scene×360)，前后端 ReviewPanel 必须一致。"""

    def test_minimum_floor(self) -> None:
        assert pipeline_timeout_sec(0, 0) == 1800

    def test_audio_scales_times_six(self) -> None:
        assert pipeline_timeout_sec(300, 0) == 1800  # 300*6=1800
        assert pipeline_timeout_sec(1000, 0) == 6000
        assert pipeline_timeout_sec(658, 0) == 3948

    def test_scene_scales_times_360(self) -> None:
        assert pipeline_timeout_sec(0, 5) == 1800  # 5*360=1800
        assert pipeline_timeout_sec(0, 10) == 3600
        assert pipeline_timeout_sec(0, 20) == 7200

    def test_both_terms_max(self) -> None:
        assert pipeline_timeout_sec(1000, 30) == 10800  # max(6000, 10800)
        assert pipeline_timeout_sec(3000, 5) == 18000  # max(18000, 1800)

    def test_none_and_float_inputs(self) -> None:
        assert pipeline_timeout_sec(None, None) == 1800
        assert pipeline_timeout_sec(30.5, 0) == 1800  # 183 < 1800 → floor
        assert pipeline_timeout_sec(600.5, 0) == 3603
