"""执行能力桥（主 Agent 的 pwsh/bash 与 python 通道）。

设计要点：dangerous 档**不进工具面**，但主 agent 必须能跑命令——所以走执行网关：
判定 → （必要时）授权 → 沙盒 → 审计。`pending_approval` 不是失败：
Step 保持未完成、任务转 paused，用户授权后 resume 继续。
"""

from __future__ import annotations

import asyncio

import pytest

from clipwright.config import settings
from clipwright.schema.task import Plan, PlanStep, TaskRequest
from clipwright.services import agent_kernel as ak
from clipwright.services import exec_capability as ec
from clipwright.services import task_events as te
from clipwright.supervisor import gateway as gw


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    ak.reset_kernel_state()
    te.clear()
    gw.clear_gateway()
    monkeypatch.setattr(settings, "agent_mode", "on", raising=False)
    yield
    ak.reset_kernel_state()
    te.clear()
    gw.clear_gateway()


def _task():
    from clipwright.schema.task import Task

    return Task(id="task_exec", request=TaskRequest(topic="x"))


def test_refuses_when_mode_off(monkeypatch):
    monkeypatch.setattr(settings, "agent_mode", "off", raising=False)
    out = asyncio.run(ec.run(_task(), PlanStep(id="s1", capability="run_shell",
                                               args={"command": "echo hi"})))
    assert out["status"] == "refused"


def test_shell_runs_through_gateway(monkeypatch):
    calls: list[tuple] = []

    async def fake_run(pid, command, **kwargs):  # noqa: ANN001
        calls.append((pid, command, kwargs))
        return {"status": "done", "exitCode": 0, "outputTail": "hi\n", "durationMs": 5}

    monkeypatch.setattr(gw, "run_command", fake_run)
    out = asyncio.run(ec.run(_task(), PlanStep(
        id="s1", capability="run_shell", args={"command": "echo hi"},
    )))
    assert out["status"] == "ok"
    assert out["output"]["exit_code"] == 0
    assert "hi" in out["output"]["output_tail"]
    assert calls[0][0] == "task_exec"
    assert calls[0][1] == "echo hi"


def test_python_runs_through_gateway(monkeypatch):
    async def fake_py(pid, code, **kwargs):  # noqa: ANN001
        return {"status": "done", "exitCode": 0, "outputTail": "42\n", "durationMs": 3}

    monkeypatch.setattr(gw, "run_python", fake_py)
    out = asyncio.run(ec.run(_task(), PlanStep(
        id="s1", capability="run_python", args={"code": "print(42)"},
    )))
    assert out["status"] == "ok"
    assert "42" in out["output"]["output_tail"]


def test_nonzero_exit_is_error_but_reported(monkeypatch):
    async def fake_run(pid, command, **kwargs):  # noqa: ANN001
        return {"status": "done", "exitCode": 2, "outputTail": "boom", "durationMs": 1}

    monkeypatch.setattr(gw, "run_command", fake_run)
    out = asyncio.run(ec.run(_task(), PlanStep(
        id="s1", capability="run_shell", args={"command": "false"},
    )))
    assert out["status"] == "error"
    assert "退出码 2" in out["error"]
    assert out["output"]["output_tail"] == "boom"


def test_pending_approval_is_reported_not_executed(monkeypatch):
    async def fake_run(pid, command, **kwargs):  # noqa: ANN001
        return {"status": "pending_approval",
                "approval": {"id": "ap_1", "riskLevel": "high",
                             "riskReasons": ["会删除目录"], "command": command}}

    monkeypatch.setattr(gw, "run_command", fake_run)
    out = asyncio.run(ec.run(_task(), PlanStep(
        id="s1", capability="run_shell", args={"command": "rm -rf build"},
    )))
    assert out["status"] == "pending_approval"
    assert out["approval_id"] == "ap_1"
    assert out["risk"] == "high"
    assert "尚未执行" in out["note"] or "已暂停" in out["note"]


def test_rejected_command_is_refused(monkeypatch):
    async def fake_run(pid, command, **kwargs):  # noqa: ANN001
        return {"status": "rejected", "reason": "硬底线：读凭据"}

    monkeypatch.setattr(gw, "run_command", fake_run)
    out = asyncio.run(ec.run(_task(), PlanStep(
        id="s1", capability="run_shell", args={"command": "cat .env"},
    )))
    assert out["status"] == "refused"
    assert "硬底线" in out["error"]


def test_missing_command_is_refused():
    out = asyncio.run(ec.run(_task(), PlanStep(id="s1", capability="run_shell", args={})))
    assert out["status"] == "refused"


class _ApprovalDriver:
    """脚本化驱动器：按模型行为调一次危险工具；授权状态由外部 dict 控制。"""

    def __init__(self, state: dict) -> None:
        self.state = state
        self.calls: list[str] = []

    async def __call__(self, task, *, context_text="", shadow=False, dry_run=False,  # noqa: ANN001
                       guard=None, on_tool_call=None):
        if guard is not None:
            hit = guard()
            if hit:
                return {"status": "aborted", "stopped_reason": hit,
                        "tool_calls": [{"tool": "run_shell"}]}
        result = ({"status": "ok", "output": {"exit_code": 0, "output_tail": "hi"}}
                  if self.state["approved"]
                  else {"status": "pending_approval", "approval_id": "ap_9",
                        "risk": "high", "note": "已暂停"})
        self.calls.append(result["status"])
        if on_tool_call is not None:
            on_tool_call("run_shell", result)
        if result["status"] != "ok":
            # 模型拿到"等授权"后不该继续调更多工具；Kernel 此时已把任务置 paused
            return {"status": "aborted", "stopped_reason": "pause",
                    "tool_calls": [{"tool": "run_shell"}]}
        return {"status": "ok", "finish": {"conclusion": "已执行"},
                "tool_calls": [{"tool": "run_shell"}], "rounds": 2,
                "stopped_reason": "model_final"}


def test_kernel_pauses_task_on_pending_approval(monkeypatch):
    """Kernel 侧语义：等授权 → 任务 paused、发 approval_request、不收尾（可 resume）。"""
    state = {"approved": False}
    driver = _ApprovalDriver(state)
    k = ak.AgentKernel(ak.KernelDeps(driver=driver,
                                     checkpoint_dir=__import__("tempfile").mkdtemp()))
    task = k.create(TaskRequest(topic="x", plan_only=False))
    task = asyncio.run(k.run(task))

    assert task.status == "paused"
    assert any(e.type == "approval_request" for e in te.events(task.id))
    assert task.finish is None, "等授权不是失败，不许写终态"


def test_kernel_resumes_after_approval(monkeypatch):
    """授权后 resume：任务重进 loop 并把工具跑完。"""
    state = {"approved": False}
    driver = _ApprovalDriver(state)
    k = ak.AgentKernel(ak.KernelDeps(driver=driver,
                                     checkpoint_dir=__import__("tempfile").mkdtemp()))
    task = k.create(TaskRequest(topic="x", plan_only=False))
    task = asyncio.run(k.run(task))
    assert task.status == "paused"

    state["approved"] = True
    task = asyncio.run(k.resume(task.id))
    assert task.status in ("done", "failed")
    assert driver.calls[-1] == "ok"
