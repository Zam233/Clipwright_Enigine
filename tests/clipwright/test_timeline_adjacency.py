"""O1 第二阶段：tick 输入通道 + 轨道相邻性精确判定。

覆盖：
- `Clip` 接受 `start_ticks`/`duration_ticks`/`source_offset_ticks` 输入并折算成浮点秒；
- `Timeline` 接受 `fps_num`/`fps_den` 有理数帧率输入；
- **序列化真值不变**：tick 输入不落库、不回写 JSON（避免两个真值来源）；
- `Timeline.adjacency_issues()`：重叠/间隙/相接在整数 tick 上判定（浮点噪声不算间隙）；
- `QualityAgent` 把重叠报 warning、间隙报 info，且相邻性检查异常不影响质检整体。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from clipwright.agents.quality_agent import QualityAgent
from clipwright.schema.agent import AgentContext, QualityInput
from clipwright.schema.timeline import Clip, ClipKind, Timeline, Track
from clipwright.schema.tool import ToolExecResult, ToolStatus
from clipwright.utils.timebase import seconds_to_ticks


def _clip(clip_id: str, start: float, duration: float, *, kind: ClipKind = ClipKind.VIDEO,
          track_id: str = "t_video", **extra: object) -> Clip:
    return Clip(id=clip_id, kind=kind, asset_id=f"media/{clip_id}.mp4", track_id=track_id,
                start_sec=start, duration_sec=duration, **extra)


def _timeline(clips: list[Clip], *, fps: float | None = None, duration: float = 0.0,
              tracks: list[Track] | None = None, **extra: object) -> Timeline:
    if tracks is None:
        tracks = [Track(id="t_video", name="video", kind=ClipKind.VIDEO, index=0, clips=clips)]
    payload: dict[str, object] = {"id": "tl", "duration_sec": duration, "tracks": tracks}
    if fps is not None:
        payload["fps"] = fps
    payload.update(extra)
    return Timeline(**payload)  # type: ignore[arg-type]


# ── tick 输入通道 ─────────────────────────────────────


class TestClipTickInput:
    def test_start_and_duration_from_ticks(self) -> None:
        clip = Clip(id="c", kind=ClipKind.VIDEO, asset_id="a", track_id="t",
                    start_ticks=600_600, duration_ticks=120_120)
        assert clip.start_sec == pytest.approx(5.005)
        assert clip.duration_sec == pytest.approx(1.001)
        assert clip.start_ticks == 600_600
        assert clip.duration_ticks == 120_120
        assert clip.end_ticks == 720_720

    def test_source_offset_from_ticks(self) -> None:
        clip = Clip(id="c", kind=ClipKind.VIDEO, asset_id="a", track_id="t",
                    start_sec=0, duration_sec=1, source_offset_ticks=12_000)
        assert clip.source_offset_sec == pytest.approx(0.1)
        assert clip.source_offset_ticks == 12_000

    def test_explicit_seconds_win_over_ticks(self) -> None:
        clip = Clip(id="c", kind=ClipKind.VIDEO, asset_id="a", track_id="t",
                    start_sec=1.5, duration_sec=2.0, start_ticks=999_999)
        assert clip.start_sec == 1.5
        assert clip.start_ticks == 180_000

    @pytest.mark.parametrize("bad", [1.5, "10", True, None])
    def test_non_integer_ticks_rejected(self, bad: object) -> None:
        with pytest.raises(ValidationError):
            Clip(id="c", kind=ClipKind.VIDEO, asset_id="a", track_id="t",
                 start_ticks=bad, duration_sec=1)  # type: ignore[arg-type]

    def test_zero_duration_tick_rejected_by_domain_rule(self) -> None:
        with pytest.raises(ValidationError):
            Clip(id="c", kind=ClipKind.VIDEO, asset_id="a", track_id="t",
                 start_sec=0, duration_ticks=0)

    def test_negative_ticks_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Clip(id="c", kind=ClipKind.VIDEO, asset_id="a", track_id="t",
                 start_ticks=-1, duration_sec=1)

    def test_serialization_has_no_tick_fields(self) -> None:
        clip = Clip(id="c", kind=ClipKind.VIDEO, asset_id="a", track_id="t",
                    start_ticks=120_000, duration_ticks=240_000)
        payload = clip.model_dump()
        assert payload["start_sec"] == pytest.approx(1.0)
        assert payload["duration_sec"] == pytest.approx(2.0)
        assert "start_ticks" not in payload
        assert "duration_ticks" not in payload
        assert "source_offset_ticks" not in payload

    def test_extra_dict_key_does_not_leak_into_model(self) -> None:
        """Clip 是 extra=allow 的，tick 输入必须先被消费掉，不能变成幽灵字段。"""
        clip = Clip.model_validate({
            "id": "c", "kind": "video", "asset_id": "a", "track_id": "t",
            "start_ticks": 120_000, "duration_ticks": 240_000,
        })
        assert clip.start_sec == pytest.approx(1.0)
        assert "start_ticks" not in clip.model_dump()

    def test_model_copy_update_cannot_shadow_tick_property(self) -> None:
        clip = _clip("c", 1.0, 2.0)
        copied = clip.model_copy(update={"start_sec": 3.0})
        assert copied.start_ticks == 360_000


class TestTimelineRateInput:
    def test_fps_num_den_gives_exact_rate(self) -> None:
        tl = _timeline([_clip("c", 0, 1)], fps_num=30000, fps_den=1001)
        assert tl.frame_rate.name == "30000/1001"
        assert tl.fps == pytest.approx(29.97002997)

    def test_denominator_defaults_to_one(self) -> None:
        tl = _timeline([_clip("c", 0, 1)], fps_num=25)
        assert tl.frame_rate.name == "25"

    def test_explicit_fps_wins(self) -> None:
        tl = _timeline([_clip("c", 0, 1)], fps=25, fps_num=30000, fps_den=1001)
        assert tl.frame_rate.name == "25"

    @pytest.mark.parametrize("bad", [{"fps_num": 30000.5}, {"fps_den": 1001.0},
                                     {"fps_num": "30000"}, {"fps_num": 0}, {"fps_den": 0},
                                     {"fps_num": -30}])
    def test_invalid_rate_input_rejected(self, bad: dict) -> None:
        with pytest.raises(ValidationError):
            Timeline(id="tl", **bad)

    def test_serialization_has_no_rate_fields(self) -> None:
        payload = _timeline([_clip("c", 0, 1)], fps_num=30000, fps_den=1001).model_dump()
        assert "fps_num" not in payload and "fps_den" not in payload
        assert payload["fps"] == pytest.approx(29.97002997)


# ── 轨道相邻性 ───────────────────────────────────────


class TestAdjacencyIssues:
    def test_touching_clips_produce_no_issue(self) -> None:
        tl = _timeline([_clip("a", 0.0, 5.0), _clip("b", 5.0, 5.0)])
        assert tl.adjacency_issues() == []

    def test_float_noise_at_boundary_is_touching(self) -> None:
        """`start + duration` 与下一段 start 差 1e-13 秒：tick 量化后恒等，不算间隙。"""
        tl = _timeline([_clip("a", 0.1, 0.2), _clip("b", 0.1 + 0.2 + 1e-13, 0.2)])
        assert tl.adjacency_issues() == []

    def test_overlap_reported_with_negative_delta(self) -> None:
        tl = _timeline([_clip("a", 0.0, 5.0), _clip("b", 4.5, 5.0)])
        issues = tl.adjacency_issues()
        assert len(issues) == 1
        assert issues[0]["kind"] == "overlap"
        assert issues[0]["prev"] == "a" and issues[0]["next"] == "b"
        assert issues[0]["delta_ticks"] == seconds_to_ticks(-0.5)
        assert issues[0]["delta_sec"] == pytest.approx(-0.5)

    def test_gap_reported_with_positive_delta(self) -> None:
        tl = _timeline([_clip("a", 0.0, 5.0), _clip("b", 6.0, 2.0)])
        issues = tl.adjacency_issues()
        assert [i["kind"] for i in issues] == ["gap"]
        assert issues[0]["delta_sec"] == pytest.approx(1.0)

    def test_unordered_clips_are_sorted_first(self) -> None:
        tl = _timeline([_clip("b", 5.0, 5.0), _clip("a", 0.0, 5.0)])
        assert tl.adjacency_issues() == []

    def test_tolerance_widens_touching_window(self) -> None:
        tl = _timeline([_clip("a", 0.0, 5.0), _clip("b", 5.001, 5.0)])
        assert len(tl.adjacency_issues()) == 1
        assert tl.adjacency_issues(tolerance_ticks=seconds_to_ticks(0.01)) == []

    def test_disabled_clips_are_skipped(self) -> None:
        tl = _timeline([
            _clip("a", 0.0, 5.0),
            _clip("skip", 100.0, 1.0, enabled=False),
            _clip("b", 5.0, 5.0),
        ])
        assert tl.adjacency_issues() == []

    def test_tracks_are_isolated(self) -> None:
        """跨轨道的起止不构成相邻关系（文字轨从头开始时不能报重叠）。"""
        video = Track(id="v", kind=ClipKind.VIDEO, index=0, clips=[_clip("a", 0.0, 10.0)])
        text = Track(id="t", kind=ClipKind.TEXT, index=1, clips=[
            _clip("t1", 0.0, 2.0, kind=ClipKind.TEXT, track_id="t"),
            _clip("t2", 3.0, 2.0, kind=ClipKind.TEXT, track_id="t"),
        ])
        tl = _timeline([], tracks=[video, text])
        issues = tl.adjacency_issues()
        assert [i["track_id"] for i in issues] == ["t"]

    def test_limit_caps_reported_issues(self) -> None:
        clips = [_clip(f"c{i}", i * 10.0, 5.0) for i in range(6)]
        assert len(_timeline(clips).adjacency_issues(limit=3)) == 3

    def test_empty_timeline(self) -> None:
        assert Timeline().adjacency_issues() == []


# ── QualityAgent 接线 ────────────────────────────────


def _ctx() -> AgentContext:
    return AgentContext(pipeline_id="p_test", persona_id="p", category_plugin_id="c", topic="t")


def _ok_result() -> ToolExecResult:
    return ToolExecResult(status=ToolStatus.SUCCESS, tool_name="frame_validator", output={
        "valid": True, "is_blank": False, "is_white": False, "is_overexposed": False,
        "sample_count": 2, "match_score": 1.0,
    })


def _audio_track() -> Track:
    return Track(id="t_audio", kind=ClipKind.AUDIO, index=1, clips=[
        Clip(id="a1", kind=ClipKind.AUDIO, asset_id="m.mp3", track_id="t_audio",
             start_sec=0.0, duration_sec=60.0),
    ])


async def _run_quality(timeline: Timeline) -> object:
    with patch("clipwright.tool.registry.ToolRegistry.execute",
               new=AsyncMock(return_value=_ok_result())):
        return await QualityAgent().execute(
            QualityInput(context=_ctx(), timeline=timeline, constraints={}), _ctx()
        )


class TestQualityAgentAdjacency:
    @pytest.mark.asyncio
    async def test_overlap_becomes_structure_warning(self) -> None:
        tl = _timeline([_clip("a", 0.0, 5.0), _clip("b", 4.5, 5.0)],
                       tracks=None, duration=9.5)
        tl.tracks.append(_audio_track())
        out = await _run_quality(tl)
        hits = [i for i in out.issues if i.category == "structure" and "重叠" in i.message]
        assert hits, [i.message for i in out.issues]
        assert hits[0].severity == "warning"
        assert "a" in hits[0].message and "b" in hits[0].message

    @pytest.mark.asyncio
    async def test_gap_becomes_structure_info(self) -> None:
        tl = _timeline([_clip("a", 0.0, 5.0), _clip("b", 6.0, 2.0)], duration=8.0)
        tl.tracks.append(_audio_track())
        out = await _run_quality(tl)
        hits = [i for i in out.issues if i.category == "structure" and "间隙" in i.message]
        assert hits, [i.message for i in out.issues]
        assert hits[0].severity == "info"

    @pytest.mark.asyncio
    async def test_contiguous_timeline_reports_neither(self) -> None:
        tl = _timeline([_clip("a", 0.0, 5.0), _clip("b", 5.0, 5.0)], duration=10.0)
        tl.tracks.append(_audio_track())
        out = await _run_quality(tl)
        assert not [i for i in out.issues if "重叠" in i.message or "间隙" in i.message]

    @pytest.mark.asyncio
    async def test_adjacency_failure_does_not_break_quality(self) -> None:
        tl = _timeline([_clip("a", 0.0, 5.0)], duration=5.0)
        tl.tracks.append(_audio_track())
        with patch.object(Timeline, "adjacency_issues", side_effect=RuntimeError("boom")):
            out = await _run_quality(tl)
        assert out is not None


# ── API 边界（tick 输入真的能被接口接受） ──────────────


class TestApiTickInput:
    def _payload(self, **clip_extra: object) -> dict:
        clip = {"id": "c1", "kind": "video", "asset_id": "media/a.mp4", "track_id": "v0"}
        clip.update(clip_extra)
        return {
            "timeline": {
                "id": "tl", "fps_num": 30000, "fps_den": 1001, "duration_sec": 10.0,
                "tracks": [{"id": "v0", "name": "video", "kind": "video", "index": 0,
                            "clips": [clip]}],
            },
        }

    def test_render_request_parses_tick_timeline(self) -> None:
        from clipwright.api.render import RenderRequest

        req = RenderRequest.model_validate(self._payload(
            start_ticks=600_600, duration_ticks=300_300, source_offset_ticks=0))
        clip = req.timeline.tracks[0].clips[0]
        assert req.timeline.frame_rate.name == "30000/1001"
        assert clip.start_sec == pytest.approx(5.005)
        assert clip.duration_sec == pytest.approx(2.5025)
        assert clip.start_ticks == 600_600

    def test_render_start_endpoint_rejects_non_integer_ticks(self) -> None:
        """非法 tick 在请求校验阶段就被拒（422），不会进入渲染。"""
        from fastapi.testclient import TestClient

        from clipwright.main import app

        client = TestClient(app)
        resp = client.post("/api/render/start", json=self._payload(start_ticks=1.5, duration_sec=2.0))
        assert resp.status_code == 422, resp.text

    def test_render_start_endpoint_rejects_bad_rate(self) -> None:
        from fastapi.testclient import TestClient

        from clipwright.main import app

        client = TestClient(app)
        payload = self._payload(start_sec=0.0, duration_sec=2.0)
        payload["timeline"]["fps_den"] = 0
        resp = client.post("/api/render/start", json=payload)
        assert resp.status_code == 422, resp.text
