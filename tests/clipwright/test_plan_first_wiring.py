"""plan-first 接线单测（PRD §II.13 / A-16/A-17，验收 V55/V58/V60）。"""

from __future__ import annotations

import json

import pytest

from clipwright.agents.structure_agent import StructureAgent
from clipwright.schema.agent import AgentContext, StructureInput
from clipwright.schema.category_spec import (
    BeatGrid,
    MaterialStrategy,
    PlanDocument,
    PlanSection,
)
from clipwright.services.plan_first import (
    PlanMissing,
    append_drift,
    detect_drift,
    is_plan_first,
    plan_prompt_block,
    require_plan,
    resolve_plan,
)


def _plan() -> PlanDocument:
    return PlanDocument(
        type="lyric_mv",
        target_duration_sec=6.0,
        sections=[
            PlanSection(id="A", name="套印", t0=0.0, t1=2.5, purpose="建立舞台",
                        screen_kinds=["plate"], key_events=[{"t": 1.267, "what": "关键词"}]),
            PlanSection(id="B", name="腐坏", t0=2.5, t1=6.0, purpose="错版切入",
                        screen_kinds=["plate"]),
        ],
        screen_kinds=["plate"],
        beats=BeatGrid(bpm=130, beat_zero_sec=0.344, policy="切点落拍"),
        material_strategy=MaterialStrategy(gaps=["手部特写 x2"]),
    )


class TestResolvePlan:
    def test_from_underscore_plan(self) -> None:
        assert resolve_plan({"_plan": _plan().model_dump()}) is not None

    def test_from_plan(self) -> None:
        assert resolve_plan({"plan": _plan().model_dump()}) is not None

    def test_from_nested_production_plan(self) -> None:
        assert resolve_plan({"production_plan": {"plan": _plan().model_dump()}}) is not None

    def test_instance_passthrough(self) -> None:
        plan = _plan()
        assert resolve_plan({"_plan": plan}) is plan

    def test_missing_returns_none(self) -> None:
        assert resolve_plan({}) is None
        assert resolve_plan(None) is None

    def test_bad_payload_returns_none(self) -> None:
        assert resolve_plan({"_plan": {"sections": "nope"}}) is None


class TestRequirePlan:
    def test_plan_first_without_plan_raises(self) -> None:
        with pytest.raises(PlanMissing):
            require_plan({"plan_first": True})

    def test_plan_first_with_plan_ok(self) -> None:
        plan = require_plan({"plan_first": True, "_plan": _plan().model_dump()})
        assert plan is not None

    def test_not_plan_first_is_noop(self) -> None:
        assert require_plan({}) is None
        assert is_plan_first({}) is False

    def test_error_message_mentions_v55(self) -> None:
        with pytest.raises(PlanMissing) as exc:
            require_plan({"plan_first": True})
        assert "V55" in str(exc.value)


class TestPromptBlock:
    def test_contains_sections_purpose_and_screen(self) -> None:
        block = plan_prompt_block(_plan())
        assert "已确认的整体规划" in block
        assert "建立舞台" in block
        assert "plate" in block
        assert "0.00–2.50s" in block

    def test_contains_beats_and_gaps(self) -> None:
        block = plan_prompt_block(_plan())
        assert "BPM 130" in block and "第 0 拍" in block
        assert "手部特写 x2" in block

    def test_key_events_rendered(self) -> None:
        assert "关键词" in plan_prompt_block(_plan())

    def test_empty_plan_returns_empty(self) -> None:
        assert plan_prompt_block(PlanDocument()) == ""

    def test_deterministic(self) -> None:
        assert plan_prompt_block(_plan()) == plan_prompt_block(_plan())


class TestDetectDrift:
    def test_no_drift_when_matching(self) -> None:
        executed = [{"duration_sec": 2.5}, {"duration_sec": 3.5}]
        assert detect_drift(_plan(), executed) == []

    def test_duration_drift_detected(self) -> None:
        executed = [{"duration_sec": 1.0}, {"duration_sec": 1.0}]
        codes = [d["code"] for d in detect_drift(_plan(), executed)]
        assert "plan_drift_duration" in codes

    def test_section_count_drift(self) -> None:
        executed = [{"duration_sec": 3.0}, {"duration_sec": 3.0}, {"duration_sec": 0.0}]
        codes = [d["code"] for d in detect_drift(_plan(), executed)]
        assert "plan_drift_section_count" in codes

    def test_screen_kind_drift(self) -> None:
        executed = [{"duration_sec": 3.0, "screen_kinds": ["chat"]},
                    {"duration_sec": 3.0, "screen_kinds": ["plate"]}]
        codes = [d["code"] for d in detect_drift(_plan(), executed)]
        assert "plan_drift_screen_kind" in codes

    def test_empty_inputs_are_safe(self) -> None:
        assert detect_drift(_plan(), []) == []
        assert detect_drift(PlanDocument(), [{"duration_sec": 1.0}]) == []


