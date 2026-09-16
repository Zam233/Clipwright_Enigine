"""R7：意图 → 时间线补丁（plan / dry-run / apply）单元测试。

测试组织逻辑：

- **执行器**（`_execute` 的各分支）逐条测，重点是"改动后的位置与时长对不对"；
- **dry-run 只读性**单独测——这是"敢把预览给用户看"的前提，一旦被破坏
  （预览顺手改了时间线）后果比功能缺失严重得多；
- **门禁**（有硬错误一条都不执行、有问题就先反问）单独测——这是 R7 与
  "直接让 LLM 改时间线"的根本区别；
- **收拢规则**（ripple）单独测，因为它的取舍（保留刻意留白、抽掉被删时间）
  是最容易被后续改动无意破坏的不变量。
"""

import pytest

from clipwright.schema.timeline import Clip, Timeline, Track
from clipwright.services import intent_apply as ia
from clipwright.services.edit_intent import EditIntent, IntentOp, IntentTarget
from clipwright.services.intent_resolver import ResolutionReport, ResolvedOp


# ── 夹具 ──────────────────────────────────────────────────


def make_timeline(specs: list[tuple[str, float, float]], *, track_id: str = "t1",
                  kind: str = "video") -> Timeline:
    """`specs = [(clip_id, start_sec, duration_sec), ...]`"""
    clips = [
        Clip(id=cid, kind=kind, asset_id=f"{cid}.mp4", track_id=track_id,
             start_sec=start, duration_sec=dur)
        for cid, start, dur in specs
    ]
    return Timeline(id="tl", fps=30, width=1920, height=1080,
                    tracks=[Track(id=track_id, name="V1", kind=kind, index=0, clips=clips)])


def five() -> Timeline:
    """5 个各 4 秒的片段：0/4/8/12/16，总时长 20s。"""
    return make_timeline([(f"c{i}", i * 4.0, 4.0) for i in range(5)])


def positions(timeline: Timeline, track_index: int = 0) -> list[tuple[str, float, float]]:
    return [(c.id, float(c.start_sec), float(c.duration_sec))
            for c in timeline.tracks[track_index].clips]


def resolution_for(intent: EditIntent, ids_per_op: list[list[str]]) -> ResolutionReport:
    report = ResolutionReport()
    for op, ids in zip(intent.ops, ids_per_op):
        report.resolved.append(ResolvedOp(op=op, clip_ids=list(ids)))
    return report


def plan_of(op: IntentOp, ids: list[str], **kwargs) -> ia.PatchPlan:
    intent = EditIntent(ops=[op])
    return ia.plan_patch(intent, resolution_for(intent, [ids]), kwargs.pop("timeline", five()), **kwargs)


# ── plan_patch：编译 ───────────────────────────────────────


class TestPlanCompile:
    def test_remove_op_becomes_patch_op(self):
        plan = plan_of(IntentOp(op="remove", target=IntentTarget(all=True)), ["c1"])
        assert [o.op for o in plan.ops] == ["remove"]
        assert plan.ops[0].clip_ids == ["c1"]

    def test_stale_clip_ids_are_dropped_with_note(self):
        """落位到执行之间时间线可能已被改（补丁过期）——过期 id 必须丢掉并说明。"""
        tl = five()
        op = IntentOp(op="remove", target=IntentTarget(all=True))
        intent = EditIntent(ops=[op])
        report = resolution_for(intent, [["c1", "ghost", "phantom"]])
        plan = ia.plan_patch(intent, report, tl)
        assert plan.ops[0].clip_ids == ["c1"]
        assert any("已不在时间线上" in n for n in plan.notes)

    def test_limit_truncates_in_timeline_order(self):
        tl = make_timeline([("z", 20.0, 2.0), ("a", 0.0, 2.0), ("m", 10.0, 2.0)])
        op = IntentOp(op="remove", target=IntentTarget(all=True), limit=2)
        intent = EditIntent(ops=[op])
        report = resolution_for(intent, [["z", "a", "m"]])
        plan = ia.plan_patch(intent, report, tl)
        assert plan.ops[0].clip_ids == ["a", "m"]      # 时间顺序，不是传入顺序

    def test_transition_name_is_normalized(self):
        plan = plan_of(IntentOp(op="transition", target=IntentTarget(all=True), transition="叠化"), ["c0"])
        assert plan.ops[0].transition == "dissolve"

    def test_unknown_transition_name_is_left_empty_for_validator(self):
        plan = plan_of(IntentOp(op="transition", target=IntentTarget(all=True), transition="量子纠缠"), ["c0"])
        assert plan.ops[0].transition == ""

    def test_total_duration_constraint_becomes_fit_step(self):
        intent = EditIntent(ops=[IntentOp(op="remove", target=IntentTarget(all=True))],
                            constraints={"total_duration_sec": 10.0})
        plan = ia.plan_patch(intent, resolution_for(intent, [["c4"]]), five())
        assert [o.op for o in plan.ops] == ["remove", "fit"]
        assert plan.ops[-1].fit_total_sec == 10.0

    def test_op_cap_is_enforced(self):
        ops = [IntentOp(op="remove", target=IntentTarget(all=True)) for _ in range(80)]
        intent = EditIntent(ops=ops)
        report = resolution_for(intent, [["c0"]] * 80)
        plan = ia.plan_patch(intent, report, five())
        assert len(plan.ops) == ia.MAX_PATCH_OPS
        assert any("超过上限" in n for n in plan.notes)


