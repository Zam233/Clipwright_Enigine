"""B2：dry-run 预览贯通 Task（closeout plan B2 / PRD §6 G 的预览语义）。

三条断言，缺一都不算修好：

1. `proceed` 勾了 dry-run → Task 的 `extra["dry_run"]` 必须为真（旧实现只写进
   随老管线失效的 `pipeline_req.dry_run`，Task 完全看不到）；
2. 预览模式下原子工具走 `surface.execute(dry_run=True)` 的**结构预检**，
   派发类工具（`delegate_agent` / `research_agent`）**直接拒绝**；
3. 预览收尾的 `reason` 是 `dry_run_preview`，且**不计入**任务成功率
   （`gate_passed=False`）——否则预览跑一遍就把成功率稀释成"出过片"。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from clipwright.schema.task import TaskRequest
from clipwright.services import agent_kernel as ak
from clipwright.services import agent_loop, evaluation
from clipwright.tool import surface as sf


class _RecordingProvider:
    """一个假 provider：先调一个原子工具，再试图派一个子 agent。"""

    def __init__(self) -> None:
        self.seen: list[tuple[str, dict]] = []

    async def with_tools(self, *, system_prompt, user_prompt, tool_executor, tools,
                         max_tool_rounds=10, **kw):  # noqa: ANN001, ARG002
        await tool_executor("media_probe", {"input_path": "a.mp4"})
        await tool_executor("delegate_agent", {"stage": "edit"})

        class _Resp:
            content = "预览完成"
            tool_calls: list = []
            success = True
            status_code = 200

        return _Resp()


def _patch_provider(monkeypatch) -> _RecordingProvider:
    prov = _RecordingProvider()
    import clipwright.services.llm as llm_mod

    monkeypatch.setattr(llm_mod, "LLMService", lambda *a, **k: prov)
    return prov


def test_dry_run_downgrades_tools_to_preflight(monkeypatch):
    """原子工具走预检（不执行），派发类工具被拒。"""
    calls: list[dict[str, Any]] = []

    async def fake_surface_execute(name, args=None, *, pipeline_id="", idem_key="",
                                   dry_run=False, task=None):  # noqa: ANN001, ARG001
        calls.append({"name": name, "dry_run": dry_run, "args": dict(args or {})})
        return {"status": "dry_run", "dry_run": True, "entry_dry_run": False,
                "ok": True, "checks": []}

    monkeypatch.setattr(sf, "execute", fake_surface_execute)
    _patch_provider(monkeypatch)

    task = TaskRequest(topic="x", extra={"dry_run": True})
    from types import SimpleNamespace
    task_obj = SimpleNamespace(
        id="task_dry", request=task, plan=None,
        limits=SimpleNamespace(max_turns=10, max_tool_calls=20, max_wall_sec=600,
                               budget_usd=None),
        usage=SimpleNamespace(turns=0, tool_calls=0, wall_sec=0.0, cost_usd=0.0),
    )
    out = asyncio.run(agent_loop.run_loop(task_obj, dry_run=True))

    assert out["dry_run"] is True
    names = [c["name"] for c in calls]
    assert names == ["media_probe"], "派发类工具不该进 surface.execute"
    assert calls[0]["dry_run"] is True, "原子工具必须带 dry_run=True 走预检"
    assert any(c["status"] == "refused" for c in out["tool_calls"]), "派发要被拒并留痕"


def test_kernel_marks_preview_and_does_not_count_as_success(tmp_path, monkeypatch):
    """预览收尾 reason = dry_run_preview，且不进任务成功率。"""
    class _NoopDryDriver:
        async def __call__(self, task, *, context_text="", shadow=False,  # noqa: ANN001
                           guard=None, on_tool_call=None, dry_run=False):  # noqa: ANN001
            assert dry_run is True, "Kernel 必须把 dry_run 透传给驱动器"
            return {"status": "ok", "finish": {"conclusion": "预览完成"},
                    "tool_calls": [], "rounds": 1, "dry_run": True,
                    "stopped_reason": "model_final"}

    evaluation.reset()
    kernel = ak.AgentKernel(ak.KernelDeps(
        driver=_NoopDryDriver(), checkpoint_dir=tmp_path / "tasks"))
    task = kernel.create(TaskRequest(topic="x", plan_only=False,
                                    extra={"dry_run": True}))
    task = asyncio.run(kernel.run(task))

    assert task.finish is not None
    assert task.finish.reason == "dry_run_preview"
    assert task.status == "done"
    rows = evaluation.records(task.id)
    assert rows and rows[0]["kind"] == "task"
    assert rows[0]["extra"]["gate_passed"] is False, "预览不算出片成功"
    evaluation.reset()


def test_real_run_reason_unchanged(tmp_path):
    """不勾预览时口径不变（防止为了修 dry-run 把正常收尾也改了）。"""
    class _NoopDriver:
        async def __call__(self, task, *, context_text="", shadow=False,  # noqa: ANN001
                           guard=None, on_tool_call=None, dry_run=False):  # noqa: ANN001
            assert dry_run is False
            return {"status": "ok", "finish": {"conclusion": "做完了"},
                    "tool_calls": [], "rounds": 1, "stopped_reason": "model_final"}

    kernel = ak.AgentKernel(ak.KernelDeps(
        driver=_NoopDriver(), checkpoint_dir=tmp_path / "tasks"))
    task = kernel.create(TaskRequest(topic="x", plan_only=False))
    task = asyncio.run(kernel.run(task))
    assert task.finish is not None and task.finish.reason != "dry_run_preview"


def test_proceed_passes_dry_run_into_task_extra(monkeypatch):
    """`_proceed_as_task` 必须把 dry-run 落到 Task.extra（旧实现在这里断了）。"""
    from clipwright.api import requirements as req_api

    captured: dict[str, Any] = {}

    class _FakeKernel:
        def create(self, task_req):  # noqa: ANN001
            captured["extra"] = dict(task_req.extra)
            from types import SimpleNamespace

            return SimpleNamespace(id="task_dry2", plan=None, request=task_req)

        async def plan(self, task):  # noqa: ANN001, ARG002
            return None

    monkeypatch.setattr("clipwright.services.agent_kernel.AgentKernel",
                        lambda *a, **k: _FakeKernel())
    monkeypatch.setattr(req_api, "_register_owner", lambda *a, **k: None, raising=False)
    monkeypatch.setattr("clipwright.api.task._register_owner", lambda *a, **k: None)
    monkeypatch.setattr("clipwright.api.task._schedule", lambda *a, **k: None)
    monkeypatch.setattr(req_api._service, "_persist", lambda *a, **k: None)

    class _WS:
        project_id = "proj_dry"

        class dirs:  # noqa: N801
            @staticmethod
            def root():
                return "."

        def write_docs(self, *a, **k):  # noqa: ANN002, ANN003
            return {}

    monkeypatch.setattr("clipwright.services.workspace.ensure_workspace",
                        lambda *a, **k: (_WS(), {}))

    from types import SimpleNamespace

    pipeline_req = SimpleNamespace(
        topic="预览选题", persona_id="default", category_plugin_id="knowledge_longform",
        duration_sec=0.0, extra_params={}, dry_run=True,
    )
    req = SimpleNamespace(session_id="sess_dry", project_id="proj_dry")
    asyncio.run(req_api._proceed_as_task(
        session={"creative_brief": "", "production_plan": {"scene_count": 1}},
        req=req, request=SimpleNamespace(headers={}),
        user_inputs={}, plan_data={"scene_count": 1},
        pipeline_req=pipeline_req, pipeline_id="pl_dry",
    ))
    assert captured["extra"].get("dry_run") is True, "dry-run 必须进 Task.extra"
