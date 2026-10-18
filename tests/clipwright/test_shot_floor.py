"""D23 镜头下限策略单测：off / record / extend / loop 的语义边界。

验收关联 V2（`min_shot_ms` 是否逐段生效）与 V35（类型节奏能否压住时长）。
"""

from __future__ import annotations

import pytest

from clipwright.services.shot_floor import (
    POLICIES,
    apply_scene_floor,
    fill_mode,
    normalize_policy,
)


class TestNormalize:
    def test_unknown_falls_back_to_off(self) -> None:
        assert normalize_policy("nonsense") == "off"
        assert normalize_policy(None) == "off"
        assert normalize_policy("") == "off"

    def test_case_and_space_tolerant(self) -> None:
        assert normalize_policy(" Extend ") == "extend"

    def test_all_policies_known(self) -> None:
        assert set(POLICIES) == {"off", "record", "extend", "loop"}


class TestOff:
    def test_no_change_no_conflicts(self) -> None:
        outcome = apply_scene_floor([1.0, 2.0], 3.0, policy="off")
        assert outcome.durations == [1.0, 2.0]
        assert outcome.conflicts == []
        assert outcome.changed is False

    def test_zero_floor_is_noop(self) -> None:
        outcome = apply_scene_floor([1.0], 0.0, policy="extend")
        assert outcome.durations == [1.0]
        assert outcome.changed is False


class TestRecord:
    def test_keeps_durations_and_records_c2(self) -> None:
        outcome = apply_scene_floor([1.0, 9.0, 2.0], 3.0, policy="record")
        assert outcome.durations == [1.0, 9.0, 2.0]
        assert outcome.changed is False
        assert [c["index"] for c in outcome.conflicts] == [0, 2]
        assert all(c["code"] == "C2" and c["key"] == "min_shot_ms" for c in outcome.conflicts)
        assert "仅记录" in outcome.conflicts[0]["message"]


class TestExtend:
    def test_raises_short_segments_to_floor(self) -> None:
        outcome = apply_scene_floor([1.0, 9.0, 2.0], 3.0, policy="extend")
        assert outcome.durations == [3.0, 9.0, 3.0]
        assert outcome.changed is True
        assert len(outcome.conflicts) == 2
        assert all("已抬到下限" in c["message"] for c in outcome.conflicts)

    def test_fill_mode_is_stretch(self) -> None:
        assert fill_mode("extend") == "stretch"

    def test_all_above_floor_leaves_untouched(self) -> None:
        outcome = apply_scene_floor([4.0, 5.0], 3.0, policy="extend")
        assert outcome.durations == [4.0, 5.0]
        assert outcome.changed is False
        assert outcome.conflicts == []


class TestLoop:
    def test_same_geometry_as_extend_but_fill_marked(self) -> None:
        outcome = apply_scene_floor([1.0], 2.5, policy="loop")
        assert outcome.durations == [2.5]
        assert fill_mode("loop") == "loop"

    def test_fill_mode_off_is_empty(self) -> None:
        assert fill_mode("off") == ""
        assert fill_mode("record") == ""


class TestBoundaries:
    def test_exact_floor_not_treated_as_violation(self) -> None:
        outcome = apply_scene_floor([3.0], 3.0, policy="record")
        assert outcome.conflicts == []

    def test_negative_duration_clamped(self) -> None:
        outcome = apply_scene_floor([-1.0], 2.0, policy="extend")
        assert outcome.durations == [2.0]

    def test_float_epsilon_tolerated(self) -> None:
        outcome = apply_scene_floor([2.999999999], 3.0, policy="record")
        assert outcome.conflicts == []

    def test_preserves_order_and_length(self) -> None:
        values = [0.5, 4.0, 1.2, 8.0]
        outcome = apply_scene_floor(values, 2.0, policy="extend")
        assert len(outcome.durations) == len(values)
        assert outcome.durations[1] == 4.0 and outcome.durations[3] == 8.0

    def test_conflict_carries_policy(self) -> None:
        outcome = apply_scene_floor([1.0], 2.0, policy="extend")
        assert outcome.conflicts[0]["policy"] == "extend"
        assert outcome.conflicts[0]["floor_sec"] == pytest.approx(2.0)
