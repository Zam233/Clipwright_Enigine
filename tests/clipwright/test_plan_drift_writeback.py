"""V58 规划偏离写回单测：并入幂等、上限、无 session 不写、写回失败不抛。"""

from __future__ import annotations

import pytest

from clipwright.agents.structure_agent import write_back_plan_drift_if_session
from clipwright.services import requirements_service
from clipwright.services.requirements_service import apply_plan_drift


class TestApplyPlanDrift:
    def test_appends_without_touching_plan_body(self) -> None:
        plan = {"plan": {"type": "vlog_daily"}, "markdown_content": "# 规划"}
        merged = apply_plan_drift(plan, [{"code": "plan_drift_duration", "planned": 20.0}])
        assert merged["plan"] == plan["plan"]
        assert merged["markdown_content"] == "# 规划"
        assert merged["_drift"] == [{"code": "plan_drift_duration", "planned": 20.0}]
        # 原对象不被就地改写
        assert "_drift" not in plan

    def test_same_code_and_index_keeps_last(self) -> None:
        first = apply_plan_drift(None, [{"code": "plan_drift_duration", "planned": 20.0}])
        second = apply_plan_drift(first, [{"code": "plan_drift_duration", "planned": 30.0}])
        assert len(second["_drift"]) == 1
        assert second["_drift"][0]["planned"] == 30.0

    def test_different_index_kept_separately(self) -> None:
        merged = apply_plan_drift(
            None,
            [
                {"code": "plan_drift_screen_kind", "index": 0},
                {"code": "plan_drift_screen_kind", "index": 1},
            ],
        )
        assert len(merged["_drift"]) == 2

    def test_caps_history(self) -> None:
        plan = None
        for index in range(30):
            plan = apply_plan_drift(plan, [{"code": "plan_drift_screen_kind", "index": index}])
        assert len(plan["_drift"]) == 20
        assert plan["_drift"][-1]["index"] == 29

    def test_empty_drifts_is_noop(self) -> None:
        assert apply_plan_drift({"plan": {}}, []) == {"plan": {}}


class TestWriteBackPlanDrift:
    def test_writes_into_memory_session(self, monkeypatch) -> None:
        monkeypatch.setattr(requirements_service, "_mongo_ok", lambda: False)
        session_id = "sess-abc"
        requirements_service._memory_sessions[session_id] = {
            "production_plan": {"plan": {"type": "vlog_daily"}},
        }
        try:
            written = requirements_service.write_back_plan_drift(
                session_id, [{"code": "plan_drift_section_count", "planned": 2, "executed": 4}]
            )
            assert written == 1
            drift = requirements_service._memory_sessions[session_id]["production_plan"]["_drift"]
            assert drift[0]["code"] == "plan_drift_section_count"
        finally:
            requirements_service._memory_sessions.pop(session_id, None)

    def test_missing_session_returns_zero(self, monkeypatch) -> None:
        monkeypatch.setattr(requirements_service, "_mongo_ok", lambda: False)
        assert requirements_service.write_back_plan_drift("nope", [{"code": "x"}]) == 0

    def test_no_drifts_returns_zero(self, monkeypatch) -> None:
        monkeypatch.setattr(requirements_service, "_mongo_ok", lambda: False)
        assert requirements_service.write_back_plan_drift("s", []) == 0


class TestStructureAgentHook:
    """有 session_id 才写回；写回抛错不能拖垮结构阶段。"""

    @pytest.mark.asyncio
    async def test_calls_write_back_with_session(self, monkeypatch) -> None:
        seen: list[tuple] = []
        monkeypatch.setattr(
            requirements_service,
            "write_back_plan_drift",
            lambda sid, drifts: seen.append((sid, drifts)) or len(drifts),
        )

        class _Ctx:
            extra_params = {"session_id": "sess-1"}

        drift = {"code": "plan_drift_duration"}
        assert await write_back_plan_drift_if_session(_Ctx(), [drift]) == 1
        assert seen == [("sess-1", [drift])]

    @pytest.mark.asyncio
    async def test_skips_without_session(self, monkeypatch) -> None:
        monkeypatch.setattr(
            requirements_service,
            "write_back_plan_drift",
            lambda *_a, **_k: pytest.fail("没有 session_id 不该写回"),
        )

        class _Ctx:
            extra_params = {}

        assert await write_back_plan_drift_if_session(_Ctx(), [{"code": "x"}]) == 0

    @pytest.mark.asyncio
    async def test_write_back_failure_is_swallowed(self, monkeypatch) -> None:
        monkeypatch.setattr(
            requirements_service,
            "write_back_plan_drift",
            lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("mongo 挂了")),
        )

        class _Ctx:
            extra_params = {"session_id": "sess-2"}

        assert await write_back_plan_drift_if_session(_Ctx(), [{"code": "x"}]) == 0