class TestTransitionAliases:
    @pytest.mark.parametrize("alias,expected", [
        ("淡入", "fade"), ("淡出", "fade"), ("叠化", "dissolve"), ("交叉溶解", "dissolve"),
        ("故障", "glitch"), ("滑入", "slide"), ("擦除", "wipe"), ("像素化", "pixel_dissolve"),
        ("硬切", "hard_cut"), ("dissolve", "dissolve"), ("FADE", "fade"), ("", "fade"),
    ])
    def test_known_aliases(self, alias, expected):
        assert ia.normalize_transition(alias) == expected

    @pytest.mark.parametrize("bad", ["量子纠缠", "闪闪发光", "whatever"])
    def test_unknown_returns_none(self, bad):
        assert ia.normalize_transition(bad) is None


# ── remove / keep ─────────────────────────────────────────


class TestRemoveKeep:
    def test_remove_ripples_subsequent_clips(self):
        tl = five()
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="remove", clip_ids=["c1"])])
        new, report = ia.apply_patch(tl, plan)
        assert positions(new) == [("c0", 0.0, 4.0), ("c2", 4.0, 4.0),
                                  ("c3", 8.0, 4.0), ("c4", 12.0, 4.0)]
        assert new.duration_sec == 16.0
        assert report.duration_before == 20.0 and report.duration_after == 16.0
        assert report.removed_clip_ids == ["c1"]
        assert not report.has_errors()

    def test_remove_without_ripple_leaves_the_hole(self):
        tl = five()
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="remove", clip_ids=["c1"])], ripple=False)
        new, _report = ia.apply_patch(tl, plan)
        assert positions(new) == [("c0", 0.0, 4.0), ("c2", 8.0, 4.0),
                                  ("c3", 12.0, 4.0), ("c4", 16.0, 4.0)]
        assert new.duration_sec == 20.0

    def test_ripple_preserves_intentional_gaps(self):
        """刻意留白是设计：只抽掉被删片段的时间，不把幸存片段之间的空档也压掉。"""
        tl = make_timeline([("c0", 0.0, 4.0), ("c1", 6.0, 4.0), ("c2", 10.0, 4.0)])
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="remove", clip_ids=["c1"])])
        new, _report = ia.apply_patch(tl, plan)
        # c1 在 6-10，占 4s；c2 原在 10 → 左移到 6；c0 与 c1 之间原有的 2s 空档保留
        assert positions(new) == [("c0", 0.0, 4.0), ("c2", 6.0, 4.0)]

    def test_ripple_never_creates_overlap(self):
        """原始时间线本来重叠时，左移只允许少移，不允许叠得更狠。"""
        tl = make_timeline([("c0", 0.0, 8.0), ("c1", 2.0, 2.0), ("c2", 6.0, 2.0)])
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="remove", clip_ids=["c1"])])
        new, report = ia.apply_patch(tl, plan)
        assert [p[0] for p in positions(new)] == ["c0", "c2"]
        assert positions(new)[1][1] >= 8.0        # 不早于 c0 的末尾
        # 结果里不允许出现**新**重叠（只按位置判断，不靠文案）
        got = sorted(positions(new), key=lambda p: p[1])
        for prev, cur in zip(got, got[1:]):
            assert cur[1] >= prev[1] + prev[2] - 1e-6

    def test_preexisting_overlap_is_warned_about(self):
        """源时间线本来就有重叠时必须**告警**（真实素材实测踩过）：

        收拢只防"左移压到前一个片段"，防不了"本来就叠在一起"——
        那种情况下被删片段之后的片段会被**右推**，于是"删了一个片段总时长反而变长"。
        这是反直觉的，所以必须让用户看到；但**不改行为**：重叠可能是刻意的叠加。
        """
        tl = make_timeline([("c0", 0.0, 8.0), ("c1", 2.0, 2.0), ("c2", 6.0, 2.0)])
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="remove", clip_ids=["c1"])])
        _new, report = ia.apply_patch(tl, plan)
        warnings = [p for p in report.problems if p.level == "warning"]
        assert any("原本就存在重叠" in p.message for p in warnings), report.problems

    def test_sequential_track_has_no_overlap_warning(self):
        """顺序摆放的时间线（正常情况）不该出现这条告警——否则就是噪音。"""
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="remove", clip_ids=["c1"])])
        _new, report = ia.apply_patch(five(), plan)
        assert not any("原本就存在重叠" in p.message for p in report.problems)

    def test_duplicate_clip_ids_are_warned_about(self):
        """片段 id 重复时补丁会"删一个删一串"——真实素材实测踩过（id 生成规则撞车）。

        必须在**执行前**就告警：等 `_invariant_checks`（执行后）才发现时，
        用户已经多删了内容。
        """
        tl = make_timeline([("dup", 0.0, 2.0), ("dup", 2.0, 2.0), ("c9", 4.0, 2.0)])
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="remove", clip_ids=["dup"])])
        _new, report = ia.apply_patch(tl, plan)
        assert any("重复的片段 id" in p.message for p in report.problems), report.problems

    def test_unique_ids_have_no_duplicate_warning(self):
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="remove", clip_ids=["c1"])])
        _new, report = ia.apply_patch(five(), plan)
        assert not any("重复的片段 id" in p.message for p in report.problems)

    def test_remove_from_untouched_track_changes_nothing(self):
        """只删 A 轨时 B 轨必须一像素不动（收拢是按轨道算的）。"""
        tl = five()
        tl.tracks.append(Track(id="t2", name="V2", kind="video", index=1, clips=[
            Clip(id="b0", kind="video", asset_id="b0.mp4", track_id="t2", start_sec=1.0, duration_sec=2.0),
        ]))
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="remove", clip_ids=["c1"])])
        new, _report = ia.apply_patch(tl, plan)
        assert positions(new, 1) == [("b0", 1.0, 2.0)]

    def test_keep_deletes_everything_else(self):
        tl = five()
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="keep", clip_ids=["c0", "c2"])])
        new, report = ia.apply_patch(tl, plan)
        assert [p[0] for p in positions(new)] == ["c0", "c2"]
        assert positions(new) == [("c0", 0.0, 4.0), ("c2", 4.0, 4.0)]
        assert sorted(report.removed_clip_ids) == ["c1", "c3", "c4"]

    def test_remove_with_empty_target_is_error_not_silent_success(self):
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="remove", clip_ids=[])])
        new, report = ia.apply_patch(five(), plan)
        assert report.has_errors()
        assert len(new.tracks[0].clips) == 5      # 没执行
        assert any("没有匹配到" in p.message for p in report.problems)

    def test_deleting_everything_is_refused(self):
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="remove", clip_ids=[f"c{i}" for i in range(5)])])
        new, report = ia.apply_patch(five(), plan)
        assert report.has_errors()
        assert any("时间线为空" in p.message for p in report.problems)
        assert len(new.tracks[0].clips) == 5

    def test_keep_all_is_a_noop_without_error(self):
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="keep", clip_ids=[f"c{i}" for i in range(5)])])
        new, report = ia.apply_patch(five(), plan)
        assert not report.has_errors()
        assert len(new.tracks[0].clips) == 5


