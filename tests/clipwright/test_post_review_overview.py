"""V4 残余：全片总览图（--edl 类比）、复核派工接入 RenderResult/QualityAgent。

真实 ffmpeg 抽帧（与轮 105 的切点复核测试同一套路），无 ffmpeg 时整体跳过。
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from clipwright.services.timeline_view import (
    OVERVIEW_FRAMES,
    build_overview_sheet,
    overview_times,
)

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="需要本机 ffmpeg/ffprobe",
)


def _clip(path: Path, seconds: float) -> None:
    r = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", f"testsrc2=size=320x240:rate=30:duration={seconds}",
         "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(path)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr[-300:]


class TestOverviewTimes:
    def test_evenly_spaced_inside_head_offset(self) -> None:
        times = overview_times(10.0, count=5)
        assert times[0] == 0.5
        assert times[-1] == pytest.approx(9.5)
        steps = [round(times[i + 1] - times[i], 6) for i in range(len(times) - 1)]
        assert len(set(steps)) == 1  # 等间隔

    def test_single_frame_takes_middle(self) -> None:
        assert overview_times(10.0, count=1) == [5.0]

    def test_short_duration_clamps_offset(self) -> None:
        times = overview_times(1.0, count=3)
        assert times[0] >= 0.0
        assert times[-1] <= 1.0
        assert times[0] < times[-1]

    @pytest.mark.parametrize("bad", [0, -5, None, "abc"])
    def test_invalid_duration(self, bad: object) -> None:
        assert overview_times(bad) == []  # type: ignore[arg-type]

    def test_default_count(self) -> None:
        assert len(overview_times(60.0)) == OVERVIEW_FRAMES


class TestBuildOverviewSheet:
    def test_grid_sheet_created(self, tmp_path: Path) -> None:
        video = tmp_path / "v.mp4"
        _clip(video, 4.0)
        out = tmp_path / "overview.png"

        path = build_overview_sheet(str(video), overview_times(4.0, count=6), out, cols=3)

        assert path and Path(path).exists()
        from PIL import Image

        with Image.open(path) as img:
            # 3 列 2 行 → 宽度约为单格 3 倍
            assert img.width > 320 * 2
            assert img.height > 240
            assert img.width < 320 * 4 + 100  # 不超过列数对应的宽度

    def test_missing_video_returns_none(self, tmp_path: Path) -> None:
        assert build_overview_sheet(str(tmp_path / "nope.mp4"), [1.0, 2.0], tmp_path / "o.png") is None

    def test_no_times_returns_none(self, tmp_path: Path) -> None:
        video = tmp_path / "v.mp4"
        _clip(video, 1.0)
        assert build_overview_sheet(str(video), [], tmp_path / "o.png") is None

    def test_partial_failures_keep_available_tiles(self, tmp_path: Path) -> None:
        """有些采样点抽不到帧（超出时长）时，只要有一格成功就要出图。"""
        video = tmp_path / "v.mp4"
        _clip(video, 2.0)
        out = tmp_path / "partial.png"
        path = build_overview_sheet(str(video), [1000.0, 1.0, 2000.0], out, cols=2)
        assert path and Path(path).exists()


class TestPostRenderReviewRouting:
    """复核结论要真的进 RenderResult（派工列表 / 评分 / 门槛标志）。"""

    def _render(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, verdict: dict | None):
        from clipwright.schema.timeline import Timeline
        from clipwright.services.render import RenderService

        clips = []
        for i in (1, 2, 3):
            p = tmp_path / f"m{i}.mp4"
            _clip(p, 3.0)
            clips.append({
                "id": f"c{i}", "kind": "video", "asset_id": str(p), "track_id": "v0",
                "start_sec": (i - 1) * 3.0, "duration_sec": 3.0, "source_offset_sec": 0,
                "speed": 1, "volume": 0, "opacity": 1, "keyframes": [], "metadata": {},
            })
        timeline = Timeline(**{
            "id": "review_case", "width": 320, "height": 240, "fps": 30, "duration_sec": 9,
            "tracks": [{"id": "v0", "name": "视频", "kind": "video", "index": 0, "clips": clips}],
        })
        monkeypatch.setenv("CLIPWRIGHT_POST_RENDER_REVIEW", "1")
        monkeypatch.setenv("CLIPWRIGHT_REVIEW_DIR", str(tmp_path / "review"))

        # 视觉模型调用替换成固定裁决（不触网、不依赖 enable_visual_llm）
        from clipwright.animation.mg import visual_eval

        async def _fake_evaluate(self, images, **kwargs):  # noqa: ANN001
            assert images, "复核图必须传给视觉裁决"
            return verdict

        monkeypatch.setattr(visual_eval.MGVisualEvaluator, "evaluate", _fake_evaluate)

        svc = RenderService(work_dir=tmp_path / "engine")
        return asyncio.run(svc.render(timeline, tmp_path / "out.mp4", width=320, height=240, fps=30))

    def test_findings_and_score_exposed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        verdict = {
            "score": 42,
            "issues": [
                {"severity": "high", "detail": "第 3 秒字幕被 overlay 遮住"},
                {"severity": "medium", "detail": "切点处画面跳变"},
            ],
            "suggestions": [],
            "summary": "两处问题",
            "source": "visual_eval",
        }
        res = self._render(tmp_path, monkeypatch, verdict)

        assert res.success, res.error
        assert res.visual_score == 42
        assert len(res.review_findings) == 2
        assert res.review_findings[0]["redo_agent"] == "animation"
        assert any(f["redo_agent"] == "edit" for f in res.review_findings)
        # 派工提示写进 warnings（人/前端都看得到）
        assert any("建议重做" in w for w in res.warnings), res.warnings
        # 复核产物落地：切点复核图 + 全片总览图
        review_dir = tmp_path / "review"
        assert list(review_dir.glob("review_*/*.png")), "复核图未生成"
        assert any(p.name == "overview.png" for p in review_dir.glob("review_*/*.png"))
        # 默认不启用评分门槛 → 不算失败
        assert res.review_failed is False

    def test_score_threshold_marks_review_failed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLIPWRIGHT_REVIEW_MIN_SCORE", "80")
        res = self._render(tmp_path, monkeypatch, {
            "score": 30, "issues": [], "suggestions": [], "summary": "", "source": "visual_eval",
        })
        assert res.success  # 复核不改变渲染成功与否
        assert res.review_failed is True
        assert any("未过门槛" in w for w in res.warnings), res.warnings

    def test_no_verdict_still_emits_sheets(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        res = self._render(tmp_path, monkeypatch, None)
        assert res.success, res.error
        assert res.review_findings == []
        assert res.visual_score is None
        assert any("复核图" in w for w in res.warnings), res.warnings

    def test_result_to_dict_exposes_review_fields(self) -> None:
        from clipwright.services.render import RenderResult

        payload = RenderResult(
            True, output_path="x.mp4", review_findings=[{"kind": "edit", "redo_agent": "edit"}],
            visual_score=88.0, review_failed=False,
        ).to_dict()
        assert payload["review_findings"] == [{"kind": "edit", "redo_agent": "edit"}]
        assert payload["visual_score"] == 88.0
        assert payload["review_failed"] is False


class TestQualityAgentConsumesFindings:
    """复核结论经 constraints 进质检 → 变成问题并推导出 redo_agent。"""

    def _ctx(self):
        from clipwright.schema.agent import AgentContext

        return AgentContext(pipeline_id="p", persona_id="pp", category_plugin_id="c", topic="t")

    def _timeline(self, tmp_path: Path):
        from clipwright.schema.timeline import Timeline

        video = tmp_path / "v.mp4"
        _clip(video, 3.0)
        clip = {
            "id": "v1", "kind": "video", "asset_id": str(video), "track_id": "t0",
            "start_sec": 0.0, "duration_sec": 3.0, "source_offset_sec": 0,
            "speed": 1, "volume": 0, "opacity": 1, "keyframes": [], "metadata": {},
        }
        audio = {
            "id": "a1", "kind": "audio", "asset_id": str(video), "track_id": "t1",
            "start_sec": 0.0, "duration_sec": 3.0, "source_offset_sec": 0,
            "speed": 1, "volume": 0.5, "opacity": 1, "keyframes": [], "metadata": {},
        }
        return Timeline(**{
            "id": "tl", "width": 320, "height": 240, "fps": 30, "duration_sec": 3,
            "tracks": [
                {"id": "t0", "name": "v", "kind": "video", "index": 0, "clips": [clip]},
                {"id": "t1", "name": "a", "kind": "audio", "index": 1, "clips": [audio]},
            ],
        })

    def _run(self, tmp_path: Path, constraints: dict):
        from unittest.mock import AsyncMock, patch

        from clipwright.agents.quality_agent import QualityAgent
        from clipwright.schema.agent import QualityInput
        from clipwright.schema.tool import ToolExecResult, ToolStatus

        ok = ToolExecResult(status=ToolStatus.SUCCESS, tool_name="frame_validator", output={
            "valid": True, "is_blank": False, "is_white": False, "is_overexposed": False,
            "sample_count": 2, "match_score": 1.0,
        })
        with patch("clipwright.tool.registry.ToolRegistry.execute", new=AsyncMock(return_value=ok)):
            return asyncio.run(QualityAgent().execute(
                QualityInput(context=self._ctx(), timeline=self._timeline(tmp_path), constraints=constraints),
                self._ctx(),
            ))

    def test_high_finding_becomes_error_and_routes_animation(self, tmp_path: Path) -> None:
        out = self._run(tmp_path, {"review_findings": [
            {"kind": "animation", "severity": "high", "detail": "字幕被 overlay 遮住",
             "redo_agent": "animation", "evidence": ["/tmp/a.png"]},
        ]})
        hits = [i for i in out.issues if "渲染后复核" in i.message]
        assert hits and hits[0].severity == "error"
        assert hits[0].category == "animation"
        assert out.redo_agent == "animation"

    def test_material_finding_routes_material(self, tmp_path: Path) -> None:
        out = self._run(tmp_path, {"review_findings": [
            {"kind": "material", "severity": "high", "detail": "第 2 秒全黑帧", "redo_agent": "material"},
        ]})
        assert out.redo_agent == "material"
        assert any(i.category == "material" for i in out.issues if "渲染后复核" in i.message)

    def test_low_finding_is_info_and_does_not_fail(self, tmp_path: Path) -> None:
        out = self._run(tmp_path, {"review_findings": [
            {"kind": "edit", "severity": "low", "detail": "切点轻微跳变", "redo_agent": "edit"},
        ]})
        hits = [i for i in out.issues if "渲染后复核" in i.message]
        assert hits and hits[0].severity == "info"
        assert out.passed is True

    def test_no_findings_is_noop(self, tmp_path: Path) -> None:
        out = self._run(tmp_path, {})
        assert not [i for i in out.issues if "渲染后复核" in i.message]

    def test_broken_findings_do_not_break_quality(self, tmp_path: Path) -> None:
        out = self._run(tmp_path, {"review_findings": "not-a-list"})
        assert out is not None
