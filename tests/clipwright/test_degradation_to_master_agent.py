# -*- coding: utf-8 -*-
"""降级信号必须抵达主 Agent 并触发决策（E2E 2026-10-29 用户裁定）。

三层断链的回归：
1. AnimationAgent/MGGenerator 里的截断/回退只写日志 → 不登记 degradation；
2. `agent_run._run_stage` 把 StageResult.degradations 整段丢弃；
3. `AgentResult.to_tool_payload()` 不含降级 → 模型看不到，收尾时也不被追问。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from clipwright.schema.run import AgentResult
from clipwright.schema.stage import StageResult
from clipwright.services import agent_kernel, agent_run
from clipwright.services import degradation as degradation_mod


class _Usage(SimpleNamespace):
    """TaskUsage 桩：kernel 收尾会调 model_dump()。"""

    def model_dump(self) -> dict:
        return dict(self.__dict__)


class _Task(SimpleNamespace):
    """最小 Task 桩（kernel / stage 都能吃）。"""

    def __init__(self, **kw) -> None:  # noqa: ANN003
        super().__init__(
            id="task_deg_1", status="running", usage=_Usage(
                tool_calls=0, cost_usd=0.0, unknown_cost_calls=0, turns=0, wall_sec=0.0),
            context_digest=[], plan=None, request=SimpleNamespace(extra={}),
            finish=None, **kw,
        )

    def touch(self) -> None:
        return None


# ── 1. AgentResult 形状：降级必须能被模型看见 ──────────────────────

def test_agent_result_payload_carries_degradations() -> None:
    result = AgentResult(
        run_id="r1", kind="stage", status="degraded", stage="animation",
        summary="轨道 6 条 / 片段 502 个",
        degradations=[{"kind": "llm_json_truncated", "detail": "finish_reason=length",
                       "scope": "scene:3", "impact": "该段退化为模板"}],
    )
    payload = result.to_tool_payload()
    assert payload["degraded"] is True
    assert payload["degradation_count"] == 1
    assert payload["degradations"][0]["kind"] == "llm_json_truncated"
    assert "该段退化为模板" in payload["degradations"][0]["impact"]


def test_agent_result_payload_clean_when_no_degradation() -> None:
    payload = AgentResult(run_id="r2", kind="stage", status="ok").to_tool_payload()
    assert payload["degraded"] is False
    assert payload["degradation_count"] == 0
    assert payload["degradations"] == []


# ── 2. StageResult → AgentResult 的桥不能再吞掉降级 ────────────────

def test_run_stage_passes_degradations_to_agent_result(monkeypatch) -> None:
    from clipwright.schema.stage import StageSpec

    async def _fake_run_stage(task, spec):  # noqa: ANN001, ANN002, ANN003
        return StageResult(
            stage="animation", status="degraded", summary="动画已编排",
            writes=["timeline", "final_timeline"],
            degradations=[{"kind": "text_motion_weak", "detail": "最大位移不足"}],
        )

    monkeypatch.setattr("clipwright.services.stage_subagent.run_stage", _fake_run_stage)
    spec = SimpleNamespace(
        stage="animation", brief="", inputs={}, provider="", model="",
        timeout_sec=60, max_turns=2, idem_key="", role="animation",
    )
    out = asyncio.run(agent_run._run_stage(spec, _Task(), "run-1", "trace-1"))  # noqa: SLF001
    assert out.degradations and out.degradations[0]["kind"] == "text_motion_weak"
    assert out.to_tool_payload()["degraded"] is True


# ── 3. MGGenerator：解析失败必须登记降级，且能区分截断/格式坏 ────────

def test_parse_llm_response_notes_truncation(monkeypatch) -> None:
    from clipwright.animation.mg import generator as gen

    g = gen.MGGenerator.__new__(gen.MGGenerator)  # 不跑 __init__（无需配置/LLM）
    captured: list = []
    monkeypatch.setattr(gen, "_note", lambda kind, detail="", **kw: captured.append((kind, detail)))
    bad = '{"elements": [{"type": "text", "keyframes": [{"time": 0.1'
    assert g._parse_llm_response(bad, finish_reason="length") is None  # noqa: SLF001
    assert captured and captured[0][0] == "llm_json_truncated"
    assert "finish_reason=length" in captured[0][1]


def test_parse_llm_response_notes_invalid_json(monkeypatch) -> None:
    from clipwright.animation.mg import generator as gen

    g = gen.MGGenerator.__new__(gen.MGGenerator)
    captured: list = []
    monkeypatch.setattr(gen, "_note", lambda kind, detail="", **kw: captured.append((kind, detail)))
    # 引号未转义：完整返回但结构坏，不该被误报成"截断"
    assert g._parse_llm_response('{"elements": [{"text": "他说"好"}]}}', finish_reason="stop") is None  # noqa: SLF001
    assert captured and captured[0][0] == "llm_json_invalid"


def test_fallback_generate_notes_template_fallback(monkeypatch) -> None:
    from clipwright.animation.mg import generator as gen

    g = gen.MGGenerator.__new__(gen.MGGenerator)
    captured: list = []
    monkeypatch.setattr(gen, "_note", lambda kind, detail="", **kw: captured.append((kind, detail)))
    monkeypatch.setattr(g, "_get_templates", lambda: [], raising=False)
    asyncio.run(g._fallback_generate("描述", "文本", {}))  # noqa: SLF001
    assert any(k == "mg_template_fallback" for k, _ in captured)


# ── 4. Kernel：降级被收集、并被守门强推一轮决策 ─────────────────────

def test_kernel_collects_degradations_and_emits_event(monkeypatch) -> None:
    agent_kernel._PENDING_DEGRADATIONS.pop("task_deg_1", None)
    task = _Task()
    emitted: list = []
    monkeypatch.setattr("clipwright.services.task_events.emit",
                        lambda *a, **kw: emitted.append(a), raising=False)
    agent_kernel._collect_degradations(task, "delegate_agent", {  # noqa: SLF001
        "status": "ok",
        "output": {"degradations": [{"kind": "llm_json_truncated", "detail": "x"}]},
    })
    assert agent_kernel._PENDING_DEGRADATIONS["task_deg_1"]  # noqa: SLF001
    assert emitted and emitted[0][1] == "degradation"
    # 同一降级重复上报不重复计数
    agent_kernel._collect_degradations(task, "delegate_agent", {  # noqa: SLF001
        "output": {"degradations": [{"kind": "llm_json_truncated", "detail": "x"}]},
    })
    assert len(agent_kernel._PENDING_DEGRADATIONS["task_deg_1"]) == 1  # noqa: SLF001
    agent_kernel._PENDING_DEGRADATIONS.pop("task_deg_1", None)


def test_pending_degradation_hint_lists_items_and_demands_decision() -> None:
    agent_kernel._PENDING_DEGRADATIONS["task_deg_1"] = [  # noqa: SLF001
        {"kind": "mg_template_fallback", "detail": "模板顶上", "scope": "scene:7"},
    ]
    hint = agent_kernel._pending_degradation_hint("task_deg_1")  # noqa: SLF001
    assert "mg_template_fallback" in hint and "scene:7" in hint
    assert "必须" in hint and "重派" in hint and "禁止" in hint
    agent_kernel._PENDING_DEGRADATIONS.pop("task_deg_1", None)  # noqa: SLF001


def test_kernel_gate_forces_one_decision_round(monkeypatch) -> None:
    """有未处置降级时，收尾会被推回一轮（恰好一次），第二遍才允许收尾。"""
    monkeypatch.setattr(agent_kernel, "_MAX_DEGRADATION_CONTINUATIONS", 1)
    monkeypatch.setattr(agent_kernel, "_MAX_TIMELINE_CONTINUATIONS", 0)
    agent_kernel._DEGRADATION_CONTINUATIONS.pop("task_deg_1", None)  # noqa: SLF001
    # 用"需要收据"的降级（信息型降级不参与守门，见 RECEIPT_EXEMPT_KINDS）
    agent_kernel._PENDING_DEGRADATIONS["task_deg_1"] = [  # noqa: SLF001
        {"kind": "text_motion_weak", "detail": "最大位移 24px", "scope": "scene:3"},
    ]
    monkeypatch.setattr("clipwright.services.task_blackboard.get",
                        lambda _tid: {"final_timeline": {"tracks": []}}, raising=False)
    monkeypatch.setattr(agent_kernel, "_MEMORY_LOADED", {"task_deg_1"}, raising=False)

    calls: list[str] = []

    async def _driver(task, *, context_text="", shadow=False, guard=None,  # noqa: ANN001, ANN003
                      on_tool_call=None, dry_run=False):
        calls.append(context_text)
        return {"status": "ok", "finish": {"conclusion": "做完了"},
                "rounds": 1, "tool_calls": []}

    kernel = agent_kernel.AgentKernel(agent_kernel.KernelDeps(driver=_driver))
    task = _Task()
    asyncio.run(kernel._run_tool_loop(task, started=0.0))  # noqa: SLF001

    # 第一遍收尾被守门推回 → 第二次进 loop（降级决策），
    # 仍未写收据 → 第三次进 loop（最后通牒：只许写收据）
    assert len(calls) == 3
    assert "降级必须处置" in calls[1] and "text_motion_weak" in calls[1]
    assert "重派" in calls[1] and "scene:3" in calls[1]
    # 收尾事实里留着未处置降级（用户能在任务详情里看到）
    assert any("degraded:" in item for item in (task.finish.unfinished or []))

    agent_kernel._PENDING_DEGRADATIONS.clear()  # noqa: SLF001
    agent_kernel._DEGRADATION_CONTINUATIONS.clear()  # noqa: SLF001


# ── 5. degradation_scope 端到端：generator 的 _note 真能进 sink ────

def test_generator_note_lands_in_scope_sink(monkeypatch) -> None:
    from clipwright.animation.mg import generator as gen

    sink: list = []
    with degradation_mod.degradation_scope(sink):
        gen._note("llm_json_truncated", "finish_reason=length chars=12000",
                  impact="退化为模板")
    assert len(sink) == 1
    assert sink[0].kind == "llm_json_truncated"
    assert "finish_reason=length" in sink[0].detail