# ── trim ──────────────────────────────────────────────────


class TestTrim:
    def test_absolute_duration(self):
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="trim", clip_ids=["c0"], duration_sec=2.0)])
        new, _report = ia.apply_patch(five(), plan)
        assert positions(new)[0] == ("c0", 0.0, 2.0)

    def test_trim_ripples_to_close_the_hole(self):
        """裁短不补空缺就会留下无人解释的空洞——与删除保持同一条收拢规则。"""
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="trim", clip_ids=["c0"], duration_sec=3.0)])
        new, _report = ia.apply_patch(five(), plan)
        assert positions(new)[:2] == [("c0", 0.0, 3.0), ("c1", 3.0, 4.0)]
        assert new.duration_sec == 19.0

    def test_start_offset_shifts_source_and_duration(self):
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="trim", clip_ids=["c0"], start_offset_sec=1.5)])
        new, _report = ia.apply_patch(five(), plan)
        clip = new.tracks[0].clips[0]
        assert float(clip.duration_sec) == pytest.approx(2.5)
        assert float(clip.source_offset_sec) == pytest.approx(1.5)

    def test_end_offset_trims_tail(self):
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="trim", clip_ids=["c0"], end_offset_sec=1.0)])
        new, _report = ia.apply_patch(five(), plan)
        assert float(new.tracks[0].clips[0].duration_sec) == pytest.approx(3.0)

    def test_trim_to_zero_is_an_error_and_clamped(self):
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="trim", clip_ids=["c0"], duration_sec=0.0)])
        _new, report = ia.apply_patch(five(), plan)
        assert report.has_errors()
        assert any("≤ 0" in p.message for p in report.problems)

    def test_trim_floors_at_min_clip_sec(self):
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="trim", clip_ids=["c0"], duration_sec=0.05)])
        new, _report = ia.apply_patch(five(), plan)
        assert float(new.tracks[0].clips[0].duration_sec) == pytest.approx(ia.MIN_CLIP_SEC)

    def test_trim_without_any_target_value_is_an_error(self):
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="trim", clip_ids=["c0"])])
        _new, report = ia.apply_patch(five(), plan)
        assert report.has_errors()


# ── set ───────────────────────────────────────────────────


class TestSet:
    def test_set_speed_on_all(self):
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="set", clip_ids=[f"c{i}" for i in range(5)],
                                            field="speed", value=1.5)])
        new, report = ia.apply_patch(five(), plan)
        assert all(float(c.speed) == 1.5 for c in new.tracks[0].clips)
        assert not report.has_errors()

    def test_value_is_clamped_by_field_specs(self):
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="set", clip_ids=["c0"], field="volume", value=99)])
        new, _report = ia.apply_patch(five(), plan)
        assert float(new.tracks[0].clips[0].volume) == 1.0

    def test_field_outside_whitelist_is_refused(self):
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="set", clip_ids=["c0"], field="asset_id", value="evil.mp4")])
        new, report = ia.apply_patch(five(), plan)
        assert report.has_errors()
        assert str(new.tracks[0].clips[0].asset_id) == "c0.mp4"

    def test_field_kind_mismatch_warns_instead_of_pretending(self):
        """给视频片段设字体大小不会有任何渲染效果——如实告警，不静默接受。"""
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="set", clip_ids=["c0"], field="font_size", value=64)])
        _new, report = ia.apply_patch(five(), plan)
        assert not report.has_errors()
        assert any("不支持 font_size" in p.message for p in report.problems)
        assert any(p.level == "warning" for p in report.problems)

    def test_text_field_applies_to_caption_clip(self):
        tl = make_timeline([("cap", 0.0, 2.0)], kind="caption")
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="set", clip_ids=["cap"], field="font_size", value=64)])
        new, report = ia.apply_patch(tl, plan)
        assert float(new.tracks[0].clips[0].font_size) == 64.0
        assert not any("不支持" in p.message for p in report.problems)


