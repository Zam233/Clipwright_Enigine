"""B08：进度按提交收据计数；doc_write 真实 bytes；step_end 在提交之后。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from clipwright.config import settings
from clipwright.schema.agent import StructureOutput
from clipwright.schema.stage import StageSpec
from clipwright.services import result_assembler as ra, run_store, stage_subagent
from clipwright.services.workspace import Workspace


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "workspace_dir", tmp_path / "ws")
    monkeypatch.setattr(settings, "project_dir", tmp_path / "projects")
    stage_subagent.reset()
    run_store.clear(memory_only=True)


def _task(task_id: str):
    return SimpleNamespace(
        id=task_id,
        request=SimpleNamespace(topic="x", persona_id="default",
                                category_plugin_id="", extra={}),
    )


def _run(task_id: str, stage: str, status: str, run_id: str):
    return ra.build(run_id=run_id, kind="stage", task_id=task_id, stage=stage,
                    status=status, summary="x")


def test_progress_counts_only_committed_plan_stages(tmp_path):
    run_store.save(_run("t_prog", "structure", "ok", "r1"))
    run_store.save(_run("t_prog", "material", "degraded", "r2"))
    run_store.save(_run("t_prog", "requirements", "ok", "r3"))   # 不在 6 阶段分母
    run_store.save(_run("t_prog", "quality", "failed", "r4"))    # 失败不算完成
    run_store.save(_run("other_task", "edit", "ok", "r5"))       # 别的任务不算

    done, total = stage_subagent._stage_progress_counts("t_prog")
    assert (done, total) == (2, 6)


def test_doc_write_bytes_and_step_end_after_commit(tmp_path, monkeypatch):
    class _FakeAgent:
        async def execute(self, input_obj, context):
            return StructureOutput(scenes=[{"title": "开场", "duration": 10.0}])

    events: list[tuple[str, dict]] = []

    def _capture(task_id, etype, text="", detail=None, **_kw):
        events.append((etype, dict(detail or {})))

    monkeypatch.setattr(stage_subagent, "agent_for", lambda stage: _FakeAgent())
    monkeypatch.setattr(stage_subagent, "emit", _capture)

    ws = Workspace("task_b08")
    ws.ensure(name="b08", auto_index=False, vision=False)
    result = asyncio.run(stage_subagent.run_stage(
        _task("task_b08"), StageSpec(stage="structure", run_id="run_b08")))

    assert result.report_path and result.status == "ok"
    kinds = [k for k, _ in events]
    assert "doc_write" in kinds and "step_end" in kinds
    assert kinds.index("doc_write") < kinds.index("step_end")

    doc = next(d for k, d in events if k == "doc_write")
    assert doc["bytes"] > 0 and doc["run_id"] == "run_b08" and doc["status"] == "ok"

    end = next(d for k, d in events if k == "step_end")
    assert end["report_state"] == "written"
    assert end["run_id"] == "run_b08"
    assert end["input_revision"] >= 0
