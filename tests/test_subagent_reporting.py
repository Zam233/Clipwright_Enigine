"""主代理调度子代理 + 子代理失败详情回传（收口批次）。

要证明的不是"能 spawn"，而是三件更细的事：
1. 主代理能**查看/取消**子代理，不只会上车；
2. 子代理失败时回传的是**结构化失败**（类别/可否重试/证据/工具轨迹/花费），
   而不是一句截断字符串；
3. 主代理据此**再做一次判断**：可重试的失败自动重试一次，不可重试的才收尾；
   等人工审核时任务暂停（不是失败）。
"""

from __future__ import annotations

import asyncio

import pytest

from clipwright.config import settings
from clipwright.schema.task import Plan, PlanStep, TaskRequest
from clipwright.services import agent_kernel as ak
from clipwright.services import task_events as te
from clipwright.supervisor.tracker import get_tracker


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    ak.reset_kernel_state()
    te.clear()
    monkeypatch.setattr(settings, "agent_mode", "on", raising=False)
    yield
    ak.reset_kernel_state()
    te.clear()


def _task():
    from clipwright.schema.task import Task

    return Task(id="task_sub", request=TaskRequest(topic="x"))


# ── 1. 查询 / 取消 ────────────────────────────────────


def _dispatch(tool: str, args: dict | None = None):
    """经工具面调用 agent 工具（subagent_capability 适配层已在 B11 退役）。"""
    from clipwright.tool import surface as sf

    return asyncio.run(sf.execute(tool, dict(args or {}),
                                  pipeline_id="task_sub", task=_task()))


def test_list_subagents_reports_tree():
    tracker = get_tracker("task_sub")
    node = tracker.register("核查员")
    tracker.update(node.id, status="running", last_output="正在看第 3 段")

    out = _dispatch("list_agent_runs")
    assert out["status"] == "ok"
    assert "runs" in out["output"]


def test_get_status_and_missing():
    tracker = get_tracker("task_sub")
    node = tracker.register("核查员")
    tracker.update(node.id, status="done")

    # 查询/取消按 run 走：tracker 里的 subagent_id 作为兼容别名传入
    ok = _dispatch("get_agent_run", {"subagent_id": node.id})
    assert ok["status"] == "ok"

    bad = _dispatch("get_agent_run", {"subagent_id": "sa_nope"})
    assert bad["status"] == "error"
    assert "没有找到" in bad["error"]


def test_cancel_subagent():
    tracker = get_tracker("task_sub")
    node = tracker.register("核查员")
    tracker.update(node.id, status="running")

    out = _dispatch("cancel_agent_run", {"subagent_id": node.id})
    assert out["status"] == "ok"
    assert out["output"]["status"] == "cancelled"


# ── 2. 失败详情、等审核、成功结论 ─────────────────────────


def test_failure_report_is_structured(monkeypatch):
    from clipwright.supervisor import spawn as sp

    async def fake_spawn(ctx, **kwargs):  # noqa: ANN001
        return {
            "worker_id": "w_1", "subagent_id": "sa_1", "role": kwargs["role"],
            "status": "failed", "error": "LLM 调用失败: 429",
            "failure": {
                "category": "external_unavailable", "retryable": "retry_same",
                "blocking_point": "429 限流",
                "evidence": [{"kind": "error", "text": "429 限流"}],
                "tool_calls": 2,
            },
            "tool_calls": [
                {"tool": "read_file", "ok": True, "brief": "读了 a.mp4 清单"},
                {"tool": "web_search", "ok": False, "error": "未配置"},
            ],
            "duration_ms": 1200,
        }

    monkeypatch.setattr(sp, "spawn_subagent", fake_spawn)
    out = _dispatch("research_agent", {"role": "核查员", "brief": "查素材"})
    assert out["status"] == "error"
    assert out["failure"]["category"] == "external_unavailable"
    assert out["failure"]["retryable"] == "retry_same"
    # 主代理能看到它调了哪些工具、哪个失败（tool payload 里带 tool_calls）
    assert [c["tool"] for c in out["output"]["tool_calls"]] == ["read_file", "web_search"]
    assert out["cost_usd"] is None


def test_pending_review_is_not_failure(monkeypatch):
    from clipwright.supervisor import spawn as sp

    async def fake_spawn(ctx, **kwargs):  # noqa: ANN001
        return {
            "worker_id": "w_2", "subagent_id": "sa_2", "status": "pending_review",
            "review": {"risk": "high", "reasons": ["会删除目录"]},
            "needs_human_review": True,
        }

    monkeypatch.setattr(sp, "spawn_subagent", fake_spawn)
    out = _dispatch("research_agent", {"role": "运维核查员", "brief": "查权限"})
    # 等审核：surface 归一到 ok（跑完了），但结构化 failure 里明确是 needs_review
    assert out["output"]["status"] == "needs"
    assert out["failure"]["category"] == "needs_review"
    assert out["failure"]["review"]["risk"] == "high"


def test_success_conclusion_visible_to_main_agent(monkeypatch):
    from clipwright.supervisor import spawn as sp

    async def fake_spawn(ctx, **kwargs):  # noqa: ANN001
        return {
            "worker_id": "w_3", "subagent_id": "sa_3", "status": "done",
            "output": "第 3 段是占位图，建议换实拍",
            "cost_usd": 0.02, "tokens": 1500, "duration_ms": 900,
            "tool_calls": [{"tool": "read_file", "ok": True, "brief": "看清单"}],
        }

    monkeypatch.setattr(sp, "spawn_subagent", fake_spawn)
    out = _dispatch("research_agent", {"role": "核查员", "brief": "查占位图"})
    assert out["status"] == "ok"
    assert "占位图" in out["output"]["summary"]
    assert out["cost_usd"] == 0.02
    assert out["output"]["tokens"] == 1500
    assert out["output"]["tool_calls"][0]["tool"] == "read_file"


