"""B10：自动派生只读核查员（用户决策：允许自动派生）。

派生的边界必须可检验，否则「自动派人」会变成「自动烧钱 + 自动改东西」：

1. 只有 `supervisor_mode="on"` 才派生（`shadow`/`off` 一律不派生，off 等价性不破）；
2. 只授予只读工具（`read_file` / `list_dir` / `list_subagents`），因此不需要执行权限审核门；
3. 只在归因置信度为 `low` 时派生（高置信说明规则已经说清了，不需要再花钱看现场）；
4. 同一阶段只派一次；派出的任务是后台协程，不阻塞自愈决策。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from clipwright.services import pipeline_v2 as pv


def _orch() -> pv.PipelineOrchestratorV2:
    return object.__new__(pv.PipelineOrchestratorV2)


def _state() -> SimpleNamespace:
    return SimpleNamespace(pipeline_id="pl_diag", shared_data={})


def _attr(confidence: str = "low", stage: str = "material") -> SimpleNamespace:
    return SimpleNamespace(confidence=confidence, primary_stage=stage)


@pytest.fixture(autouse=True)
def _clean():
    from clipwright.config import settings

    settings.supervisor_mode = "off"
    settings.supervisor_auto_spawn = True
    yield
    settings.supervisor_mode = "off"
    settings.supervisor_auto_spawn = True


def test_off_mode_never_spawns(monkeypatch):
    calls: list[str] = []

    async def fake(self, state, pid, stage, issues):  # noqa: ANN001
        calls.append(stage)

    monkeypatch.setattr(pv.PipelineOrchestratorV2, "_spawn_diagnostician", fake)
    _orch()._maybe_spawn_diagnostician(_state(), "pl_diag", _attr(), [], mode="off")
    asyncio.run(asyncio.sleep(0))
    assert calls == []


def test_shadow_mode_never_spawns(monkeypatch):
    """影子模式只记录，不派人——否则 shadow 也会产生费用。"""
    calls: list[str] = []

    async def fake(self, state, pid, stage, issues):  # noqa: ANN001
        calls.append(stage)

    monkeypatch.setattr(pv.PipelineOrchestratorV2, "_spawn_diagnostician", fake)
    _orch()._maybe_spawn_diagnostician(_state(), "pl_diag", _attr(), [], mode="shadow")
    asyncio.run(asyncio.sleep(0))
    assert calls == []


def test_on_mode_spawns_when_confidence_is_low(monkeypatch):
    calls: list[str] = []

    async def fake(self, state, pid, stage, issues):  # noqa: ANN001
        calls.append(stage)

    monkeypatch.setattr(pv.PipelineOrchestratorV2, "_spawn_diagnostician", fake)
    _orch()._maybe_spawn_diagnostician(_state(), "pl_diag", _attr(), [], mode="on")
    asyncio.run(asyncio.sleep(0))
    assert calls == ["material"]


def test_high_confidence_does_not_spawn(monkeypatch):
    calls: list[str] = []

    async def fake(self, state, pid, stage, issues):  # noqa: ANN001
        calls.append(stage)

    monkeypatch.setattr(pv.PipelineOrchestratorV2, "_spawn_diagnostician", fake)
    for conf in ("high", "mid"):
        _orch()._maybe_spawn_diagnostician(_state(), "pl_diag", _attr(conf), [], mode="on")
    asyncio.run(asyncio.sleep(0))
    assert calls == []


def test_same_stage_spawns_only_once(monkeypatch):
    calls: list[str] = []

    async def fake(self, state, pid, stage, issues):  # noqa: ANN001
        calls.append(stage)

    monkeypatch.setattr(pv.PipelineOrchestratorV2, "_spawn_diagnostician", fake)
    state = _state()
    _orch()._maybe_spawn_diagnostician(state, "pl_diag", _attr(), [], mode="on")
    _orch()._maybe_spawn_diagnostician(state, "pl_diag", _attr(), [], mode="on")
    asyncio.run(asyncio.sleep(0))
    assert calls == ["material"]
    assert state.shared_data["_diag_spawned"] == ["material"]


def test_setting_switch_disables_auto_spawn(monkeypatch):
    from clipwright.config import settings

    calls: list[str] = []

    async def fake(self, state, pid, stage, issues):  # noqa: ANN001
        calls.append(stage)

    monkeypatch.setattr(pv.PipelineOrchestratorV2, "_spawn_diagnostician", fake)
    settings.supervisor_auto_spawn = False
    _orch()._maybe_spawn_diagnostician(_state(), "pl_diag", _attr(), [], mode="on")
    asyncio.run(asyncio.sleep(0))
    assert calls == []


def test_diagnostician_uses_readonly_tools_only(monkeypatch):
    """核查员不得拿到执行权限——否则必须过「独立判定 + 人工审核」两级门。"""
    from clipwright.supervisor import spawn as sp

    captured: dict = {}

    async def fake_spawn(ctx, **kwargs):  # noqa: ANN001
        captured.update(kwargs)
        captured["pid"] = ctx.pipeline_id
        return {"subagent_id": "sa_1", "status": "done", "output": "第 3 段是占位图",
                "cost_usd": 0.0, "duration_ms": 10}

    monkeypatch.setattr(sp, "spawn_subagent", fake_spawn)
    orch = _orch()
    asyncio.run(orch._spawn_diagnostician(_state(), "pl_diag", "material",
                                          [{"title": "素材缺失"}]))
    assert captured["pid"] == "pl_diag"
    assert set(captured["tools"]) <= {"read_file", "list_dir", "list_subagents"}
    assert not (set(captured["tools"]) & {"run_shell", "run_python"})
    assert captured["role"] == "失败现场核查员"
    assert "素材缺失" in captured["system_prompt"]


def test_diagnostician_failure_is_swallowed(monkeypatch):
    """核查员失败不能把管线带崩。"""
    from clipwright.supervisor import spawn as sp

    async def boom(ctx, **kwargs):  # noqa: ANN001
        raise RuntimeError("LLM 挂了")

    monkeypatch.setattr(sp, "spawn_subagent", boom)
    asyncio.run(_orch()._spawn_diagnostician(_state(), "pl_diag", "edit", []))