class TestSpeedAdjustsDuration:
    """变速必须**同时改时长**——这是"审计角色"在人工评分表里直接指出的问题。

    渲染端对 speed 的处理是 `setpts=PTS/speed`（见 `services/render.py`），
    1.5 倍速**真的会把这段缩短到 2/3**；`duration_sec` 不变的话，时间线模型说的时长
    与渲染输出不一致，后面所有片段的起止位置也全错。
    """

    def test_faster_speeds_shrink_and_pull_up(self):
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="set", clip_ids=["c0", "c1", "c2"],
                                            field="speed", value=1.5)])
        new, report = ia.apply_patch(make_timeline([("c0", 0.0, 3.0), ("c1", 3.0, 3.0),
                                                    ("c2", 6.0, 3.0)]), plan)
        assert not report.has_errors()
        got = positions(new)
        assert [(p[0], p[1], p[2]) for p in got] == [("c0", 0.0, 2.0), ("c1", 2.0, 2.0),
                                                    ("c2", 4.0, 2.0)]
        assert new.duration_sec == pytest.approx(6.0)
        assert any("变速同时调整时长" in line for line in report.preview)

    def test_slower_speeds_grow_and_push_right(self):
        """变慢是**反方向**：后续片段必须右推，否则与前一个重叠。"""
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="set", clip_ids=["c0", "c1", "c2"],
                                            field="speed", value=0.5)])
        new, _report = ia.apply_patch(make_timeline([("c0", 0.0, 3.0), ("c1", 3.0, 3.0),
                                                     ("c2", 6.0, 3.0)]), plan)
        got = positions(new)
        assert [(p[0], p[1], p[2]) for p in got] == [("c0", 0.0, 6.0), ("c1", 6.0, 6.0),
                                                    ("c2", 12.0, 6.0)]
        assert new.duration_sec == pytest.approx(18.0)
        # 不许重叠
        ordered = sorted(got, key=lambda p: p[1])
        for prev, cur in zip(ordered, ordered[1:]):
            assert cur[1] >= prev[1] + prev[2] - 1e-6

    def test_same_speed_is_a_noop_on_duration(self):
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="set", clip_ids=["c0"], field="speed", value=1.0)])
        new, report = ia.apply_patch(five(), plan)
        assert float(new.tracks[0].clips[0].duration_sec) == 4.0
        assert not any("变速同时调整时长" in line for line in report.preview)

    def test_speed_change_keeps_min_clip_sec(self):
        """极慢的素材被压到极快时，时长不许低于最小时长。"""
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="set", clip_ids=["c0"], field="speed", value=4.0)])
        new, _report = ia.apply_patch(make_timeline([("c0", 0.0, 0.5)]), plan)
        assert float(new.tracks[0].clips[0].duration_sec) >= ia.MIN_CLIP_SEC

    def test_non_speed_fields_do_not_touch_duration(self):
        """只有 speed 会耦合时长；亮度等字段不该动时长。"""
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="set", clip_ids=["c0"],
                                            field="fx_brightness", value=1.2)])
        new, _report = ia.apply_patch(five(), plan)
        assert float(new.tracks[0].clips[0].duration_sec) == 4.0
        assert float(new.tracks[0].clips[0].fx_brightness) == pytest.approx(1.2)


# ── reorder ───────────────────────────────────────────────


class TestReorder:
    def test_reorder_by_time_is_stable(self):
        tl = make_timeline([("a", 0.0, 4.0), ("b", 4.0, 4.0), ("c", 8.0, 4.0)])
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="reorder", clip_ids=["a", "b", "c"], by="time")])
        new, _report = ia.apply_patch(tl, plan)
        assert [p[0] for p in positions(new)] == ["a", "b", "c"]

    def test_reorder_by_time_is_always_ascending(self):
        """`desc` 是给统计排序用的默认值；套到时间上会变成"倒序播放"，没人想要。"""
        tl = make_timeline([("a", 0.0, 4.0), ("b", 4.0, 4.0), ("c", 8.0, 4.0)])
        op = IntentOp(op="reorder", by="time", desc=True)
        intent = EditIntent(ops=[op])
        plan = ia.plan_patch(intent, resolution_for(intent, [["a", "b", "c"]]), tl)
        assert plan.ops[0].desc is False

    def test_reorder_by_stat_uses_index_stats(self):
        tl = make_timeline([("a", 0.0, 4.0), ("b", 4.0, 4.0), ("c", 8.0, 4.0)])
        stats = {"a": {"sharpness": 1.0}, "b": {"sharpness": 9.0}, "c": {"sharpness": 5.0}}
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="reorder", clip_ids=["a", "b", "c"],
                                            by="sharpness", desc=True)])
        new, _report = ia.apply_patch(tl, plan, stats=stats)
        assert [p[0] for p in positions(new)] == ["b", "c", "a"]     # 最锐在前

    def test_reorder_preserves_gap_rhythm(self):
        """重排只换顺序，不换"留白节奏"：原有的间隙序列按位置沿用。"""
        tl = make_timeline([("a", 0.0, 2.0), ("b", 3.0, 2.0), ("c", 10.0, 2.0)])
        stats = {"a": {"sharpness": 1.0}, "b": {"sharpness": 9.0}, "c": {"sharpness": 5.0}}
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="reorder", clip_ids=["a", "b", "c"],
                                            by="sharpness", desc=True)])
        new, _report = ia.apply_patch(tl, plan, stats=stats)
        got = positions(new)
        assert [p[0] for p in got] == ["b", "c", "a"]
        # 第 1/2 个间隙沿用（1s 与 5s），整组从最早的 0s 起，不整体平移
        assert (got[0][1], got[1][1], got[2][1]) == (0.0, 3.0, 10.0)

    def test_reorder_without_stats_warns_about_missing_index(self):
        tl = make_timeline([("a", 0.0, 2.0), ("b", 2.0, 2.0)])
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="reorder", clip_ids=["a", "b"], by="brightness")])
        _new, report = ia.apply_patch(tl, plan)      # 没传 stats
        assert any("没有内容索引" in p.message for p in report.problems)

    def test_reorder_with_partial_stats_reports_the_gap(self):
        tl = make_timeline([("a", 0.0, 2.0), ("b", 2.0, 2.0), ("c", 4.0, 2.0)])
        stats = {"a": {"brightness": 10.0}, "b": {"brightness": 200.0}}   # c 没有索引
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="reorder", clip_ids=["a", "b", "c"], by="brightness")])
        _new, report = ia.apply_patch(tl, plan, stats=stats)
        assert any("1 个片段没有内容索引" in p.message for p in report.problems)

    def test_unknown_order_key_falls_back_to_time_with_warning(self):
        tl = make_timeline([("a", 0.0, 2.0), ("b", 2.0, 2.0)])
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="reorder", clip_ids=["a", "b"], by="vibes")])
        _new, report = ia.apply_patch(tl, plan)
        assert any("不支持" in p.message for p in report.problems)


