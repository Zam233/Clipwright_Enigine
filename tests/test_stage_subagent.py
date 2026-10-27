"""阶段子代理 + 黑板 + 装配/合并（PRD docs/master-agent-prd-2026-10-26.md §3 / §5）。

策略：不碰真实 Agent（会调 LLM/ffmpeg）——`agent_for` 全部用替身，
只验证契约：装配、墙钟、路由白名单、结果形状、黑板语义、失败分类。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from clipwright.config import settings
from clipwright.schema.stage import StageSpec
from clipwright.services import stage_inputs, stage_merge, stage_subagent
from clipwright.services import task_blackboard as bb


class _FakeRequest:
    def __init__(self, **kw):
        self.topic = kw.get("topic", "测试选题")
        self.persona_id = kw.get("persona_id", "default")
        self.category_plugin_id = kw.get("category_plugin_id", "knowledge_longform")
        self.extra = kw.get("extra", {})


class _Task:
    def __init__(self, task_id="task_p1", **kw):
        self.id = task_id
        self.request = _FakeRequest(**kw)


class _FakeAgent:
    def __init__(self, payload, delay: float = 0.0):
        self.payload = payload
        self.delay = delay

    async def execute(self, input_obj, context):
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.payload


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    bb.reset()
    stage_subagent.reset()
    monkeypatch.setattr(settings, "agent_mode", "on")


def _patch_agent(monkeypatch, payload, delay: float = 0.0):
    monkeypatch.setattr(stage_subagent, "agent_for",
                        lambda stage: _FakeAgent(payload, delay=delay))


# ── 黑板语义 ────────────────────────────────────────────────


def test_blackboard_tracks_writer_and_rejects_backwards_timeline() -> None:
    bb.put("t1", "script_skeleton", {"title": "x"}, by="structure")
    assert bb.owner_of("t1", "script_skeleton") == "structure"

    bb.put("t1", "timeline", {"tracks": []}, by="edit")
    bb.put("t1", "timeline", {"tracks": [], "duration": 10}, by="audio")
    assert bb.owner_of("t1", "timeline") == "audio"

    with pytest.raises(bb.BlackboardError) as exc:
        bb.put("t1", "timeline", {"tracks": [{"clips": []}]}, by="edit")
    assert "下游覆盖上游" in str(exc.value)


def test_blackboard_requires_pointer_for_huge_values() -> None:
    with pytest.raises(bb.BlackboardError) as exc:
        bb.put("t2", "timeline", {"blob": "x" * (bb.MAX_BLACKBOARD_BYTES + 10)}, by="edit")
    assert "文件指针" in str(exc.value)
    # 指针形状可以通过
    bb.put("t2", "timeline", {"path": "C:/ws/timeline.json", "sha256": "abc"}, by="edit")
    assert bb.get("t2")["timeline"]["sha256"] == "abc"


def test_blackboard_rejects_unknown_timeline_producer() -> None:
    """不在 TIMELINE_STAGE_ORDER 里的 by 不能写 timeline（否则单向校验被绕过）。"""
    with pytest.raises(bb.BlackboardError) as exc:
        bb.put("t6", "timeline", {"tracks": []}, by="不明阶段")
    assert "不是时间线生产者" in str(exc.value)
    # 空 by（匿名写）同样必须拒绝：get("",99) 恒 >= 任何真实 rank，等于绕过校验
    with pytest.raises(bb.BlackboardError) as exc2:
        bb.put("t6", "timeline", {"tracks": []}, by="")
    assert "匿名" in str(exc2.value)
    bb.put("t6", "timeline", {"tracks": []}, by="edit")
    with pytest.raises(bb.BlackboardError):
        bb.put("t6", "timeline", {"tracks": [1]}, by="不明阶段")


def test_blackboard_describe_reports_ownership() -> None:
    bb.put("t3", "candidate_clips", [{"id": 1}], by="material")
    info = bb.describe("t3")
    assert info["keys"] == ["candidate_clips"]
    assert info["by"]["candidate_clips"] == "material"


# ── 输入装配：缺键不猜 ──────────────────────────────────────


def test_assemble_material_without_skeleton_reports_upstream() -> None:
    with pytest.raises(stage_inputs.StageInputMissing) as exc:
        stage_inputs.assemble("material", task=_Task(), board={})
    assert exc.value.missing == ["script_skeleton"]
    assert exc.value.suggestion == ["structure"]


def test_assemble_edit_needs_two_keys() -> None:
    with pytest.raises(stage_inputs.StageInputMissing) as exc:
        stage_inputs.assemble("edit", task=_Task(), board={"script_skeleton": {}})
    assert "candidate_clips" in exc.value.missing
    assert "material" in exc.value.suggestion


def test_assemble_structure_builds_real_input() -> None:
    obj = stage_inputs.assemble("structure", task=_Task(topic="甲"), board={})
    assert type(obj).__name__ == "StructureInput"
    assert obj.context.topic == "甲"


# ── 合并语义（与老编排器的 _merge_agent_result 对齐，已迁入 stage_merge）──────


def test_merge_excludes_control_keys() -> None:
    written, errors = stage_merge.merge("structure", {
        "script_skeleton": {"title": "t"},
        "scenes": [{"n": 1}],
        "agent_name": "structure_agent",
        "decision": "pass",
        "error": None,
        "status": "ok",
    }, task_id="t4")
    assert set(written) == {"script_skeleton", "scenes"}
    assert errors == []
    board = bb.get("t4")
    assert "agent_name" not in board and "decision" not in board


def test_merge_audio_promotes_final_timeline() -> None:
    stage_merge.merge("edit", {"timeline": {"tracks": []}}, task_id="t5")
    written, errors = stage_merge.merge(
        "audio", {"timeline": {"tracks": [], "duration": 5}}, task_id="t5")
    assert "final_timeline" in written
    assert errors == []
    assert bb.get("t5")["final_timeline"]["duration"] == 5


def test_merge_spills_huge_timeline_to_pointer(tmp_path, monkeypatch) -> None:
    """超大时间线必须落盘转指针，而不是被静默丢弃（审计 P1-2）。"""
    monkeypatch.setattr(settings, "workspace_dir", tmp_path / "workspaces")
    huge = {"tracks": [], "blob": "x" * (bb.MAX_BLACKBOARD_BYTES + 1000)}
    written, errors = stage_merge.merge("edit", {"timeline": huge}, task_id="t_spill")
    assert "timeline" in written and errors == []
    stored = bb.get("t_spill")["timeline"]
    assert stored.get("path") and Path(stored["path"]).is_file()
    assert stored["bytes"] > bb.MAX_BLACKBOARD_BYTES

    # 读端能还原（stage_inputs._as_timeline 支持指针）
    import json as _json

    raw = _json.loads(Path(stored["path"]).read_text(encoding="utf-8"))
    assert raw["blob"].startswith("xxx")


# ── 阶段执行 ────────────────────────────────────────────────


def test_run_stage_structure_ok_and_writes_blackboard(monkeypatch) -> None:
    _patch_agent(monkeypatch, {
        "script_skeleton": {"title": "标题"},
        "scenes": [{"title": "开场"}, {"title": "结尾"}],
        "decision": "pass",
    })
    result = asyncio.run(stage_subagent.run_stage(_Task(), StageSpec(stage="structure")))
    assert result.status == "ok"
    assert set(result.writes) == {"script_skeleton", "scenes"}
    assert "分镜 2 个" in result.summary
    board = bb.get("task_p1")
    assert board["script_skeleton"]["title"] == "标题"


def test_run_stage_missing_input_returns_input_insufficient(monkeypatch) -> None:
    _patch_agent(monkeypatch, {"candidate_clips": []})
    result = asyncio.run(stage_subagent.run_stage(_Task(), StageSpec(stage="edit")))
    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.category == "input_insufficient"
    # needs 里要写清"该先派哪个阶段补什么"
    assert result.failure.needs
    assert result.failure.needs[0]["from_stage"] == "structure"


def test_run_stage_dry_run_does_not_execute_or_merge(monkeypatch) -> None:
    called: list[str] = []

    class _Spy:
        async def execute(self, input_obj, context):
            called.append("x")
            return {}

    monkeypatch.setattr(stage_subagent, "agent_for", lambda stage: _Spy())
    result = asyncio.run(stage_subagent.run_stage(
        _Task(), StageSpec(stage="structure", dry_run=True)))
    assert result.status == "dry_run"
    assert called == []
    assert bb.get("task_p1") == {}


def test_run_stage_timeout_is_categorized(monkeypatch) -> None:
    _patch_agent(monkeypatch, {"scenes": []}, delay=0.5)
    result = asyncio.run(stage_subagent.run_stage(
        _Task(), StageSpec(stage="structure", timeout_sec=0.05)))
    assert result.status == "failed"
    assert result.failure.category == "timeout"
    assert result.failure.retryable == "retry_same"


def test_run_stage_rejects_model_outside_whitelist(monkeypatch) -> None:
    _patch_agent(monkeypatch, {"scenes": []})
    monkeypatch.setattr(settings, "model_whitelist", [], raising=False)
    result = asyncio.run(stage_subagent.run_stage(
        _Task(), StageSpec(stage="structure", provider="不存在的源", model="x1")))
    assert result.status == "refused"
    assert "白名单" in result.error


def test_stage_result_exposes_no_fake_cost(monkeypatch) -> None:
    """没有真实 LLM 用量时 cost 只能是 None（未知），不许写 0。"""
    _patch_agent(monkeypatch, {"scenes": []})
    result = asyncio.run(stage_subagent.run_stage(_Task(), StageSpec(stage="structure")))
    assert result.cost_usd is None
    assert result.cost_unknown is True


# ── 能力层（Kernel 调用面）──────────────────────────────────


def test_capability_splits_control_args_from_payload(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def _fake_agent_for(stage):
        class _Spy:
            async def execute(self, input_obj, context):
                seen["input"] = input_obj
                return {"scenes": []}
        return _Spy()

    monkeypatch.setattr(stage_subagent, "agent_for", _fake_agent_for)

    from clipwright.schema.task import PlanStep

    step = PlanStep(id="p1", capability="delegate", args={
        "stage": "structure", "provider": "", "model": "",
        "timeout_sec": 30, "max_turns": 2, "dry_run": False,
        "persona_config": {"tone": "冷静"},
    })
    from clipwright.tool import surface as sf

    out = asyncio.run(sf.execute("delegate_agent", dict(step.args),
                                 pipeline_id="task_cap", task=_Task("task_cap")))
    assert out["status"] == "ok"
    assert out["output"]["stage"] == "structure"
    # 控制参数（provider/model/timeout/max_turns/dry_run）不进业务输入：payload 里没有它们
    assert "provider" not in (out["output"].get("artifacts") or {})
    assert "timeout_sec" not in (out["output"].get("artifacts") or {})


def test_capability_refuses_unknown_stage(monkeypatch) -> None:
    from clipwright.schema.task import PlanStep

    from clipwright.tool import surface as sf

    step = PlanStep(id="p1", capability="delegate", args={"stage": "不存在的阶段"})
    out = asyncio.run(sf.execute("delegate_agent", dict(step.args),
                                 pipeline_id="task_cap2", task=_Task("task_cap2")))
    assert out["status"] == "refused"
    assert out["failure"]["category"] == "input_insufficient"


def test_kernel_accumulates_stage_cost_into_task_usage() -> None:
    """阶段能力的 cost_usd 必须在任务 usage 里累计（成本条不能永远是 0）。

    M3：记账点从 `_run_step` 移到 tool loop 的 `_on_tool_call`——注入一个
    "调一次 delegate_agent 且结果带 cost"的驱动器即可验证同一条不变量。
    """
    from clipwright.schema.task import TaskRequest
    from clipwright.services.agent_kernel import AgentKernel, KernelDeps

    class _CostDriver:
        async def __call__(self, task, *, context_text="", shadow=False, dry_run=False,  # noqa: ANN001
                           guard=None, on_tool_call=None):  # noqa: ANN001
            if on_tool_call is not None:
                on_tool_call("delegate_agent",
                             {"status": "ok", "output": {}, "cost_usd": 0.25},
                             {"stage": "structure"})
            return {"status": "ok", "finish": {"conclusion": "ok"},
                    "tool_calls": [{"tool": "delegate_agent"}], "rounds": 2,
                    "stopped_reason": "model_final"}

    kernel = AgentKernel(KernelDeps(driver=_CostDriver()))
    task = kernel.create(TaskRequest(topic="x", plan_only=False))
    task = asyncio.run(kernel.run(task))
    assert task.usage.cost_usd == pytest.approx(0.25)


def test_list_stages_query_returns_blackboard(monkeypatch) -> None:
    from clipwright.schema.task import PlanStep

    _patch_agent(monkeypatch, {"scenes": [{"n": 1}]})
    asyncio.run(stage_subagent.run_stage(_Task(), StageSpec(stage="structure")))
    from clipwright.tool import surface as sf

    task = _Task("task_list")
    # 必须经 surface 派发，run 表里才有记录（直接调 run_stage 不会登记 AgentRun）
    asyncio.run(sf.execute("delegate_agent", {"stage": "structure"},
                           pipeline_id="task_list", task=task))
    out = asyncio.run(sf.execute("list_agent_runs", {}, pipeline_id="task_list",
                                 task=task))
    assert out["status"] == "ok"
    assert out["output"]["count"] >= 1
    assert "keys" in (out["output"]["blackboard"] or {})
