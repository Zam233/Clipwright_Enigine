"""R5：自然语言剪辑意图（解析 / 校验 / 描述）测试。

分组：
1. **字段规格唯一性**：`requirements_service._ADJUST_FIELDS` 必须就是 `edit_intent.FIELD_SPECS`
   （不许出现第二份白名单）；
2. **中文数字**：常规写法 + 小数 + 非法输入；
3. **规则解析**：总时长/画幅/不重复素材、删除、只保留、排序、加法、转场、裁剪、速度、音量；
4. **内容条件复用**：`_target_from_phrase` 必须走 R4 的 `route_query`（"黑场"→结构化条件）；
5. **校验**：未知 op / 未知字段 / 值域钳制 / 非法时间区间 / 非法枚举 / 未知重排依据 /
   未知新增类型 / 约束校验 / 上限 / 不可变性；
6. **LLM 路径**：结构化输出 → 意图；不可用/非法/异常时返回空意图而不抛；
7. **统一入口**：规则的优先级、回退条件、必须过校验；
8. **描述**：人话总结能被确认卡直接用。
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from clipwright.services import edit_intent as ei


# ── 1. 字段规格唯一性 ─────────────────────────────────────


class TestFieldSpecsSingleSource:
    def test_requirements_service_reuses_specs(self) -> None:
        from clipwright.services.requirements_service import _ADJUST_FIELDS

        assert set(_ADJUST_FIELDS) == set(ei.FIELD_SPECS)
        # 值域行为必须一致（不是"复制了一份看起来一样"）
        for field_name in ("speed", "volume", "font_size"):
            _kind, fn = _ADJUST_FIELDS[field_name]
            _kind2, fn2 = ei.FIELD_SPECS[field_name]
            assert fn(-999) == fn2(-999)
            assert fn(99999) == fn2(99999)

    def test_legacy_alias_exists(self) -> None:
        assert ei._ADJUST_FIELDS is ei.FIELD_SPECS

    def test_core_fields_present(self) -> None:
        for field_name in ("speed", "volume", "opacity", "duration_sec", "start_sec",
                           "font_size", "font_color", "text"):
            assert field_name in ei.FIELD_SPECS


# ── 2. 中文数字 ───────────────────────────────────────────


class TestChineseNumbers:
    @pytest.mark.parametrize("text,expect", [
        ("60", 60.0), ("1.5", 1.5), ("三", 3.0), ("十", 10.0), ("十五", 15.0),
        ("二十三", 23.0), ("两", 2.0), ("一百", 100.0), ("一百二", 102.0),
        ("三十", 30.0), ("九十", 90.0),
    ])
    def test_parses(self, text: str, expect: float) -> None:
        assert ei.parse_cn_number(text) == expect

    def test_rejects_garbage(self) -> None:
        for bad in ("", "abc", "秒", "六十秒"):
            assert ei.parse_cn_number(bad) is None

    def test_rejects_long_digit_only_chains(self) -> None:
        assert ei.parse_cn_number("一二三") is None


# ── 3. 规则解析 ───────────────────────────────────────────


class TestParseRules:
    def test_total_duration(self) -> None:
        intent = ei.parse_intent_rules("剪到 60 秒")
        assert intent.constraints.total_duration_sec == 60.0

    def test_total_duration_chinese(self) -> None:
        assert ei.parse_intent_rules("控制在四十五秒").constraints.total_duration_sec == 45.0

    def test_platform(self) -> None:
        assert ei.parse_intent_rules("换成竖屏").constraints.platform == "竖屏"

    def test_no_reuse(self) -> None:
        assert ei.parse_intent_rules("同一素材只用一次").constraints.no_reuse is True

    def test_remove_black(self) -> None:
        intent = ei.parse_intent_rules("去掉黑场")
        assert len(intent.ops) == 1
        op = intent.ops[0]
        assert op.op == "remove" and op.target.filter == {"is_black": True}
        assert not intent.unresolved

    def test_keep_with_face(self) -> None:
        intent = ei.parse_intent_rules("只保留有人脸的镜头")
        assert intent.ops[0].op == "keep" and intent.ops[0].target.filter == {"has_face": True}

    def test_keep_with_query(self) -> None:
        intent = ei.parse_intent_rules("只保留咖啡杯特写")
        op = intent.ops[0]
        assert op.op == "keep" and "咖啡杯" in op.target.query

    def test_remove_loud_audio(self) -> None:
        intent = ei.parse_intent_rules("把掌声那几段删掉")
        assert intent.ops[0].op == "remove"
        assert intent.ops[0].target.filter == {"event_kinds": ["loud"]}

    def test_reorder_by_motion_desc(self) -> None:
        intent = ei.parse_intent_rules("按运动量从大到小重排")
        assert intent.ops[0].op == "reorder" and intent.ops[0].by == "motion"
        assert intent.ops[0].desc is True

    def test_reorder_ascending(self) -> None:
        assert ei.parse_intent_rules("按时长从小到大排序").ops[0].desc is False

    def test_add_caption_at_start(self) -> None:
        intent = ei.parse_intent_rules("给开头加个字幕")
        op = intent.ops[0]
        assert op.op == "add" and op.kind == "caption"
        assert op.position == "start" and op.target.index == [0]

    def test_add_music_at_end(self) -> None:
        op = ei.parse_intent_rules("结尾加个背景音乐").ops[0]
        assert op.op == "add" and op.kind == "audio" and op.position == "end"

    def test_transition(self) -> None:
        op = ei.parse_intent_rules("转场改成淡入淡出").ops[0]
        assert op.op == "transition" and op.transition == "淡入淡出"

    def test_speed_multiplier(self) -> None:
        op = ei.parse_intent_rules("把选中的速度调到 1.5 倍").ops[0]
        assert op.op == "set" and op.field == "speed" and op.value == 1.5
        assert op.target.selected is True

    def test_speed_x_suffix(self) -> None:
        assert ei.parse_intent_rules("加速到 2x").ops[0].value == 2.0

    def test_volume_up_down(self) -> None:
        assert ei.parse_intent_rules("音量调大一点").ops[0].value > 0.5
        assert ei.parse_intent_rules("音量调小一点").ops[0].value < 0.5

    def test_trim(self) -> None:
        op = ei.parse_intent_rules("把这段裁到 3 秒").ops[0]
        assert op.op == "trim" and op.duration_sec == 3.0

    def test_combined_instruction(self) -> None:
        intent = ei.parse_intent_rules("去掉黑场，剪到 60 秒")
        assert intent.constraints.total_duration_sec == 60.0
        assert intent.ops and intent.ops[0].op == "remove"

    def test_unresolved_when_nothing_parsed(self) -> None:
        intent = ei.parse_intent_rules("随便剪一下")
        assert intent.ops == [] and intent.unresolved

    def test_empty_message(self) -> None:
        intent = ei.parse_intent_rules("")
        assert intent.ops == [] and intent.unresolved == [] and intent.source == "rules"

    def test_source_is_rules(self) -> None:
        assert ei.parse_intent_rules("剪到 30 秒").source == "rules"


# ── 4. 内容条件复用 R4 路由 ───────────────────────────────


class TestTargetRouting:
    def test_filter_phrase_becomes_structured_condition(self) -> None:
        target = ei._target_from_phrase("黑场")
        assert target.filter == {"is_black": True} and not target.query

    def test_query_phrase_keeps_both_signals(self) -> None:
        """路由对"特写"这类词会给出结构化条件——保留它、同时保留文本查询，
        两条信号叠加比只留一个更准（实测：单靠关键词对"特写"召回不稳）。"""
        target = ei._target_from_phrase("咖啡杯特写")
        assert "咖啡杯" in target.query
        assert target.filter == {"shot_size": ["extreme_close", "close"]}

    def test_mixed_phrase_keeps_both(self) -> None:
        target = ei._target_from_phrase("有人脸的镜头")
        assert target.filter == {"has_face": True}

    def test_empty_phrase_targets_all(self) -> None:
        assert ei._target_from_phrase("").all is True

    def test_overexposed_maps_to_ratio(self) -> None:
        assert ei._target_from_phrase("过曝的镜头").filter == {"min_overexposed": 0.3}

    def test_router_failure_degrades_to_query(self, monkeypatch) -> None:
        """路由不可用（导入失败）时退化为自然语言查询，不能抛。"""
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *a, **k):
            if name == "clipwright.services.content_query":
                raise ImportError("boom")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        target = ei._target_from_phrase("黑场")
        assert target.query == "黑场"


# ── 5. 校验 ───────────────────────────────────────────────


class TestValidate:
    def test_unknown_op_rejected(self) -> None:
        intent = ei.EditIntent(ops=[ei.IntentOp(op="delete_everything")])
        norm, problems = ei.validate_intent(intent)
        assert norm.ops == []
        assert ei.has_errors(problems)
        assert "不支持的操作" in problems[0].message

    def test_unknown_field_rejected(self) -> None:
        intent = ei.EditIntent(ops=[ei.IntentOp(op="set", target=ei.IntentTarget(all=True),
                                               field="render_engine", value="x")])
        norm, problems = ei.validate_intent(intent)
        assert norm.ops == [] and ei.has_errors(problems)
        assert "不可通过指令修改" in problems[0].message

    def test_value_clamped_with_warning(self) -> None:
        intent = ei.EditIntent(ops=[ei.IntentOp(op="set", target=ei.IntentTarget(all=True),
                                               field="speed", value=99)])
        norm, problems = ei.validate_intent(intent)
        assert norm.ops[0].value == 4.0
        assert not ei.has_errors(problems) and any(p.level == "warning" for p in problems)

    def test_bad_value_type_is_error(self) -> None:
        intent = ei.EditIntent(ops=[ei.IntentOp(op="set", target=ei.IntentTarget(all=True),
                                               field="speed", value="很快")])
        norm, problems = ei.validate_intent(intent)
        assert norm.ops == [] and ei.has_errors(problems)

    def test_enum_field_rejects_unknown(self) -> None:
        intent = ei.EditIntent(ops=[ei.IntentOp(op="set", target=ei.IntentTarget(all=True),
                                               field="image_fit", value="stretch")])
        norm, problems = ei.validate_intent(intent)
        assert norm.ops == [] and ei.has_errors(problems)

    def test_hex_color_enforced(self) -> None:
        intent = ei.EditIntent(ops=[ei.IntentOp(op="set", target=ei.IntentTarget(all=True),
                                               field="font_color", value="red")])
        assert ei.has_errors(ei.validate_intent(intent)[1])
        ok = ei.EditIntent(ops=[ei.IntentOp(op="set", target=ei.IntentTarget(all=True),
                                            field="font_color", value="#ff8800")])
        assert ei.validate_intent(ok)[0].ops[0].value == "#FF8800"

    def test_missing_target_for_structural_op(self) -> None:
        intent = ei.EditIntent(ops=[ei.IntentOp(op="remove")])
        norm, problems = ei.validate_intent(intent)
        assert norm.ops == [] and any("必须指定对象" in p.message for p in problems)

    def test_invalid_time_range(self) -> None:
        intent = ei.EditIntent(ops=[ei.IntentOp(op="remove",
                                                target=ei.IntentTarget(time_range=(5.0, 2.0)))])
        norm, problems = ei.validate_intent(intent)
        assert norm.ops == [] and any("时间区间非法" in p.message for p in problems)

    def test_reorder_bad_key_falls_back_with_problem(self) -> None:
        intent = ei.EditIntent(ops=[ei.IntentOp(op="reorder", by="excitement")])
        norm, problems = ei.validate_intent(intent)
        assert norm.ops[0].by == "time"
        assert any("重排依据" in p.message for p in problems)

    def test_add_bad_kind_rejected(self) -> None:
        intent = ei.EditIntent(ops=[ei.IntentOp(op="add", kind="hologram")])
        norm, problems = ei.validate_intent(intent)
        assert norm.ops == [] and any("新增类型" in p.message for p in problems)

    def test_limit_normalized(self) -> None:
        intent = ei.EditIntent(ops=[ei.IntentOp(op="keep", target=ei.IntentTarget(all=True),
                                               limit=0)])
        assert ei.validate_intent(intent)[0].ops[0].limit == 1

    def test_constraints_platform_normalized(self) -> None:
        norm, _ = ei.validate_intent(ei.EditIntent(constraints=ei.IntentConstraints(platform="竖屏")))
        assert norm.constraints.platform == "vertical"

    def test_constraints_bad_platform(self) -> None:
        norm, problems = ei.validate_intent(ei.EditIntent(
            constraints=ei.IntentConstraints(platform="球形")))
        assert norm.constraints.platform == "" and ei.has_errors(problems)

    def test_constraints_duration_too_short_warns(self) -> None:
        norm, problems = ei.validate_intent(ei.EditIntent(
            constraints=ei.IntentConstraints(total_duration_sec=1.0)))
        assert norm.constraints.total_duration_sec == 3.0
        assert any(p.level == "warning" for p in problems)

    def test_constraints_negative_duration_is_error(self) -> None:
        norm, problems = ei.validate_intent(ei.EditIntent(
            constraints=ei.IntentConstraints(total_duration_sec=-5)))
        assert norm.constraints.total_duration_sec is None and ei.has_errors(problems)

    def test_too_many_ops(self) -> None:
        intent = ei.EditIntent(ops=[ei.IntentOp(op="reorder")] * 25)
        _norm, problems = ei.validate_intent(intent)
        assert any("超过上限" in p.message for p in problems)

    def test_single_clip_warning(self) -> None:
        intent = ei.EditIntent(ops=[ei.IntentOp(op="remove", target=ei.IntentTarget(all=True))])
        _norm, problems = ei.validate_intent(intent, clip_count=1)
        assert any("只有一个片段" in p.message for p in problems)

    def test_validation_does_not_mutate_input(self) -> None:
        intent = ei.EditIntent(ops=[ei.IntentOp(op="set", target=ei.IntentTarget(all=True),
                                               field="speed", value=99)])
        ei.validate_intent(intent)
        assert intent.ops[0].value == 99      # 原对象不被改

    def test_valid_intent_passes_clean(self) -> None:
        intent = ei.EditIntent(
            goal="去掉黑场并剪到 60 秒",
            ops=[ei.IntentOp(op="remove", target=ei.IntentTarget(filter={"is_black": True}))],
            constraints=ei.IntentConstraints(total_duration_sec=60),
        )
        norm, problems = ei.validate_intent(intent, clip_count=5)
        assert not ei.has_errors(problems) and norm.ops and norm.constraints.total_duration_sec == 60.0


# ── 6. LLM 路径 ───────────────────────────────────────────


class TestLlmPath:
    def _llm(self, payload: Any) -> AsyncMock:
        llm = AsyncMock()
        llm.structured_output = AsyncMock(return_value=payload)
        return llm

    def test_parses_payload(self) -> None:
        llm = self._llm({"goal": "精简", "ops": [{"op": "remove",
                                                 "target": {"filter": {"is_black": True}}}],
                         "constraints": {"total_duration_sec": 45}})
        intent = asyncio.run(ei.parse_intent_llm("精简到 45 秒", llm=llm))
        assert intent.ops[0].op == "remove" and intent.constraints.total_duration_sec == 45
        assert intent.source == "llm"

    def test_invalid_payload_returns_empty_intent(self) -> None:
        llm = self._llm({"ops": "not-a-list"})
        intent = asyncio.run(ei.parse_intent_llm("x", llm=llm))
        assert intent.ops == [] and intent.source == "llm-invalid"

    def test_llm_exception_returns_empty_intent(self) -> None:
        llm = AsyncMock()
        llm.structured_output = AsyncMock(side_effect=RuntimeError("api down"))
        intent = asyncio.run(ei.parse_intent_llm("x", llm=llm))
        assert intent.ops == [] and intent.source == "llm-failed"

    def test_no_llm(self) -> None:
        assert asyncio.run(ei.parse_intent_llm("x", llm=None)).ops == []

    def test_empty_message(self) -> None:
        llm = self._llm({"ops": []})
        asyncio.run(ei.parse_intent_llm("", llm=llm))
        llm.structured_output.assert_not_called()

    def test_schema_covers_ops_and_fields(self) -> None:
        schema = ei.intent_json_schema()
        props = schema["properties"]["ops"]["items"]["properties"]
        assert set(props["op"]["enum"]) == set(ei.OP_SPECS)
        assert "speed" in props["field"]["enum"]
        assert "reorder" in schema["properties"]["ops"]["items"]["properties"]["op"]["enum"]

    def test_examples_are_consistent_with_parser(self) -> None:
        """Few-shot 里写的说法，规则解析器也必须能解析（文档与实现不许漂移）。

        比较前**过一遍校验**：解析器产出原始值（"竖屏"），归一化（→ vertical）在校验阶段。
        """
        for example in ei.EXAMPLES:
            norm, _problems = ei.validate_intent(ei.parse_intent_rules(example["user"]))
            expected = example["intent"]
            if expected.get("ops"):
                assert norm.ops, f"规则解析器漏了示例：{example['user']}"
                assert norm.ops[0].op == expected["ops"][0]["op"], example["user"]
            if expected.get("constraints"):
                for key, value in expected["constraints"].items():
                    assert getattr(norm.constraints, key) == value, example["user"]


# ── 7. 统一入口 ───────────────────────────────────────────


class TestParseIntentEntry:
    def test_rules_first_by_default(self) -> None:
        llm = AsyncMock()
        llm.structured_output = AsyncMock(return_value={"ops": []})
        intent, problems = asyncio.run(ei.parse_intent("剪到 60 秒", llm=llm))
        assert intent.constraints.total_duration_sec == 60.0
        llm.structured_output.assert_not_called()   # 规则能解析就不花 token

    def test_fallback_to_llm_when_rules_empty(self) -> None:
        llm = AsyncMock()
        llm.structured_output = AsyncMock(return_value={
            "ops": [{"op": "reorder", "by": "duration"}]})
        intent, _problems = asyncio.run(ei.parse_intent("把最有冲击力的放前面", llm=llm))
        assert intent.ops and intent.ops[0].op == "reorder"
        assert intent.source == "llm"

    def test_prefer_llm_forces_llm(self) -> None:
        llm = AsyncMock()
        llm.structured_output = AsyncMock(return_value={"ops": [{"op": "reorder", "by": "time"}]})
        intent, _ = asyncio.run(ei.parse_intent("剪到 60 秒", llm=llm, prefer="llm"))
        assert intent.source == "llm"

    def test_entry_always_validates(self) -> None:
        """统一入口必须过校验：LLM 说出的幻觉 op 不能漏过去。"""
        llm = AsyncMock()
        llm.structured_output = AsyncMock(return_value={"ops": [{"op": "explode_video"}]})
        intent, problems = asyncio.run(ei.parse_intent("炸一下", llm=llm, prefer="llm"))
        assert intent.ops == [] and ei.has_errors(problems)

    def test_entry_without_llm(self) -> None:
        intent, problems = asyncio.run(ei.parse_intent("去掉黑场"))
        assert intent.ops and not ei.has_errors(problems)


# ── 8. 描述 ───────────────────────────────────────────────


class TestDescribe:
    def test_describes_ops_and_constraints(self) -> None:
        intent, _ = asyncio.run(ei.parse_intent("去掉黑场，剪到 60 秒，换成竖屏"))
        text = ei.describe_intent(intent)
        assert "删除匹配的片段" in text and "60" in text and "竖屏" in text

    def test_describes_set_and_reorder(self) -> None:
        intent = ei.EditIntent(ops=[
            ei.IntentOp(op="set", target=ei.IntentTarget(selected=True), field="speed", value=1.5),
            ei.IntentOp(op="reorder", by="motion", desc=True),
        ])
        text = ei.describe_intent(intent)
        assert "speed → 1.5" in text and "motion" in text and "降序" in text

    def test_mentions_unresolved(self) -> None:
        intent = ei.EditIntent(unresolved=["随便剪一下"])
        assert "需要确认" in ei.describe_intent(intent)

    def test_empty_intent(self) -> None:
        assert ei.describe_intent(ei.EditIntent()) == "（空意图）"

    def test_target_description_variants(self) -> None:
        assert ei.IntentTarget(all=True).describe() == "整条时间线"
        assert ei.IntentTarget(selected=True).describe() == "选中的片段"
        assert "3.0" in ei.IntentTarget(time_range=(3.0, 5.0)).describe()
        assert "指定片段" in ei.IntentTarget(ref=["cl_1"]).describe()
        assert "第 0、2 个片段" in ei.IntentTarget(index=[0, 2]).describe()
        assert "条件" in ei.IntentTarget(filter={"is_black": True}).describe()
        assert "内容匹配" in ei.IntentTarget(query="咖啡杯").describe()
        assert ei.IntentTarget().describe() == "（未指定）"
        assert ei.IntentTarget().is_empty() is True