# ── add ───────────────────────────────────────────────────


class TestAdd:
    def test_caption_needs_text_not_asset(self):
        tl = make_timeline([("v", 0.0, 4.0)])
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="add", new_clip={
            "kind": "caption", "text": "本场最佳", "duration_sec": 2.0, "position": "end"})])
        new, report = ia.apply_patch(tl, plan)
        assert not report.has_errors()
        captions = [(t, c) for t in new.tracks for c in t.clips if str(c.kind) == "caption"]
        assert len(captions) == 1
        track, clip = captions[0]
        assert str(clip.text) == "本场最佳"
        assert str(track.kind) == "caption"          # 字幕不该塞进视频轨
        assert float(clip.start_sec) == 0.0          # 新建的字幕轨从 0 起
        assert len(new.tracks[0].clips) == 1         # 视频轨没被动过

    def test_new_caption_track_is_created_when_absent(self):
        tl = five()
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="add", new_clip={
            "kind": "caption", "text": "标题", "duration_sec": 2.0})])
        new, _report = ia.apply_patch(tl, plan)
        kinds = [str(t.kind) for t in new.tracks]
        assert "caption" in kinds
        assert len(new.tracks) == 2

    def test_media_add_without_asset_is_refused(self):
        """凭空造一个素材路径就是假数据——宁可报错。"""
        tl = five()
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="add", new_clip={
            "kind": "audio", "asset_id": "", "duration_sec": 3.0})])
        _new, report = ia.apply_patch(tl, plan)
        assert report.has_errors()
        assert any("需要真实素材" in p.message for p in report.problems)

    def test_media_add_with_asset_succeeds(self):
        tl = five()
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="add", new_clip={
            "kind": "audio", "asset_id": "bgm.mp3", "duration_sec": 3.0})])
        new, report = ia.apply_patch(tl, plan)
        assert not report.has_errors()
        assert any(str(t.kind) == "audio" for t in new.tracks)

    def test_blank_caption_text_is_refused(self):
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="add", new_clip={
            "kind": "caption", "text": "   ", "duration_sec": 2.0})])
        _new, report = ia.apply_patch(five(), plan)
        assert report.has_errors()

    def test_unknown_add_kind_is_refused(self):
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="add", new_clip={"kind": "hologram"})])
        _new, report = ia.apply_patch(five(), plan)
        assert report.has_errors()

    def test_overlapping_add_warns(self):
        tl = five()
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="add", new_clip={
            "kind": "video", "asset_id": "x.mp4", "duration_sec": 4.0, "position": "start"})])
        _new, report = ia.apply_patch(tl, plan)
        assert any("重叠" in p.message for p in report.problems)
        assert not report.has_errors()      # 重叠只告警：叠加渲染在编辑器里是合法需求


# ── transition ────────────────────────────────────────────


class TestTransition:
    def test_sets_transition_in(self):
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="transition", clip_ids=["c0", "c1"],
                                           transition="dissolve", duration_sec=0.5)])
        new, report = ia.apply_patch(five(), plan)
        clip = new.tracks[0].clips[0]
        assert str(clip.transition_in) == "dissolve"
        assert float(clip.transition_duration_sec) == 0.5
        assert not report.has_errors()

    def test_transition_out_flag(self):
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="transition", clip_ids=["c0"],
                                           transition="fade", transition_out=True)])
        new, _report = ia.apply_patch(five(), plan)
        assert str(new.tracks[0].clips[0].transition_out) == "fade"

    def test_unknown_transition_is_an_error(self):
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="transition", clip_ids=["c0"], transition="")])
        _new, report = ia.apply_patch(five(), plan)
        assert report.has_errors()
        assert any("转场名不认识" in p.message for p in report.problems)


