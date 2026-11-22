"""research worker 工作区上下文与失败语义回归。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from clipwright.schema.run import RunSpec
from clipwright.services.agent_run import _run_research
from clipwright.supervisor import spawn as sp
from clipwright.supervisor.tracker import clear_trackers
from clipwright.supervisor.worker_registry import clear_workers, find_worker
from clipwright.supervisor.worker_tools import build_executor


@pytest.fixture(autouse=True)
def _clean():
    clear_trackers()
    clear_workers()
    yield
    clear_trackers()
    clear_workers()


def test_worker_read_file_resolves_workspace(tmp_path, monkeypatch) -> None:
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "a.md").write_text("工作区报告", encoding="utf-8")

    import clipwright.security as security

    monkeypatch.setattr(security, "allowed_media_roots", lambda: [tmp_path])
    ex = build_executor(
        "p_read", "sa", allowed=("read_file",),
        workspace_root=str(tmp_path), project_id="proj_test",
    )
    out = asyncio.run(ex("read_file", {"path": "reports/a.md"}))
    assert out.get("content") == "工作区报告"
    assert "路径不在允许" not in str(out.get("error", ""))


def test_research_context_inherits_workspace(tmp_path, monkeypatch) -> None:
    captured: dict = {}

    async def fake_spawn(ctx, **kwargs):
        captured["extra"] = dict(ctx.extra_params)
        return {"status": "done", "output": "ok", "tool_calls": []}

    monkeypatch.setattr(sp, "spawn_subagent", fake_spawn)
    task = SimpleNamespace(
        id="task_research",
        request=SimpleNamespace(extra={
            "workspace_root": str(tmp_path),
            "workspace_project_id": "proj_abc",
            "host_agent": "supervisor",  # 不能透传，否则会被拓扑闸拒绝
        }),
    )
    result = asyncio.run(_run_research(
        RunSpec(kind="research", role="核查员", brief="查报告"), task, "run_x", "trace_x",
    ))
    assert result.status == "ok"
    assert captured["extra"]["workspace_root"] == str(tmp_path)
    assert captured["extra"]["project_id"] == "proj_abc"
    assert "host_agent" not in captured["extra"]



def test_research_failure_is_not_reported_as_ok(monkeypatch) -> None:
    async def fake_spawn(ctx, **kwargs):
        return {
            "status": "done", "output": "本次调研无法完成",
            "failure": {"category": "tool_failed", "retryable": "retry_same",
                        "blocking_point": "read_file failed", "evidence": []},
            "tool_calls": [],
        }

    monkeypatch.setattr(sp, "spawn_subagent", fake_spawn)
    task = SimpleNamespace(id="t", request=SimpleNamespace(extra={}))
    result = asyncio.run(_run_research(
        RunSpec(kind="research", role="r", brief="b"), task, "run_y", "trace_y",
    ))
    assert result.status == "failed"
    assert result.failure and result.failure["category"] == "tool_failed"


def test_all_tool_failures_mark_worker_failed(monkeypatch) -> None:
    from clipwright.services.llm import LLMService

    async def fake_with_tools(self, **kwargs):
        await kwargs["tool_executor"]("read_file", {"path": "missing.md"})
        return SimpleNamespace(success=True, content="无法读取", status_code=200,
                               duration_ms=10, usage=None)

    monkeypatch.setattr(LLMService, "with_tools", fake_with_tools)
    ctx = SimpleNamespace(
        pipeline_id="p_fail",
        extra_params={"workspace_root": "J:\\Clipwright\\workspaces\\missing"},
    )
    result = asyncio.run(sp.spawn_subagent(
        ctx, role="核查员", tools=["read_file"], system_prompt="读文件",
    ))
    assert result["status"] == "failed"
    assert result["failure"]["category"] == "tool_failed"
    assert find_worker(result["worker_id"]).status == "failed"


def test_worker_grep_workspace(tmp_path, monkeypatch) -> None:
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "a.md").write_text("alpha\n目标证据\n", encoding="utf-8")
    import clipwright.security as security

    monkeypatch.setattr(security, "allowed_media_roots", lambda: [tmp_path])
    ex = build_executor(
        "p_grep", "sa", allowed=("grep_workspace",),
        workspace_root=str(tmp_path), project_id="p",
    )
    out = asyncio.run(ex("grep_workspace", {"pattern": "目标证据", "path": "reports"}))
    assert out.get("count") == 1
    assert out["items"][0]["line"] == 2


def test_worker_glob_and_range(tmp_path, monkeypatch) -> None:
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "a.md").write_text("0123456789", encoding="utf-8")
    import clipwright.security as security

    monkeypatch.setattr(security, "allowed_media_roots", lambda: [tmp_path])
    ex = build_executor(
        "p_ws", "sa", allowed=("glob_workspace", "read_file_range"),
        workspace_root=str(tmp_path), project_id="p",
    )
    glob = asyncio.run(ex("glob_workspace", {"pattern": "reports/*.md"}))
    assert glob["count"] == 1
    part = asyncio.run(ex("read_file_range", {"path": "reports/a.md", "offset": 2, "length": 4}))
    assert part["content"] == "2345"
