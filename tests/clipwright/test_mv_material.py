"""MV 素材识别与落位单测（PRD C13 / S-01…S-04，验收 V41–V43）。"""

from __future__ import annotations

import pytest

from clipwright.services.mv_cues import build_grid
from clipwright.services.mv_material import (
    MIN_MATCH_SCORE,
    MV_TAGS,
    MaterialCandidate,
    build_lyric_queries,
    place_materials,
    placement_report,
    score_candidate,
    tag_text,
)


def _line(i: int, text: str, t0: float = 0.0, t1: float = 2.0) -> dict:
    return {"i": i, "text": text, "t0": t0, "t1": t1}


class TestTagText:
    def test_hand_closeup(self) -> None:
        assert "hand_closeup" in tag_text("她伸出手")

    def test_english_keyword(self) -> None:
        assert "stage" in tag_text("live on stage tonight")

    def test_multiple_tags_order_stable(self) -> None:
        tags = tag_text("舞台的灯光与雨")
        assert tags == [tag for tag in MV_TAGS if tag in tags]
        assert {"stage", "light_glow", "water_rain"} <= set(tags)

    def test_no_hit(self) -> None:
        assert tag_text("完全无关的一句话") == []


class TestLyricQueries:
    def test_one_query_per_line(self) -> None:
        queries = build_lyric_queries([_line(0, "手伸向镜头"), _line(1, "城市夜景")])
        assert [q["line_index"] for q in queries] == [0, 1]
        assert "hand_closeup" in queries[0]["tags"]
        assert "city" in queries[1]["tags"]

    def test_keywords_extracted(self) -> None:
        queries = build_lyric_queries([_line(0, "if I am a set of points")])
        assert "points" in queries[0]["keywords"]

    def test_times_preserved(self) -> None:
        queries = build_lyric_queries([_line(3, "x", 1.5, 3.0)])
        assert queries[0]["t0"] == 1.5 and queries[0]["t1"] == 3.0


class TestScoring:
    def test_tag_hit_beats_keyword_hit(self) -> None:
        """显式标签 > 描述同义词 > 关键词（分档，不许同分）。"""
        tagged = MaterialCandidate("a", duration_sec=2, tags=("hand_closeup",))
        synonym = MaterialCandidate("b", description="hand in the dark", duration_sec=2)
        keyword = MaterialCandidate("c", description="points everywhere", duration_sec=2)

        tag_score, tag_by = score_candidate(["hand_closeup"], "", tagged)
        syn_score, syn_by = score_candidate(["hand_closeup"], "", synonym)
        kw_score, kw_by = score_candidate([], "points", keyword)

        assert tag_score > syn_score > kw_score
        assert tag_by.startswith("tag:")
        assert syn_by.startswith("tag_text:")
        assert kw_by.startswith("keyword:")

    def test_no_hit_scores_zero(self) -> None:
        score, by = score_candidate([], "完全无关", MaterialCandidate("a", description="zzz"))
        assert score == 0.0 and by == "none"


class TestPlacement:
    def _candidates(self) -> list[MaterialCandidate]:
        return [
            MaterialCandidate("hand_1", duration_sec=2, tags=("hand_closeup",)),
            MaterialCandidate("city_1", duration_sec=2, tags=("city",)),
            MaterialCandidate("stage_1", duration_sec=2, tags=("stage",)),
        ]

    def test_places_matching_line(self) -> None:
        result = place_materials([_line(0, "她伸出手", 0.0, 2.0)], self._candidates())
        assert result.covered == 1
        assert result.placements[0].asset_id == "hand_1"
        assert result.placements[0].matched_by.startswith("tag:")

    def test_no_match_reports_gap_not_filler(self) -> None:
        result = place_materials([_line(0, "完全无关的一句")], self._candidates())
        assert result.covered == 0
        assert result.gaps and result.gaps[0]["reason"] == "no_candidate_above_threshold"

    def test_license_unclear_is_rejected_not_used(self) -> None:
        candidates = [MaterialCandidate("bad", tags=("hand_closeup",), license_ok=False)]
        result = place_materials([_line(0, "她伸出手")], candidates)
        assert result.covered == 0
        assert any(item["asset_id"] == "bad" for item in result.rejected)

    def test_each_candidate_used_once(self) -> None:
        lines = [_line(0, "她伸出手"), _line(1, "她再次伸出手")]
        result = place_materials(lines, self._candidates())
        assert result.covered == 1
        assert result.gaps  # 第二次没有可用候选

    def test_snaps_start_to_grid_without_overshoot(self) -> None:
        grid = build_grid(120, 0.0)  # 一拍 0.5s
        result = place_materials([_line(0, "她伸出手", 0.62, 2.0)], self._candidates(), grid=grid)
        start = result.placements[0].start_sec
        assert start <= 0.62
        assert start in (0.5, 0.62)

    def test_deterministic_tie_break(self) -> None:
        a = MaterialCandidate("b_asset", tags=("hand_closeup",))
        b = MaterialCandidate("a_asset", tags=("hand_closeup",))
        result = place_materials([_line(0, "她伸出手")], [a, b])
        assert result.placements[0].asset_id in {"a_asset", "b_asset"}
        again = place_materials([_line(0, "她伸出手")], [a, b])
        assert result.placements[0].asset_id == again.placements[0].asset_id

    def test_threshold_respected(self) -> None:
        weak = MaterialCandidate("weak", description="dim light", tags=())
        result = place_materials([_line(0, "她伸出手")], [weak], min_score=MIN_MATCH_SCORE)
        assert result.covered == 0


class TestReport:
    def test_report_shape(self) -> None:
        result = place_materials(
            [_line(0, "她伸出手"), _line(1, "无关的一句")],
            [MaterialCandidate("hand_1", tags=("hand_closeup",))],
        )
        payload = placement_report(result)
        assert payload["covered"] == 1
        assert payload["total"] == 2
        assert payload["gaps"]
        assert payload["placements"][0]["matched_by"].startswith("tag:")

    def test_report_lists_rejected(self) -> None:
        result = place_materials(
            [_line(0, "她伸出手")],
            [MaterialCandidate("bad", tags=("hand_closeup",), license_ok=False)],
        )
        assert placement_report(result)["rejected"]
