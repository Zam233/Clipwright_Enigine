"""B04：RunStore 持久化、task scope、attempt 原子分配、重启回放。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from clipwright.config import settings
from clipwright.schema.run import RunSpec
from clipwright.services import agent_run, result_assembler as ra, run_store


def _result(task_id: str, run_id: str, *, stage: str = "structure", attempt: int = 1,
            status: str = "ok"):
    return ra.build(run_id=run_id, kind="stage", task_id=task_id, trace_id=task_id,
                    stage=stage, attempt=attempt, status=status, summary="x")


def _task(tid: str):
    return SimpleNamespace(id=tid)


def test_persistence_survives_memory_clear(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "workspace_dir", tmp_path / "ws")
    monkeypatch.setattr(settings, "project_dir", tmp_path / "projects")
    run_store.clear(memory_only=True)
    r = _result("t_persist", "run_persist_1")
    run_store.save(r)

    run_store.clear(memory_only=True)  # 模拟进程重启
    loaded = run_store.get_for_task("t_persist", "run_persist_1")
    assert loaded is not None and loaded.run_id == r.run_id
    assert [x.run_id for x in run_store.list_task("t_persist")] == ["run_persist_1"]


def test_task_scope_isolation(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "workspace_dir", tmp_path / "ws")
    monkeypatch.setattr(settings, "project_dir", tmp_path / "projects")
    run_store.clear(memory_only=True)
    run_store.save(_result("t_a", "run_same_shape"))

    assert run_store.get_for_task("t_b", "run_same_shape") is None
    assert run_store.list_task("t_b") == []


def test_idem_index_is_task_scoped(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "workspace_dir", tmp_path / "ws")
    monkeypatch.setattr(settings, "project_dir", tmp_path / "projects")
    run_store.clear(memory_only=True)
    run_store.save(_result("t_idem", "run_idem_1"))
    run_store.bind_idem("t_idem", "idem-1", "run_idem_1")

    assert run_store.lookup_idem("t_idem", "idem-1") is not None
    assert run_store.lookup_idem("other_task", "idem-1") is None


def test_next_attempt_increments_and_survives(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "workspace_dir", tmp_path / "ws")
    monkeypatch.setattr(settings, "project_dir", tmp_path / "projects")
    run_store.clear(memory_only=True)

    assert run_store.next_attempt("t_attempt", "edit") == 1
    assert run_store.next_attempt("t_attempt", "edit") == 2
    run_store.clear(memory_only=True)
    assert run_store.next_attempt("t_attempt", "edit") == 3
    # 不同任务各自从 1 开始
    assert run_store.next_attempt("t_attempt_b", "edit") == 1


def test_agent_run_replays_after_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "workspace_dir", tmp_path / "ws")
    monkeypatch.setattr(settings, "project_dir", tmp_path / "projects")
    calls = {"n": 0}

    async def fake_stage(task, spec):
        calls["n"] += 1
        from clipwright.schema.stage import make_stage_result

        return make_stage_result(stage=spec.stage, status="ok", summary="ok")

    from clipwright.services import stage_subagent

    monkeypatch.setattr(stage_subagent, "run_stage", fake_stage)
    agent_run.reset()
    spec = RunSpec(kind="stage", stage="structure", idem_key="idem-replay", depth=1)
    first = asyncio.run(agent_run.run(spec, _task("t_replay")))
    second = asyncio.run(agent_run.run(spec, _task("t_replay")))
    assert calls["n"] == 1
    assert first.run_id == second.run_id
    assert second.replayed is True

    agent_run.reset()  # 模拟重启：内存清空，磁盘保留
    third = asyncio.run(agent_run.run(spec, _task("t_replay")))
    assert calls["n"] == 1, "重启后仍应按 idem_key 回放，不重复执行"
    assert third.run_id == first.run_id
