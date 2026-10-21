"""V45 指标提取单测：关键词命中、镜头节奏、差值比较。

纯函数，用构造的时间线快照，不跑管线。
"""

from __future__ import annotations

from tools.creative_ab import PROMPT_KEYWORDS, diff_metrics, timeline_metrics


def _clip(duration: float, *, text: str = "", transition: str | None = None,
          description: dict | None = None) -> dict:
    clip: dict = {
        "id": f"c_{duration}",
        "kind": "video",
        "duration_sec": duration,
        "transition_in": transition,
    }
    if description is not None:
        clip["metadata"] = {"visual_description": description}
    if text:
        clip["text"] = text
    return clip


def _snapshot(clips: list[dict], duration: float = 24.0) -> dict:
    return {
        "id": "tl_x",
        "duration_sec": duration,
        "tracks": [
            {"name": "视频轨", "kind": "video", "clips": clips},
            {"name": "字幕轨", "kind": "caption", "clips": []},
        ],
    }


class TestKeywordHits:
    def test_counts_prompt_words_in_metadata(self) -> None:
        snap = _snapshot([
            _clip(3.0, description={"material_preference": "版画质感，三墨套印，硬切为主"}),
            _clip(4.0, description={"material_preference": "花哨转场一律不要"}),
        ])
        metrics = timeline_metrics(snap)
        assert metrics["keyword_hits"]["版画"] == 1
        assert metrics["keyword_hits"]["三墨"] == 1
        assert metrics["keyword_hits"]["硬切"] == 1
        assert metrics["keyword_hits"]["花哨"] == 1
        # "三墨套印" 同时命中 三墨 与 套印 → 5 次
        assert metrics["keyword_hits"]["套印"] == 1
        assert metrics["keyword_total"] == 5

    def test_zero_when_prompt_absent(self) -> None:
        metrics = timeline_metrics(_snapshot([_clip(3.0, description={"material_content": "桌面上两台手机"})]))
        assert metrics["keyword_total"] == 0
        assert set(metrics["keyword_hits"]) == set(PROMPT_KEYWORDS)

    def test_all_keywords_reported_even_unused(self) -> None:
        metrics = timeline_metrics(_snapshot([]))
        assert metrics["keyword_total"] == 0
        assert len(metrics["keyword_hits"]) == len(PROMPT_KEYWORDS)


class TestPacing:
    def test_mean_and_bounds(self) -> None:
        metrics = timeline_metrics(_snapshot([_clip(2.0), _clip(4.0), _clip(6.0)]))
        assert metrics["video_clips"] == 3
        assert metrics["mean_clip_sec"] == 4.0
        assert metrics["min_clip_sec"] == 2.0
        assert metrics["max_clip_sec"] == 6.0

    def test_transitions_and_hard_cut_share(self) -> None:
        metrics = timeline_metrics(_snapshot([
            _clip(2.0, transition="hard_cut"),
            _clip(2.0, transition="hard_cut"),
            _clip(2.0, transition="dissolve"),
            _clip(2.0),
        ]))
        assert metrics["hard_cut_share"] == 0.5
        assert metrics["transitions"]["hard_cut"] == 2
        assert metrics["transitions"]["none"] == 1

    def test_only_video_track_counted(self) -> None:
        metrics = timeline_metrics(_snapshot([_clip(3.0)], duration=10.0))
        assert metrics["duration_sec"] == 10.0
        assert [t["kind"] for t in metrics["tracks"]] == ["video", "caption"]

    def test_visual_description_coverage(self) -> None:
        metrics = timeline_metrics(_snapshot([
            _clip(3.0, description={"material_content": "a"}),
            _clip(3.0),
        ]))
        assert metrics["clips_with_visual_description"] == 1

    def test_empty_snapshot_safe(self) -> None:
        metrics = timeline_metrics(None)
        assert metrics["video_clips"] == 0
        assert metrics["mean_clip_sec"] is None
        assert metrics["hard_cut_share"] is None


class TestDiff:
    def test_delta_sign_and_fields(self) -> None:
        control = timeline_metrics(_snapshot([_clip(6.0), _clip(6.0)]))
        prompted = timeline_metrics(_snapshot([
            _clip(4.0, description={"material_preference": "硬切"}),
            _clip(4.0), _clip(4.0),
        ]))
        delta = diff_metrics(control, prompted)
        assert delta["video_clips"]["delta"] == 1
        assert delta["mean_clip_sec"]["delta"] == -2.0
        assert delta["keyword_total"]["delta"] == 1

    def test_missing_values_skipped(self) -> None:
        delta = diff_metrics({"video_clips": 2}, {"mean_clip_sec": 3.0})
        assert delta == {}
