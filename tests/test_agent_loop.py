"""M1：Kernel 原生 tool-use loop（modern-agent PRD §2.2 / §5.2）。

测的是**可证伪**的东西，不是"日志看起来像 agent"：

- 每轮请求都带 `tools`，且工具面里包含派发类工具；
- 模型发 tool_call → 真的执行 → 结果回填 → 模型继续；
- 闸（取消/预算/墙钟）在**工具执行前**生效，中止后 Kernel 可恢复；
- shadow 模式记录调用序列但不产生副作用；
- 条件不具备时**明确降级**回 plan adapter，不假装在跑 loop。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from clipwright.services import agent_loop
from clipwright.tool import surface as sf


class _FakeResp:
    def __init__(self, content: str = "", tool_calls: Any = None) -> None:
        self.content = content
        self.tool_calls = tool_calls or []
        self.success = True
        self.status_code = 200


class _FakeLLM:
    """假 provider：记录每轮收到的 tools，按脚本回放 tool_calls。"""

    def __init__(self, script: list[Any]) -> None:
        self.script = script
        self.rounds: list[list[dict[str, Any]]] = []
        self.executed: list[tuple[str, dict[str, Any]]] = []

    async def with_tools(self, *, system_prompt, user_prompt, tool_executor, tools,
                         max_tool_rounds=10, **kwargs):  # noqa: ANN001, ARG002
        self.rounds.append(list(tools))
        pending = list(self.script)
        await pending_calls(self, tool_executor, pending)
        return _FakeResp(content="本轮结论：已完成可做的部分")


async def pending_calls(fake: _FakeLLM, executor: Any, pending: list[Any]) -> None:
    for item in pending:
        fake.executed.append(item)
        await executor(item[0], item[1] or {})


def _task() -> Any:
    from types import SimpleNamespace

    from clipwright.schema.task import TaskRequest

    return SimpleNamespace(
        id="task_loop", request=TaskRequest(topic="做一期 3 分钟的数码评测"), plan=None,
        limits=SimpleNamespace(max_turns=10, max_tool_calls=20, max_wall_sec=600,
                               budget_usd=None),
        usage=SimpleNamespace(turns=0, tool_calls=0, wall_sec=0.0, cost_usd=0.0),
    )


def _patch_llm(monkeypatch, fake: _FakeLLM) -> None:
    import clipwright.services.llm as llm_mod

    monkeypatch.setattr(llm_mod, "LLMService", lambda *a, **k: fake)


# ── 工具面 ─────────────────────────────────────────────


def test_agent_tools_are_first_class_surface_tools():
    names = {e.name for e in sf.build_surface()}
    for name in ("delegate_agent", "research_agent", "get_agent_run",
                 "list_agent_runs", "cancel_agent_run"):
        assert name in names, f"{name} 必须是工具面里的一等工具"
    schemas = {s["function"]["name"]: s for s in sf.schemas()}
    assert "delegate_agent" in schemas
    # 档位提示必须在描述里（模型要知道这一步会不会花钱）
    assert "：" in schemas["delegate_agent"]["function"]["description"]


def test_build_tools_contains_schemas_every_round(monkeypatch):
    fake = _FakeLLM([("local_probe", {"n": 1})])
    _patch_llm(monkeypatch, fake)
    asyncio.run(agent_loop.run_loop(_task(), context_text="ctx"))
    assert fake.rounds, "必须真的发起过一次模型请求"
    assert all(len(tools) >= 40 for tools in fake.rounds), "每轮 tools 必须非空"


# ── 执行与回填 ─────────────────────────────────────────


def test_tool_call_is_executed_and_narrated(monkeypatch):
    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_execute(name, args=None, *, pipeline_id="", idem_key="", dry_run=False,
                           task=None):  # noqa: ANN001, ARG001
        calls.append((name, dict(args or {})))
        return {"status": "ok", "output": {"summary": "探到了 3 个素材"}}

    monkeypatch.setattr(sf, "execute", fake_execute)
    fake = _FakeLLM([("local_probe", {"n": 2})])
    _patch_llm(monkeypatch, fake)

    events: list[dict[str, Any]] = []
    monkeypatch.setattr(agent_loop, "emit",
                        lambda tid, t, m, d=None, **kw: events.append(
                            {"type": t, "detail": d}))

    out = asyncio.run(agent_loop.run_loop(_task()))
    assert calls == [("local_probe", {"n": 2})]
    assert out["status"] == "ok"
    assert out["tool_calls"][0]["tool"] == "local_probe"
    types = [e["type"] for e in events]
    assert "tool_call" in types and "tool_result" in types


def test_guard_aborts_before_side_effects(monkeypatch):
    executed: list[str] = []

    async def fake_execute(name, args=None, **kwargs):  # noqa: ANN001, ARG001
        executed.append(name)
        return {"status": "ok"}

    monkeypatch.setattr(sf, "execute", fake_execute)
    fake = _FakeLLM([("local_probe", {})])
    _patch_llm(monkeypatch, fake)

    out = asyncio.run(agent_loop.run_loop(_task(), guard=lambda: "预算耗尽"))
    assert out["status"] == "aborted"
    assert out["stopped_reason"] == "预算耗尽"
    assert executed == [], "触闸后不允许再执行任何工具"


def test_tool_call_limit_refuses_instead_of_looping(monkeypatch):
    async def fake_execute(name, args=None, **kwargs):  # noqa: ANN001, ARG001
        return {"status": "ok"}

    monkeypatch.setattr(sf, "execute", fake_execute)
    # 脚本里排 5 次调用，上限设为 1
    fake = _FakeLLM([("local_probe", {})] * 5)
    _patch_llm(monkeypatch, fake)
    monkeypatch.setattr(agent_loop.settings, "agent_loop_max_tool_calls", 1)

    out = asyncio.run(agent_loop.run_loop(_task()))
    statuses = [c["status"] for c in out["tool_calls"]]
    assert statuses.count("ok") == 1
    assert len(statuses) == 5, "超出上限的调用要如实记为 refused，而不是静默丢"


def test_shadow_mode_runs_no_side_effects(monkeypatch):
    executed: list[str] = []

    async def fake_execute(name, args=None, **kwargs):  # noqa: ANN001, ARG001
        executed.append(name)
        return {"status": "ok"}

    monkeypatch.setattr(sf, "execute", fake_execute)
    fake = _FakeLLM([("local_probe", {}), ("delegate_agent", {"stage": "edit"})])
    _patch_llm(monkeypatch, fake)

    out = asyncio.run(agent_loop.run_loop(_task(), shadow=True))
    assert executed == []
    assert [c["status"] for c in out["tool_calls"]] == ["shadowed", "shadowed"]


# ── 可用性判定（降级必须显式）───────────────────────────


def test_tool_loop_unavailable_without_credential(monkeypatch):
    monkeypatch.setattr(agent_loop.settings, "kernel_mode", "tool_loop")
    monkeypatch.setattr(agent_loop, "_has_credential", lambda: False)
    ok, why = agent_loop.tool_loop_available()
    assert ok is False and why == "no_llm_credential"


def test_tool_loop_unavailable_in_plan_mode(monkeypatch):
    monkeypatch.setattr(agent_loop.settings, "kernel_mode", "plan")
    ok, why = agent_loop.tool_loop_available()
    assert ok is False and "kernel_mode=plan" in why


def test_provider_failure_returns_structured_failure(monkeypatch):
    class _Boom:
        async def with_tools(self, **kwargs):  # noqa: ANN003, ARG002
            raise RuntimeError("provider 挂了")

    _patch_llm(monkeypatch, _Boom())
    out = asyncio.run(agent_loop.run_loop(_task()))
    assert out["status"] == "error"
    assert out["failure"]["category"] == "external_unavailable"
    assert out["failure"]["retryable"] == "retry_same"


def test_prompt_does_not_require_step_array(monkeypatch):
    """可证伪边界：提示词里给的是"初始假设"，不是必须执行的步骤表。"""
    from clipwright.schema.task import Plan, PlanStep

    task = _task()
    task.plan = Plan(rationale="初判", steps=[
        PlanStep(id="s1", capability="structure", title="先出结构")])
    prompt = agent_loop._user_prompt(task, "上下文")
    assert "初始假设" in prompt
    assert "不是必须执行的步骤表" in prompt
    assert json.dumps("steps", ensure_ascii=False) not in prompt


# ── 真实 provider 路径上的闸（P0-1 回归）────────────────


def test_control_flow_exception_survives_do_tool_call():
    """`_do_tool_call` 不许吞中止异常（否则闸变成"告诉模型出错了、继续下一轮"）。"""
    from clipwright.services.llm import LLMService

    def boom(name, args):  # noqa: ANN001, ARG001
        raise agent_loop.LoopAborted("预算耗尽")

    with pytest.raises(agent_loop.LoopAborted):
        asyncio.run(LLMService._do_tool_call(boom, False, "x", {}))


def test_business_error_is_still_converted():
    """业务异常仍要如实转成工具结果——不能把两类混在一起。"""
    from clipwright.services.llm import LLMService

    def boom(name, args):  # noqa: ANN001, ARG001
        raise ValueError("参数不对")

    out = asyncio.run(LLMService._do_tool_call(boom, False, "x", {}))
    assert "参数不对" in str(out.get("error"))


def test_guard_aborts_through_real_with_tools(monkeypatch):
    """走真实 `with_tools` → `_do_tool_call` 链路，验证中止真的能停住循环。"""
    from types import SimpleNamespace

    from clipwright.services import llm as llm_mod
    from clipwright.services.llm import LLMResponse, LLMService

    rounds = {"n": 0}

    async def fake_generate(self, messages=None, **kwargs):  # noqa: ANN001, ARG001
        rounds["n"] += 1
        return LLMResponse(
            content="", success=True, status_code=200,
            tool_calls=[SimpleNamespace(id=f"c{rounds['n']}", name="local_probe",
                                        arguments="{}")],
        )

    async def fake_execute(name, args=None, **kwargs):  # noqa: ANN001, ARG001
        return {"status": "ok"}

    monkeypatch.setattr(LLMService, "generate", fake_generate)
    monkeypatch.setattr(sf, "execute", fake_execute)
    monkeypatch.setattr(llm_mod, "_resolve_llm_provider", lambda *a, **k: None)

    def guard() -> str:
        return "预算耗尽"

    out = asyncio.run(agent_loop.run_loop(_task(), guard=guard))
    assert out["status"] == "aborted", "闸必须在模型请求中间真的停住"
    assert out["stopped_reason"] == "预算耗尽"
    assert rounds["n"] == 1, "中止后不许再发起下一轮模型请求"


def test_credential_probe_counts_role_binding(monkeypatch):
    """role-bound 凭据也算有凭据——否则生产会静默跑 plan 却以为在跑 tool_loop。"""
    monkeypatch.setattr(agent_loop.settings, "kernel_mode", "tool_loop")
    monkeypatch.setattr(agent_loop.settings, "llm_api_key", "")
    monkeypatch.setattr(agent_loop.settings, "llm_provider", "anthropic")
    monkeypatch.setattr(agent_loop, "_has_credential", lambda: True)
    ok, why = agent_loop.tool_loop_available()
    assert ok is True, why


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))

def test_repeated_identical_failure_aborts_loop(monkeypatch):
    """同一调用连续失败 3 次就中止——环境缺东西不该被当成"再想想能解决"。

    实测背景：没有工作区时，模型把 10 次调用全花在 list_dir/list_reports 的 error 上，
    最后交不出时间线还烧满回合（评估报告里能看到）。
    """
    async def always_fail(name, args=None, **kw):  # noqa: ANN001, ARG001
        return {"status": "error", "error": "未找到工作区"}

    monkeypatch.setattr(sf, "execute", always_fail)
    fake = _FakeLLM([("list_dir", {"path": "x"})] * 5)
    _patch_llm(monkeypatch, fake)

    out = asyncio.run(agent_loop.run_loop(_task()))
    assert out["status"] == "aborted"
    assert "连续失败 3 次" in out["stopped_reason"]
    # 中止得早：没有跑满 5 次
    assert len(out["tool_calls"]) == 3


def test_success_resets_failure_streak(monkeypatch):
    """中间成功一次就重置计数（不能把偶发失败累计成熔断）。"""
    state = {"n": 0}

    async def flaky(name, args=None, **kw):  # noqa: ANN001, ARG001
        state["n"] += 1
        if state["n"] % 2 == 1:
            return {"status": "error", "error": "偶发"}
        return {"status": "ok", "output": {}}

    monkeypatch.setattr(sf, "execute", flaky)
    fake = _FakeLLM([("list_dir", {"path": "x"})] * 4)
    _patch_llm(monkeypatch, fake)

    out = asyncio.run(agent_loop.run_loop(_task()))
    assert out["status"] == "ok", out.get("stopped_reason")


# ── B2：主 agent「说话再行动」────────────────────────────


def _real_path_env(monkeypatch, script):
    """走真实 `with_tools` + `generate` 打桩，返回 (events, rounds 计数)。"""
    from types import SimpleNamespace

    from clipwright.services import llm as llm_mod
    from clipwright.services.llm import LLMResponse, LLMService

    rounds = {"n": 0}
    scripted = list(script)

    async def fake_generate(self, messages=None, **kwargs):  # noqa: ANN001, ARG001
        rounds["n"] += 1
        item = scripted.pop(0) if scripted else (None, [])
        content, names = item
        return LLMResponse(
            content=content or "", success=True, status_code=200,
            tool_calls=[
                SimpleNamespace(id=f"c{rounds['n']}_{i}", name=n, arguments="{}")
                for i, n in enumerate(names)
            ],
        )

    async def fake_execute(name, args=None, **kwargs):  # noqa: ANN001, ARG001
        return {"status": "ok"}

    monkeypatch.setattr(LLMService, "generate", fake_generate)
    monkeypatch.setattr(sf, "execute", fake_execute)
    monkeypatch.setattr(llm_mod, "_resolve_llm_provider", lambda *a, **k: None)

    events: list[dict[str, Any]] = []
    monkeypatch.setattr(agent_loop, "emit",
                        lambda tid, t, m, d=None, **kw: events.append(
                            {"type": t, "message": m, "detail": d}))
    return events, rounds


def test_preamble_narration_precedes_tool_call(monkeypatch):
    """模型动手前说的话要**先进事件流**，顺序严格是 narration → tool_call → tool_result。"""
    events, rounds = _real_path_env(monkeypatch, [
        ("我先派 structure 阶段把大纲落出来，因为时间线要靠它。", ["local_probe"]),
        ("", []),
    ])

    out = asyncio.run(agent_loop.run_loop(_task()))
    assert out["status"] == "ok", out.get("stopped_reason")
    assert rounds["n"] >= 2

    preambles = [e for e in events if (e["detail"] or {}).get("kind") == "preamble"]
    assert len(preambles) == 1, "一轮只有一条 preamble"
    assert preambles[0]["type"] == "narration"
    assert "structure" in preambles[0]["message"]
    assert preambles[0]["detail"]["round"] == 1
    assert preambles[0]["detail"]["before_tool"] == ["local_probe"]

    # 顺序：preamble 一定在第一条 tool_call / tool_result 之前
    seq = [e["type"] for e in events]
    i_pre = events.index(preambles[0])
    i_call = seq.index("tool_call")
    i_res = seq.index("tool_result")
    assert i_pre < i_call < i_res


def test_no_preamble_emitted_when_content_empty(monkeypatch):
    """模型没说话就动手 → 不发 preamble 叙述（不能编一段"它说过"）。"""
    events, _ = _real_path_env(monkeypatch, [("", ["local_probe"]), ("", [])])

    asyncio.run(agent_loop.run_loop(_task()))
    preambles = [e for e in events if (e["detail"] or {}).get("kind") == "preamble"]
    assert preambles == []
    assert "tool_call" in [e["type"] for e in events]


def test_multiple_tool_calls_emit_single_preamble(monkeypatch):
    """同轮多个 tool_call 只发一条叙述（不是每个工具重复一遍）。"""
    events, _ = _real_path_env(monkeypatch, [
        ("先看一眼素材和报告，再决定派谁。", ["local_probe", "local_probe"]),
        ("", []),
    ])

    out = asyncio.run(agent_loop.run_loop(_task()))
    assert out["status"] == "ok", out.get("stopped_reason")
    preambles = [e for e in events if (e["detail"] or {}).get("kind") == "preamble"]
    assert len(preambles) == 1
    assert preambles[0]["detail"]["tool_count"] == 2
    assert preambles[0]["detail"]["before_tool"] == ["local_probe", "local_probe"]


def test_preamble_callback_failure_does_not_block_tools(monkeypatch):
    """叙述回调炸了也不能挡住工具执行（B2 实施步骤 4）。"""
    import clipwright.services.llm as llm_mod
    from clipwright.services.llm import LLMService

    monkeypatch.setattr(llm_mod, "_resolve_llm_provider", lambda *a, **k: None)
    raw = LLMService()

    calls: list[str] = []

    async def executor(name, args):  # noqa: ANN001
        calls.append(name)
        return {"status": "ok"}

    from types import SimpleNamespace
    from clipwright.services.llm import LLMResponse

    state = {"n": 0}

    async def fake_generate(self, messages=None, **kwargs):  # noqa: ANN001, ARG001
        state["n"] += 1
        if state["n"] == 1:
            return LLMResponse(
                content="我要动手了", success=True, status_code=200,
                tool_calls=[SimpleNamespace(id="c1", name="local_probe", arguments="{}")],
            )
        return LLMResponse(content="", success=True, status_code=200)

    monkeypatch.setattr(LLMService, "generate", fake_generate)

    def boom(content, round_no, tool_calls):  # noqa: ANN001, ARG001
        raise RuntimeError("回调炸了")

    resp = asyncio.run(raw.with_tools(
        system_prompt="s", user_prompt="u", tool_executor=executor,
        tools=[{"type": "function", "function": {"name": "local_probe"}}],
        max_tool_rounds=3, on_preamble=boom,
    ))
    assert resp.success is True
    assert calls == ["local_probe"], "回调异常后工具仍然要执行"


# ── B5：reasoning → thinking 事件（已报缺口 1）────────────────


def _reasoning_env(monkeypatch, *, reasoning_per_round: list[str]):
    """真实 with_tools 路径 + 逐轮 reasoning，返回 (events, rounds)。"""
    from types import SimpleNamespace

    from clipwright.services import llm as llm_mod
    from clipwright.services.llm import LLMResponse, LLMService

    rounds = {"n": 0}
    seen: list[dict[str, Any]] = []

    async def fake_generate(self, messages=None, on_reasoning=None, **kwargs):  # noqa: ANN001
        rounds["n"] += 1
        idx = rounds["n"] - 1
        text = reasoning_per_round[idx] if idx < len(reasoning_per_round) else ""
        seen.append({"round": rounds["n"], "reasoning": text})
        if text and on_reasoning is not None:
            on_reasoning(text)
        if rounds["n"] == 1:
            return LLMResponse(
                content="我打算先看看素材。", success=True, status_code=200,
                tool_calls=[SimpleNamespace(id="c1", name="local_probe", arguments="{}")],
            )
        return LLMResponse(content="做完了", success=True, status_code=200)

    async def fake_execute(name, args=None, **kwargs):  # noqa: ANN001, ARG001
        return {"status": "ok"}

    monkeypatch.setattr(LLMService, "generate", fake_generate)
    monkeypatch.setattr(sf, "execute", fake_execute)
    monkeypatch.setattr(llm_mod, "_resolve_llm_provider", lambda *a, **k: None)

    events: list[dict[str, Any]] = []
    monkeypatch.setattr(agent_loop, "emit",
                        lambda tid, t, m, d=None, **kw: events.append(
                            {"type": t, "message": m, "detail": d}))
    return events, rounds, seen


def test_reasoning_becomes_thinking_events(monkeypatch):
    """provider 的 reasoning 必须进事件流（旧实现整段丢在日志里，前端只能显示"未提供"）。"""
    events, rounds, _ = _reasoning_env(
        monkeypatch, reasoning_per_round=["先判断素材够不够。", "素材够，可以派 edit。"])

    out = asyncio.run(agent_loop.run_loop(_task()))
    assert out["status"] == "ok", out.get("stopped_reason")
    assert rounds["n"] >= 2

    thinking = [e for e in events if e["type"] == "thinking"]
    assert thinking, "reasoning 没有变成 thinking 事件"
    deltas = [e for e in thinking if not (e["detail"] or {}).get("done")]
    assert len(deltas) == 2, "逐轮各发一块"
    assert deltas[0]["message"].startswith("先判断素材")
    assert deltas[0]["detail"]["round"] == 1
    assert deltas[1]["detail"]["round"] == 2
    # 收尾有一条 done，且只发一次
    assert sum(1 for e in thinking if (e["detail"] or {}).get("done")) == 1


def test_thinking_precedes_narration_and_tool_call(monkeypatch):
    """顺序契约：thinking → narration(preamble) → tool_call。"""
    events, _, _ = _reasoning_env(monkeypatch, reasoning_per_round=["先看一眼。", ""])

    asyncio.run(agent_loop.run_loop(_task()))
    seq = [e["type"] for e in events]
    i_think = next(i for i, e in enumerate(events) if e["type"] == "thinking")
    i_pre = next(i for i, e in enumerate(events)
                 if e["type"] == "narration" and (e["detail"] or {}).get("kind") == "preamble")
    i_call = seq.index("tool_call")
    assert i_think < i_pre < i_call


def test_no_thinking_when_provider_has_no_reasoning(monkeypatch):
    """provider 不返回 reasoning → 不发 thinking，也不许伪造（前端显示"未提供"）。"""
    events, _, _ = _reasoning_env(monkeypatch, reasoning_per_round=["", ""])

    asyncio.run(agent_loop.run_loop(_task()))
    assert [e for e in events if e["type"] == "thinking"] == []


def test_thinking_buffer_is_per_task(monkeypatch):
    """缓冲按 task 隔离：A 的思考不会被 B 取走（旧实现是全局单槽）。"""
    _reasoning_env(monkeypatch, reasoning_per_round=["A 的思考", ""])
    asyncio.run(agent_loop.run_loop(_task()))
    assert "A 的思考" in agent_loop.take_thinking("task_loop")
    assert agent_loop.take_thinking("task_loop") == ""  # 取走即清空
    assert agent_loop.take_thinking("另一个任务") == ""


def test_on_reasoning_failure_does_not_break_generation(monkeypatch):
    """回调炸了只记日志，不吞掉生成本身（B5 实施步骤要求）。"""
    from clipwright.services import llm as llm_mod
    from clipwright.services.llm import LLMResponse, LLMService

    monkeypatch.setattr(llm_mod, "_resolve_llm_provider", lambda *a, **k: None)

    class _Client:
        def generate(self, **kwargs):  # noqa: ANN003
            return LLMResponse(content="ok", success=True, status_code=200,
                               reasoning_content="想了想")

    monkeypatch.setattr(LLMService, "_routed_client_and_model",
                        lambda self, use_flash=False: (_Client(), None))

    def boom(delta):  # noqa: ANN001
        raise RuntimeError("回调炸了")

    resp = asyncio.run(LLMService().generate(
        messages=[{"role": "user", "content": "hi"}], on_reasoning=boom))
    assert resp.success is True and resp.content == "ok"