def test_spawn_rejection_has_failure_block(monkeypatch):
    from clipwright.supervisor import spawn as sp

    async def boom(ctx, **kwargs):  # noqa: ANN001
        raise sp.SpawnError("并发子代理已达上限（6）")

    monkeypatch.setattr(sp, "spawn_subagent", boom)
    out = _dispatch("research_agent", {"role": "核查员", "brief": "查素材"})
    assert out["status"] == "refused"
    assert out["failure"]["category"] == "spawn_rejected"
    assert "并发" in out["failure"]["blocking_point"]


# ── 3. 主代理据失败再做判断（retry vs finish）────────────────


class _ResultDriver:
    """按给定结果序列"调用一次 delegate_agent"，用来观察 Kernel 侧语义。"""

    def __init__(self, results: list[dict]) -> None:
        self.results = results
        self.executed: list[dict] = []

    async def __call__(self, task, *, context_text="", shadow=False, dry_run=False,  # noqa: ANN001
                       guard=None, on_tool_call=None):  # noqa: ANN001
        for result in self.results:
            if guard is not None:
                hit = guard()
                if hit:
                    return {"status": "aborted", "stopped_reason": str(hit),
                            "tool_calls": [{"tool": "delegate_agent"}]}
            self.executed.append(result)
            if on_tool_call is not None:
                on_tool_call("delegate_agent", result, {"stage": "material"})
            if str(result.get("status") or "") in ("pending_approval",
                                                   "needs_human_review"):
                return {"status": "aborted", "stopped_reason": "pause",
                        "tool_calls": [{"tool": "delegate_agent"}]}
        last = self.results[-1] if self.results else {}
        if str(last.get("status") or "ok") not in ("ok", ""):
            return {"status": "error", "error": str(last.get("error") or "工具失败"),
                    "stopped_reason": "tool_failed",
                    "failure": last.get("failure") or {"category": "tool_failed",
                                                       "retryable": "permanent",
                                                       "blocking_point": "",
                                                       "evidence": []},
                    "tool_calls": [{"tool": "delegate_agent"}]}
        return {"status": "ok", "finish": {"conclusion": "完成"},
                "tool_calls": [{"tool": "delegate_agent"}], "rounds": 2,
                "stopped_reason": "model_final"}


def _kernel(tmp_path, driver):
    return ak.AgentKernel(ak.KernelDeps(driver=driver,
                                        checkpoint_dir=tmp_path / "tasks"))


def test_retryable_failure_is_surfaced_for_model_to_retry(tmp_path):
    """可重试失败：Kernel **不**自己重试（M3 起由模型决定换参数/换人重来），

    但必须把 `retryable=retry_same` 与分类如实交到下一轮上下文里，
    否则模型只能瞎猜。
    """
    driver = _ResultDriver([{
        "status": "error", "error": "429 限流",
        "failure": {"category": "external_unavailable", "retryable": "retry_same",
                    "blocking_point": "429", "evidence": [{"kind": "error", "text": "429"}]},
    }])
    k = _kernel(tmp_path, driver)
    task = k.create(TaskRequest(topic="x", plan_only=False))
    task = asyncio.run(k.run(task))

    facts = task.context_digest[-1]["facts"]
    assert facts.get("failure_category") == "external_unavailable"
    assert facts.get("failure_retryable") == "retry_same"
    assert task.status == "failed"


def test_permanent_failure_finishes_with_category(tmp_path):
    driver = _ResultDriver([{
        "status": "error", "error": "权限不足",
        "failure": {"category": "input_insufficient", "retryable": "needs_human",
                    "blocking_point": "401 未授权",
                    "evidence": [{"kind": "error", "text": "401 未授权"}]},
    }])
    k = _kernel(tmp_path, driver)
    task = k.create(TaskRequest(topic="x", plan_only=False))
    task = asyncio.run(k.run(task))

    assert task.status == "failed"
    assert "input_insufficient" in task.finish.conclusion
    assert "needs_human" in task.finish.conclusion


def test_needs_human_review_pauses_task(tmp_path):
    driver = _ResultDriver([{
        "status": "needs_human_review", "approval_id": "w_9", "risk": "high",
        "reasons": ["会删除目录"], "role": "运维核查员",
    }])
    k = _kernel(tmp_path, driver)
    task = k.create(TaskRequest(topic="x", plan_only=False))
    task = asyncio.run(k.run(task))

    assert task.status == "paused"
    assert any(e.type == "approval_request" for e in te.events(task.id))
    assert task.finish is None, "等审核不是失败，不许写终态"


def test_failure_evidence_reaches_digest(tmp_path):
    driver = _ResultDriver([{
        "status": "error", "error": "工具失败",
        "failure": {"category": "tool_failed", "retryable": "permanent",
                    "evidence": [], "tool_calls": 3},
    }])
    k = _kernel(tmp_path, driver)
    task = k.create(TaskRequest(topic="x", plan_only=False))
    task = asyncio.run(k.run(task))

    facts = task.context_digest[-1]["facts"]
    assert facts.get("failure_category") == "tool_failed"
    assert facts.get("failure_tool_calls") == 3
