"""plan-first 单测（PRD §II.13 / V52–V60，A-12/A-13/A-15/A-01）。"""

from __future__ import annotations

import re

from clipwright.agents.requirements_agent import CREATIVE_BRIEF_SYSTEM, RequirementsAgent
from clipwright.schema.category_spec import (
    AcceptanceItem,
    BeatGrid,
    CustomSection,
    MaterialStrategy,
    PlanDocument,
    PlanSchema,
    PlanSection,
    RiskItem,
)
from clipwright.schema.requirements import CreativeBrief, brief_field_names, brief_prompt_template
from clipwright.services.plan_renderer import render_brief_markdown, render_plan_markdown
from clipwright.services.plan_validator import (
    assert_plan_confirmable,
    plan_from_scenes,
    validate_plan,
)


def _plan(**kwargs) -> PlanDocument:
    base = dict(
        type="lyric_mv",
        target_duration_sec=30.0,
        sections=[
            PlanSection(id="A", name="开机", t0=0.0, t1=10.0, purpose="建立舞台", screen_kinds=["chat"]),
            PlanSection(id="B", name="版画", t0=10.0, t1=30.0, purpose="内心", screen_kinds=["plate"]),
        ],
        screen_kinds=["chat", "plate"],
        material_strategy=MaterialStrategy(sources=["user_provided"], grounding="每句落位"),
    )
    base.update(kwargs)
    return PlanDocument(**base)


class TestFieldDrift:
    """V53：提示词字段集必须与模型一致（旧实现在这里必挂）。"""

    def test_template_is_bijective_with_model(self) -> None:
        model_fields = set(CreativeBrief.model_fields)
        template = brief_prompt_template()
        listed = set(re.findall(r"\|\s*`([a-z_]+)`\s*\|", template))
        assert listed == model_fields

    def test_prompt_contains_every_field(self) -> None:
        for name in brief_field_names():
            assert name in CREATIVE_BRIEF_SYSTEM, f"提示词缺少字段 {name}"

    def test_legacy_missing_fields_are_present(self) -> None:
        # 旧实现里这 4 个字段被静默丢弃，key_points/references 永远为空
        for name in (
            "core_message",
            "structure_suggestion",
            "key_elements",
            "special_requirements",
        ):
            assert name in CreativeBrief.model_fields

    def test_normalize_drops_unknown_and_fills_defaults(self) -> None:
        out = RequirementsAgent._normalize_brief({"title": "x", "bogus": 1})
        assert "bogus" not in out
        assert out["title"] == "x"
        assert out["key_elements"] == []
        assert out["core_message"] == ""


class TestPlanValidator:
    def test_clean_plan_passes(self) -> None:
        result = validate_plan(_plan())
        assert result.ok, [i.code for i in result.issues]

    def test_missing_required_dimension(self) -> None:
        schema = PlanSchema(required=["sections", "beats"])
        result = validate_plan(_plan(), schema)
        assert "plan_missing_required" in [i.code for i in result.issues]
        assert not result.ok

    def test_unknown_dimension_rejected_when_custom_disallowed(self) -> None:
        schema = PlanSchema(custom_allowed=False)
        plan = _plan(custom_sections=[CustomSection(name="母题", reason="复用副歌版画")])
        result = validate_plan(plan, schema)
        assert "plan_unknown_dimension" in [i.code for i in result.issues]

    def test_unknown_dimension_allowed_when_custom_allowed(self) -> None:
        schema = PlanSchema(custom_allowed=True)
        plan = _plan(custom_sections=[CustomSection(name="母题", reason="复用副歌版画")])
        assert validate_plan(plan, schema).ok

    def test_gap_between_sections(self) -> None:
        plan = _plan(
            sections=[
                PlanSection(id="A", t0=0.0, t1=10.0),
                PlanSection(id="B", t0=12.0, t1=30.0),
            ]
        )
        result = validate_plan(plan)
        assert "plan_section_gap" in [i.code for i in result.issues]

    def test_overlap_between_sections(self) -> None:
        plan = _plan(
            sections=[
                PlanSection(id="A", t0=0.0, t1=12.0),
                PlanSection(id="B", t0=10.0, t1=30.0),
            ]
        )
        assert "plan_section_overlap" in [i.code for i in validate_plan(plan).issues]

    def test_section_not_starting_at_zero(self) -> None:
        plan = _plan(sections=[PlanSection(id="A", t0=2.0, t1=30.0)])
        assert "plan_section_gap" in [i.code for i in validate_plan(plan).issues]

    def test_duration_drift_warning_then_error(self) -> None:
        plan = _plan(target_duration_sec=36.0)  # 30/36 偏差 16.7%
        warn = validate_plan(plan)
        assert "plan_duration_drift" in [i.code for i in warn.issues]
        assert warn.ok  # 只是告警

        plan = _plan(target_duration_sec=45.0)  # 偏差 33%
        error = validate_plan(plan)
        assert not error.ok
        assert "plan_duration_drift" in [i.code for i in error.issues]

    def test_beats_mismatch(self) -> None:
        plan = _plan(beats=BeatGrid(bpm=130, beat_zero_sec=0.344))
        grid = {"bpm": 128.0, "beat_zero_sec": 0.344}
        assert "plan_beats_mismatch" in [
            i.code for i in validate_plan(plan, audio_grid=grid).issues
        ]

    def test_beats_match(self) -> None:
        plan = _plan(beats=BeatGrid(bpm=130, beat_zero_sec=0.344))
        grid = {"bpm": 130.0, "beat_zero_sec": 0.344}
        assert validate_plan(plan, audio_grid=grid).ok

    def test_custom_section_requires_reason(self) -> None:
        plan = _plan(custom_sections=[CustomSection(name="母题", reason="")])
        assert "plan_missing_reason" in [i.code for i in validate_plan(plan).issues]

    def test_assert_confirmable_raises(self) -> None:
        plan = _plan(target_duration_sec=45.0)
        try:
            assert_plan_confirmable(plan)
        except ValueError as exc:
            assert "plan_duration_drift" in str(exc)
        else:  # pragma: no cover - 断言失败路径
            raise AssertionError("应当拒绝确认")