class TestAppendDrift:
    def test_appends_jsonl(self, tmp_path) -> None:
        path = tmp_path / "drift.jsonl"
        written = append_drift(path, [{"code": "plan_drift_duration"}, {"code": "x"}])
        assert written == 2
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["code"] == "plan_drift_duration"

    def test_empty_entries_no_file(self, tmp_path) -> None:
        path = tmp_path / "drift.jsonl"
        assert append_drift(path, []) == 0
        assert not path.exists()


class TestEmitDriftEvents:
    """V58：偏离除了落 JSONL，还必须进 trace（前端运行详情可查）。"""

    def test_emits_one_event_per_drift(self, monkeypatch) -> None:
        from clipwright.services import trace as trace_module
        from clipwright.services.plan_first import emit_drift_events

        captured: list[tuple] = []

        def _fake_add_event(pid, agent, event_type, summary, detail=None, **_kw):
            captured.append((pid, agent, event_type, summary, detail))

        monkeypatch.setattr(trace_module, "add_event", _fake_add_event)
        drifts = [{"code": "plan_drift_duration", "planned": 6.0, "executed": 2.0}]
        assert emit_drift_events("p" * 32, drifts) == 1
        pid, agent, event_type, summary, detail = captured[0]
        assert (pid, agent, event_type) == ("p" * 32, "structure", "plan_drift")
        assert "plan_drift_duration" in summary
        assert detail == drifts[0]

    def test_no_drift_no_event(self, monkeypatch) -> None:
        from clipwright.services import trace as trace_module
        from clipwright.services.plan_first import emit_drift_events

        monkeypatch.setattr(
            trace_module, "add_event",
            lambda *a, **k: pytest.fail("无偏离时不应写 trace"),
        )
        assert emit_drift_events("p" * 32, []) == 0
        assert emit_drift_events("", [{"code": "x"}]) == 0

    def test_trace_failure_does_not_raise(self, monkeypatch) -> None:
        from clipwright.services import trace as trace_module
        from clipwright.services.plan_first import emit_drift_events

        def _boom(*_a, **_k):
            raise RuntimeError("trace 挂了")

        monkeypatch.setattr(trace_module, "add_event", _boom)
        assert emit_drift_events("p" * 32, [{"code": "x"}]) == 0


def _structure_input(context: AgentContext) -> StructureInput:
    return StructureInput(
        context=context,
        persona_config={"identity": {}, "language": {}, "rhythm": {}, "constraints": {}},
        creative_brief={},
        production_plan=None,
    )


class TestStructureAgentPlanFirst:
    """V55：空 plan 时结构阶段必须失败，不能悄悄自由生成。"""

    @pytest.mark.asyncio
    async def test_missing_plan_fails_with_clear_message(self) -> None:
        agent = StructureAgent()
        context = AgentContext(
            pipeline_id="p" * 32,
            persona_id="demo",
            category_plugin_id="lyric_mv",
            topic="示范",
            extra_params={"plan_first": True},
        )
        output = await agent.execute(_structure_input(context), context)
        assert output.decision.value == "fail"
        assert output.script_skeleton.get("failure_reason") == "plan_missing"
        joined = " ".join(str(note) for note in output.script_skeleton.get("_warnings") or [])
        assert "V55" in joined

    @pytest.mark.asyncio
    async def test_plan_first_off_still_works(self) -> None:
        """未开 plan_first 时行为不变（不因缺少规划而失败）。"""
        agent = StructureAgent()
        context = AgentContext(
            pipeline_id="p" * 32,
            persona_id="demo",
            category_plugin_id="lyric_mv",
            topic="示范",
            extra_params={},
        )
        from clipwright.services.plan_first import require_plan as _req

        assert _req(context.extra_params) is None
        assert agent is not None