# ── fit（总时长约束）───────────────────────────────────────


class TestFitTotal:
    def test_trims_from_the_tail(self):
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="fit", fit_total_sec=10.0)])
        new, report = ia.apply_patch(five(), plan)
        assert new.duration_sec == pytest.approx(10.0)
        assert [p[0] for p in positions(new)] == ["c0", "c1", "c2"]
        assert positions(new)[-1] == ("c2", 8.0, 2.0)
        assert not report.has_errors()
        assert any("总时长" in line for line in report.preview)

    def test_shorter_target_than_one_clip_still_leaves_one(self):
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="fit", fit_total_sec=3.0)])
        new, report = ia.apply_patch(five(), plan)
        assert not report.has_errors()
        assert new.duration_sec == pytest.approx(3.0)
        assert len(new.tracks[0].clips) == 1

    def test_target_longer_than_current_is_a_noop(self):
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="fit", fit_total_sec=60.0)])
        new, report = ia.apply_patch(five(), plan)
        assert new.duration_sec == pytest.approx(20.0)
        assert not report.has_errors()
        assert any("无需裁剪" in line for line in report.preview)

    def test_zero_target_is_refused(self):
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="fit", fit_total_sec=0.0)])
        _new, report = ia.apply_patch(five(), plan)
        assert report.has_errors()

    def test_order_of_clips_is_not_changed_by_fit(self):
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="fit", fit_total_sec=6.0)])
        new, _report = ia.apply_patch(five(), plan)
        assert [p[0] for p in positions(new)] == ["c0", "c1"]


# ── dry-run 只读性与门禁 ──────────────────────────────────


class TestDryRun:
    def test_validate_patch_does_not_touch_the_input(self):
        tl = five()
        before = tl.model_dump()
        plan = ia.PatchPlan(ops=[
            ia.PatchOp(op="remove", clip_ids=["c1"]),
            ia.PatchOp(op="set", clip_ids=["c0"], field="speed", value=2.0),
            ia.PatchOp(op="fit", fit_total_sec=8.0),
        ])
        report = ia.validate_patch(tl, plan)
        assert tl.model_dump() == before         # 预览绝不能改时间线
        # 5 片段 / 20s → 删 1 个（16s）→ c0 变速 2× 变 2s 并收拢（14s）→ 收到 8s 剩 3 个。
        # 变速那一环现在会**同时改时长**（见 TestSpeedAdjustsDuration），所以这里的数字
        # 与"变速不改时长"的旧行为不同。
        assert report.clips_after == 3
        assert report.duration_after == pytest.approx(8.0)
        assert report.dry_run is True

    def test_validate_reports_before_and_after_numbers(self):
        tl = five()
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="remove", clip_ids=["c1"])])
        report = ia.validate_patch(tl, plan)
        assert (report.clips_before, report.clips_after) == (5, 4)
        assert (report.duration_before, report.duration_after) == (20.0, 16.0)

    def test_preview_first_line_summarizes_the_change(self):
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="remove", clip_ids=["c1"])])
        report = ia.validate_patch(five(), plan)
        # "片段数 N → M"：写成"片段 N → M 个"会有歧义（"片段 2" 既可读成数量
        # 也可读成"第 2 个片段"——实测审计角色就把它读出歧义并报了问题）。
        assert "片段数 5 → 4" in report.preview[0]
        assert "20.0s → 16.0s" in report.preview[0]

    def test_apply_with_errors_returns_original_untouched(self):
        tl = five()
        plan = ia.PatchPlan(ops=[
            ia.PatchOp(op="remove", clip_ids=["c1"]),           # 合法
            ia.PatchOp(op="set", clip_ids=["c0"], field="nope", value=1),  # 非法
        ])
        new, report = ia.apply_patch(tl, plan)
        assert report.has_errors()
        # 要么全做要么不做：合法的前半段也不许落地
        assert [p[0] for p in positions(new)] == [f"c{i}" for i in range(5)]

    def test_affected_ids_cover_removed_added_and_changed(self):
        tl = five()
        plan = ia.PatchPlan(ops=[
            ia.PatchOp(op="remove", clip_ids=["c1"]),
            ia.PatchOp(op="set", clip_ids=["c0"], field="speed", value=2.0),
            ia.PatchOp(op="add", new_clip={"kind": "caption", "text": "x", "duration_sec": 1.0}),
        ])
        _new, report = ia.apply_patch(tl, plan)
        assert "c1" in report.affected_clip_ids
        assert "c0" in report.affected_clip_ids
        assert len(report.added_clip_ids) == 1
        assert report.tracks_touched


class TestInvariants:
    def test_result_duration_is_recalculated(self):
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="remove", clip_ids=["c4"])])
        new, _report = ia.apply_patch(five(), plan)
        assert new.duration_sec == pytest.approx(16.0)

    def test_empty_result_is_flagged(self):
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="keep", clip_ids=[])])
        _new, report = ia.apply_patch(five(), plan)
        assert report.has_errors()

    def test_problems_carry_level_and_where(self):
        plan = ia.PatchPlan(ops=[ia.PatchOp(op="set", clip_ids=["c0"], field="nope", value=1)])
        _new, report = ia.apply_patch(five(), plan)
        err = [p for p in report.problems if p.level == "error"]
        assert err and err[0].where.startswith("ops[0]")


# ── 答案回填 ──────────────────────────────────────────────


