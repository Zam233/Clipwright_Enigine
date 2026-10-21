"""D23 镜头节奏策略单测：off / record / extend / loop / merge / split 的语义边界。

验收关联 V2（`min_shot_ms` 是否逐段生效）与 V35（类型节奏能否压住时长）。
D23 拍板后管线默认 ``merge``（低于下限合并相邻场景）+ ``split``（高于上限切分），
两者都**保总时长**。
"""

from __future__ import annotations

import pytest

from clipwright.services.shot_floor import (
    CAP_POLICIES,
    POLICIES,
    apply_scene_floor,
    fill_mode,
    merge_to_floor,
    normalize_cap_policy,
    normalize_policy,
    split_to_ceiling,
)


class TestNormalize:
    def test_unknown_falls_back_to_off(self) -> None:
        assert normalize_policy("nonsense") == "off"
        assert normalize_policy(None) == "off"
        assert normalize_policy("") == "off"

    def test_case_and_space_tolerant(self) -> None:
        assert normalize_policy(" Extend ") == "extend"

    def test_all_policies_known(self) -> None:
        assert set(POLICIES) == {"off", "record", "extend", "loop", "merge"}


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


class TestMergeToFloor:
    """D23 默认策略：低于下限的相邻场景**合并**，镜头变少变长、总时长不变。"""

    def test_merges_adjacent_short_scenes(self) -> None:
        # [5,3,5,6,5] floor=9 → (5+3+5)=13 ≥9、(6+5)=11 ≥9
        outcome = merge_to_floor([5.0, 3.0, 5.0, 6.0, 5.0], 9.0)
        assert outcome.changed is True
        assert outcome.durations == pytest.approx([13.0, 11.0])
        assert outcome.groups == [[0, 1, 2], [3, 4]]
        assert sum(outcome.durations) == pytest.approx(24.0)  # 总时长不变

    def test_all_above_floor_is_noop(self) -> None:
        outcome = merge_to_floor([9.0, 12.0], 9.0)
        assert outcome.changed is False
        assert outcome.durations == [9.0, 12.0]
        assert outcome.conflicts == []

    def test_floor_zero_is_noop(self) -> None:
        outcome = merge_to_floor([1.0, 2.0], 0.0)
        assert outcome.durations == [1.0, 2.0]
        assert outcome.changed is False

    def test_tail_remainder_folds_into_previous(self) -> None:
        # [9,4] floor=9 → 9 达标，尾部 4 并入 → 13 单段
        outcome = merge_to_floor([9.0, 4.0], 9.0)
        assert outcome.durations == pytest.approx([13.0])
        assert outcome.groups == [[0, 1]]

    def test_total_shorter_than_floor_keeps_one_segment_with_conflict(self) -> None:
        outcome = merge_to_floor([3.0, 4.0], 45.0)
        assert outcome.durations == pytest.approx([7.0])
        assert outcome.conflicts and "无解" in outcome.conflicts[0]["message"]

    def test_conflict_mentions_merged_indices(self) -> None:
        # 贪心累加：1+1 仍不足 5，继续吸收后面的 9 → 单段 11s
        outcome = merge_to_floor([1.0, 1.0, 9.0], 5.0)
        assert outcome.groups == [[0, 1, 2]]
        assert outcome.conflicts[0]["merged_from"] == [0, 1, 2]
        assert "下限" in outcome.conflicts[0]["message"]


class TestSplitToCeiling:
    """D23 默认上限策略：超长场景切分多镜，总时长不变（V35 的落地机制）。"""

    def test_splits_long_scene_evenly(self) -> None:
        outcome = split_to_ceiling([6.0, 3.0], 3.2)
        assert outcome.changed is True
        assert outcome.durations == pytest.approx([3.0, 3.0, 3.0])
        assert outcome.groups == [2, 1]
        assert sum(outcome.durations) == pytest.approx(9.0)

    def test_within_ceiling_is_noop(self) -> None:
        outcome = split_to_ceiling([3.0, 2.0], 3.2)
        assert outcome.changed is False
        assert outcome.durations == [3.0, 2.0]

    def test_ceiling_zero_is_noop(self) -> None:
        outcome = split_to_ceiling([6.0], 0.0)
        assert outcome.durations == [6.0]
        assert outcome.changed is False

    def test_conflict_is_c3(self) -> None:
        outcome = split_to_ceiling([20.0], 3.2)
        assert outcome.conflicts[0]["code"] == "C3"
        assert outcome.conflicts[0]["split_into"] == 7
        assert outcome.conflicts[0]["ceiling_sec"] == pytest.approx(3.2)
        assert all(value <= 3.2 + 1e-9 for value in outcome.durations)

    def test_uneven_split_keeps_total(self) -> None:
        outcome = split_to_ceiling([10.0], 3.0)
        assert len(outcome.durations) == 4
        assert sum(outcome.durations) == pytest.approx(10.0)

    def test_max_parts_guard_truncates_and_records(self) -> None:
        # 250s 场景按 3s 上限本该切 84 镜；安全阀只切 8 镜并留痕（不假装合规）
        outcome = split_to_ceiling([250.0], 3.0, max_parts=8)
        assert len(outcome.durations) == 8
        assert sum(outcome.durations) == pytest.approx(250.0)
        conflict = outcome.conflicts[0]
        assert conflict["truncated"] is True
        assert conflict["split_wanted"] == 84
        assert conflict["split_into"] == 8
        assert "截断" in conflict["message"]

    def test_no_truncation_flag_when_within_cap(self) -> None:
        outcome = split_to_ceiling([20.0], 3.0)
        assert outcome.conflicts[0]["truncated"] is False


class TestCapPolicyNormalize:
    def test_known_values(self) -> None:
        assert set(CAP_POLICIES) == {"off", "record", "split"}
        assert normalize_cap_policy(" Split ") == "split"
        assert normalize_cap_policy("merge") == "off"  # 上限不接受 merge
        assert normalize_cap_policy(None) == "off"