class TestLegacyCompat:
    """V60：只有 scenes 的旧会话要能反向生成 legacy plan。"""

    def test_plan_from_scenes(self) -> None:
        scenes = [
            {"id": "s1", "title": "开场", "duration_sec": 5.0, "description": "介绍"},
            {"id": "s2", "title": "正片", "duration_sec": 15.0},
        ]
        plan = plan_from_scenes(scenes, type_id="vlog_daily")
        assert plan.legacy is True
        assert plan.target_duration_sec == 20.0
        assert [s.t0 for s in plan.sections] == [0.0, 5.0]
        assert plan.sections[1].t1 == 20.0
        assert validate_plan(plan).ok


class TestPlanRenderer:
    """V52：同一 plan 两次渲染逐字节一致。"""

    def test_render_is_deterministic(self) -> None:
        plan = _plan(
            beats=BeatGrid(bpm=130, beat_zero_sec=0.344, policy="切点落拍"),
            risks=[RiskItem(risk="4K 锯齿", mitigation="矢量化")],
            acceptance=[AcceptanceItem(metric="flash_violations", target=0)],
            custom_sections=[CustomSection(name="母题", reason="复用副歌版画", content="四张")],
        )
        first = render_plan_markdown(plan, {"title": "测试"})
        second = render_plan_markdown(plan, {"title": "测试"})
        assert first == second

    def test_render_contains_key_sections(self) -> None:
        plan = _plan(
            beats=BeatGrid(bpm=130, beat_zero_sec=0.344, policy="切点落拍"),
            material_strategy=MaterialStrategy(
                sources=["user_provided"], grounding="每句落位", gaps=["手部特写 x2"]
            ),
        )
        text = render_plan_markdown(plan)
        assert "## 段落规划" in text
        assert "## 卡点策略" in text
        assert "手部特写 x2" in text
        assert "10.00–30.00s" in text

    def test_render_escapes_pipes(self) -> None:
        plan = _plan(sections=[PlanSection(id="A", name="a|b", t0=0.0, t1=30.0)])
        assert "a／b" in render_plan_markdown(plan)

    def test_render_brief_lists_required_fields(self) -> None:
        text = render_brief_markdown(
            {
                "title": "标题",
                "core_message": "记住这句话",
                "key_elements": ["茄子", "番茄"],
                "special_requirements": ["不出现红色"],
            }
        )
        assert "**核心信息**：记住这句话" in text
        assert "茄子" in text
        assert "不出现红色" in text

    def test_legacy_plan_is_marked(self) -> None:
        plan = plan_from_scenes([{"title": "a", "duration_sec": 3.0}])
        assert "legacy" in render_plan_markdown(plan)