class TestApplyAnswers:
    def test_no_match_answer_becomes_explicit_ref(self):
        intent = EditIntent(ops=[IntentOp(op="remove", target=IntentTarget(selected=True))])
        out, notes = ia.apply_answers(intent, {"no_match": "c3"}, five())
        assert out.ops[0].target.ref == ["c3"]
        assert any("选定的片段" in n for n in notes)

    def test_no_match_with_unknown_clip_is_ignored(self):
        intent = EditIntent(ops=[IntentOp(op="remove", target=IntentTarget(selected=True))])
        out, _notes = ia.apply_answers(intent, {"no_match": "ghost"}, five())
        assert out.ops[0].target.selected is True

    def test_too_many_first10_sets_limit(self):
        intent = EditIntent(ops=[IntentOp(op="remove", target=IntentTarget(all=True))])
        out, notes = ia.apply_answers(intent, {"too_many": "first10"}, five())
        assert out.ops[0].limit == 10
        assert any("前 10 个" in n for n in notes)

    def test_too_many_manual_abandons_execution(self):
        intent = EditIntent(ops=[IntentOp(op="remove", target=IntentTarget(all=True))])
        out, notes = ia.apply_answers(intent, {"too_many": "manual"}, five())
        assert out.ops == []
        assert any("放弃" in n for n in notes)

    def test_per_op_answer_keys_win(self):
        intent = EditIntent(ops=[
            IntentOp(op="remove", target=IntentTarget(selected=True)),
            IntentOp(op="remove", target=IntentTarget(selected=True)),
        ])
        out, _notes = ia.apply_answers(intent, {"no_match:1": "c2"}, five())
        assert out.ops[0].target.ref == []
        assert out.ops[1].target.ref == ["c2"]

    def test_original_intent_is_not_mutated(self):
        intent = EditIntent(ops=[IntentOp(op="remove", target=IntentTarget(all=True))])
        ia.apply_answers(intent, {"too_many": "first10"}, five())
        assert intent.ops[0].limit is None


# ── 统计取值 ──────────────────────────────────────────────


class TestShotStats:
    def test_no_indexes_yields_empty_stats(self):
        assert ia.shot_stats_for_timeline(five(), []) == {}

    def test_stats_lookup_uses_source_offset(self):
        from clipwright.schema.content import ContentIndex, ShotRecord, ShotStats

        idx = ContentIndex(
            sha256="a" * 64, asset_path="c0.mp4",
            shots=[
                ShotRecord(index=0, t0=0.0, t1=1.0, duration_sec=1.0,
                           stats=ShotStats(sharpness=1.0)),
                ShotRecord(index=1, t0=1.0, t1=4.0, duration_sec=3.0,
                           stats=ShotStats(sharpness=42.0)),
            ])
        tl = make_timeline([("c0", 0.0, 4.0)])
        tl.tracks[0].clips[0].source_offset_sec = 2.0
        stats = ia.shot_stats_for_timeline(tl, [idx])
        assert stats["c0"]["sharpness"] == pytest.approx(42.0)

    def test_clip_without_index_is_absent_from_stats(self):
        from clipwright.schema.content import ContentIndex, ShotRecord, ShotStats

        idx = ContentIndex(sha256="b" * 64, asset_path="other.mp4",
                           shots=[ShotRecord(index=0, t0=0.0, t1=1.0,
                                             duration_sec=1.0, stats=ShotStats(sharpness=5.0))])
        tl = make_timeline([("c0", 0.0, 4.0)])
        assert ia.shot_stats_for_timeline(tl, [idx]) == {}


# ── 端到端（plan_validate_apply）──────────────────────────


