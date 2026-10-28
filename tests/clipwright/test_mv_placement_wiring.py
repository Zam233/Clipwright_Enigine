"""S 轨接线单测：落位写回场景（PRD C13 / S-03 接线，验收 V41 的代码侧）。"""

from __future__ import annotations

from clipwright.agents.material_agent import annotate_mv_placement, _placement_enabled
from clipwright.services.mv_cues import build_grid
from clipwright.services.mv_material import annotate_scenes_with_placement


def _scenes() -> list[dict]:
    return [
        {"title": "开场", "description": "她伸出手", "start_sec": 0.0, "duration_sec": 2.0},
        {"title": "城市", "description": "城市夜景", "start_sec": 2.0, "duration_sec": 2.0},
        {"title": "无关", "description": "完全无关的一句", "start_sec": 4.0, "duration_sec": 2.0},
    ]


def _candidate_clips() -> list[dict]:
    return [
        {
            "scene_index": 0,
            "suggested_assets": [
                {"asset_id": "hand_1", "description": "close-up of hand", "tags": ["hand_closeup"]},
                {"asset_id": "city_1", "description": "night city lights", "tags": ["city"]},
            ],
        },
        {
            "scene_index": 1,
            "suggested_assets": [
                {"asset_id": "city_2", "description": "street at night", "tags": ["city"]},
            ],
        },
    ]


class TestPlacementEnabled:
    def test_mv_type_enables(self) -> None:
        class Ctx:
            category_plugin_id = "lyric_mv"
            extra_params: dict = {}

        assert _placement_enabled(Ctx()) is True

    def test_explicit_flag_enables(self) -> None:
        class Ctx:
            category_plugin_id = "knowledge_longform"
            extra_params = {"mv_placement": True}

        assert _placement_enabled(Ctx()) is True

    def test_other_types_disabled(self) -> None:
        class Ctx:
            category_plugin_id = "knowledge_longform"
            extra_params: dict = {}

        assert _placement_enabled(Ctx()) is False


class TestAnnotateScenes:
    def test_writes_refs_and_evidence(self) -> None:
        scenes = _scenes()
        stats = annotate_mv_placement(scenes, _candidate_clips())
        assert stats is not None
        assert scenes[0]["material_refs"] == ["hand_1"]
        assert scenes[0]["placement"]["matched_by"].startswith("tag")
        assert scenes[0]["placement"]["score"] > 0
        assert scenes[1]["material_refs"] == ["city_2"] or scenes[1]["material_refs"] == ["city_1"]

    def test_unmatched_scene_left_untouched_and_reported_as_gap(self) -> None:
        scenes = _scenes()
        stats = annotate_mv_placement(scenes, _candidate_clips())
        assert "material_refs" not in scenes[2]
        assert any(gap["line_index"] == 2 for gap in stats["gaps"])

    def test_no_candidates_returns_none(self) -> None:
        assert annotate_mv_placement(_scenes(), []) is None

    def test_defensive_against_bad_assets(self) -> None:
        stats = annotate_mv_placement(
            _scenes(),
            [{"scene_index": 0, "suggested_assets": [{"no_id": True}, "not-a-dict"]}],
        )
        assert stats is None

    def test_grid_snapping_applied(self) -> None:
        scenes = [{"title": "x", "description": "手", "start_sec": 0.62, "duration_sec": 2.0}]
        annotate_mv_placement(
            scenes,
            [{"scene_index": 0, "suggested_assets": [
                {"asset_id": "hand_1", "tags": ["hand_closeup"]}]}],
            bits_per_second=120.0,
        )
        assert scenes[0]["placement"]["start_sec"] <= 0.62


class TestPureAnnotation:
    def test_annotate_scenes_direct(self) -> None:
        scenes = [{"title": "手", "description": "她伸出手", "start_sec": 0.0, "duration_sec": 2.0}]
        stats = annotate_scenes_with_placement(
            scenes,
            [{"asset_id": "hand_1", "tags": ["hand_closeup"]}],
            grid=build_grid(120, 0.0),
        )
        assert stats["covered"] == 1
        assert scenes[0]["material_refs"] == ["hand_1"]

    def test_license_unclear_not_placed(self) -> None:
        scenes = [{"title": "手", "description": "她伸出手", "start_sec": 0.0, "duration_sec": 2.0}]
        stats = annotate_scenes_with_placement(
            scenes, [{"asset_id": "bad", "tags": ["hand_closeup"], "license_ok": False}]
        )
        assert stats["covered"] == 0
        assert "material_refs" not in scenes[0]
        assert stats["rejected"]