class TestEndToEnd:
    @pytest.mark.asyncio
    async def test_one_sentence_remove_runs_through(self):
        tl = five()
        result = await ia.plan_validate_apply("删掉第2个片段", timeline=tl, session_id="s1", apply=True)
        assert result["applied"] is True
        assert result["blocked"] is False
        new_positions = [(c["id"], c["start_sec"]) for c in result["timeline"]["tracks"][0]["clips"]]
        assert new_positions == [("c0", 0.0), ("c2", 4.0), ("c3", 8.0), ("c4", 12.0)]

    @pytest.mark.asyncio
    async def test_one_sentence_speed_runs_through(self):
        tl = five()
        result = await ia.plan_validate_apply("把所有片段速度调成1.5倍", timeline=tl,
                                              session_id="s1", apply=True)
        assert result["applied"] is True
        assert all(c["speed"] == 1.5 for c in result["timeline"]["tracks"][0]["clips"])

    @pytest.mark.asyncio
    async def test_one_sentence_duration_runs_through(self):
        tl = five()
        result = await ia.plan_validate_apply("剪到10秒", timeline=tl, session_id="s1", apply=True)
        assert result["applied"] is True
        assert result["timeline"]["duration_sec"] == pytest.approx(10.0)
        assert len(result["timeline"]["tracks"][0]["clips"]) == 3

    @pytest.mark.asyncio
    async def test_dry_run_does_not_apply(self):
        tl = five()
        result = await ia.plan_validate_apply("删掉第2个片段", timeline=tl, session_id="s1")
        assert result["applied"] is False
        assert "timeline" not in result
        assert len(tl.tracks[0].clips) == 5

    @pytest.mark.asyncio
    async def test_unresolved_ambiguity_blocks_execution(self):
        """没问清就不动手：无选中时"裁到3秒"必须拦住。"""
        tl = five()
        result = await ia.plan_validate_apply("裁到3秒", timeline=tl, session_id="s1", apply=True)
        assert result["blocked"] is True
        assert result["applied"] is False
        assert len(tl.tracks[0].clips) == 5

    @pytest.mark.asyncio
    async def test_answers_unblock_execution(self):
        tl = five()
        result = await ia.plan_validate_apply("裁到3秒", timeline=tl, session_id="s1",
                                              answers={"no_match": "c0"}, apply=True)
        assert result["applied"] is True
        assert float(result["timeline"]["tracks"][0]["clips"][0]["duration_sec"]) == pytest.approx(3.0)

    @pytest.mark.asyncio
    async def test_hallucinated_op_is_blocked_before_any_patch(self):
        tl = five()
        result = await ia.plan_validate_apply("把这个视频炸开成碎片", timeline=tl,
                                              session_id="s1", apply=True)
        assert result["blocked"] is True
        assert result["applied"] is False
        assert result["patch"]["ops"] == []
        assert len(tl.tracks[0].clips) == 5

    @pytest.mark.asyncio
    async def test_intent_raw_reuse_skips_reparsing(self):
        """确认执行时用的必须是用户确认过的那份意图，而不是重新解析的。"""
        tl = five()
        first = await ia.plan_validate_apply("删掉第2个片段", timeline=tl, session_id="s1")
        raw = first["intent_raw"]
        second = await ia.plan_validate_apply("随便一句话", timeline=tl, session_id="s1",
                                              intent_raw=raw, apply=True)
        assert second["applied"] is True
        assert [c["id"] for c in second["timeline"]["tracks"][0]["clips"]] == ["c0", "c2", "c3", "c4"]

    @pytest.mark.asyncio
    async def test_broken_intent_raw_falls_back_to_parsing(self):
        tl = five()
        result = await ia.plan_validate_apply("删掉第2个片段", timeline=tl, session_id="s1",
                                              intent_raw={"ops": "not-a-list"}, apply=True)
        assert result["applied"] is True

    @pytest.mark.asyncio
    async def test_empty_timeline_does_not_crash(self):
        empty = Timeline(id="e")
        result = await ia.plan_validate_apply("删掉第2个片段", timeline=empty,
                                              session_id="s1", apply=True)
        assert result["applied"] is False
        assert result["blocked"] is True

    @pytest.mark.asyncio
    async def test_session_shortlist_updates_after_apply(self):
        """执行后短名单要更新，否则下一句"刚才那几段"指的还是老片段。"""
        from clipwright.services.intent_resolver import reset_session_state, session_state

        reset_session_state("s_short")
        tl = five()
        await ia.plan_validate_apply("去掉 c2", timeline=tl, session_id="s_short", apply=True)
        assert session_state("s_short").shortlists


# ── API 层 ────────────────────────────────────────────────


@pytest.fixture()
def api(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from clipwright.api import requirements as req_api

    monkeypatch.setattr(req_api, "_require_session_owner", lambda request, sid: None)
    app = FastAPI()
    app.include_router(req_api.router)
    return TestClient(app)


class TestApplyApi:
    def _body(self, **over):
        body = {"message": "删掉第2个片段", "timeline": five().model_dump(),
                "selected_clip_ids": []}
        body.update(over)
        return body

    def test_dry_run_endpoint_is_readonly(self, api) -> None:
        r = api.post("/api/requirements/s1/intent/apply", json=self._body())
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["applied"] is False
        assert "timeline" not in body                       # 没执行就不该回传新时间线
        assert body["report"]["clips_after"] == 4
        assert "session_id" in body

    def test_apply_endpoint_returns_new_timeline(self, api) -> None:
        r = api.post("/api/requirements/s1/intent/apply", json=self._body(apply=True))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["applied"] is True and body["blocked"] is False
        clips = body["timeline"]["tracks"][0]["clips"]
        assert [c["id"] for c in clips] == ["c0", "c2", "c3", "c4"]
        assert clips[1]["start_sec"] == 4.0

    def test_invalid_timeline_400(self, api) -> None:
        r = api.post("/api/requirements/s1/intent/apply",
                     json={"message": "x", "timeline": {"tracks": "nope"}})
        assert r.status_code == 400

    def test_unparsed_message_is_blocked_with_reason(self, api) -> None:
        r = api.post("/api/requirements/s1/intent/apply",
                     json=self._body(message="让它更有感染力", apply=True))
        body = r.json()
        assert body["applied"] is False and body["blocked"] is True
        assert any("没能从这句话里解析出" in p["message"] for p in body["problems"])

    def test_answers_are_applied(self, api) -> None:
        r = api.post("/api/requirements/s1/intent/apply",
                     json=self._body(message="裁到3秒", answers={"no_match": "c0"}, apply=True))
        body = r.json()
        assert body["applied"] is True
        assert body["timeline"]["tracks"][0]["clips"][0]["duration_sec"] == 3.0

    def test_intent_raw_is_reused_not_reparsed(self, api) -> None:
        first = api.post("/api/requirements/s1/intent/apply", json=self._body()).json()
        r = api.post("/api/requirements/s1/intent/apply",
                     json=self._body(message="完全另一句话", intent_raw=first["intent_raw"],
                                     apply=True))
        body = r.json()
        assert body["applied"] is True
        assert [c["id"] for c in body["timeline"]["tracks"][0]["clips"]] == ["c0", "c2", "c3", "c4"]
