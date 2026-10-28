"""工作区文档协议（PRD §17）：报告/文档落盘、主 agent 读改、上下文装载。

不碰真实 Agent（会调 LLM）——`agent_for` 用替身；工作区用 tmp_path。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from clipwright.config import settings
from clipwright.schema.stage import StageSpec
from clipwright.schema.task import TaskRequest
from clipwright.services import context_loader, stage_report
from clipwright.services import stage_subagent
from clipwright.services import task_blackboard as bb
from clipwright.services.workspace import Workspace
from clipwright.tool.reports import EditFileTool, ListReportsTool, ReadReportTool


class _Task:
    def __init__(self, task_id: str, root: Path, **kw):
        self.id = task_id
        self.request = TaskRequest(topic=kw.get("topic", "测试选题"),
                                   persona_id="default",
                                   category_plugin_id="knowledge_longform",
                                   plan_only=False,
                                   extra={"workspace_root": str(root), **kw.get("extra", {})})


class _FakeAgent:
    def __init__(self, payload):
        self.payload = payload

    async def execute(self, input_obj, context):
        return self.payload


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "workspace_dir", tmp_path / "workspaces")
    monkeypatch.setattr(settings, "project_dir", tmp_path / "projects")
    monkeypatch.setattr(settings, "agent_mode", "on")
    monkeypatch.setattr(settings, "context_budget_chars", 6000)
    bb.reset()
    stage_subagent.reset()


def _ws(tmp_path, pid="proj_docs") -> Workspace:
    ws = Workspace(pid)
    ws.ensure(name="文档测试", auto_index=False, vision=False)
    return ws


# ── 需求文档（§17.4）────────────────────────────────────────


def test_write_docs_creates_brief_and_plan_with_versions(tmp_path) -> None:
    ws = _ws(tmp_path)
    out = ws.write_docs({"theme": "手机越用越卡"}, {"scenes": [{"title": "开场"}]})
    assert Path(out["brief"]).is_file() and Path(out["plan"]).is_file()
    assert "手机越用越卡" in Path(out["brief"]).read_text(encoding="utf-8")
    assert "开场" in Path(out["plan"]).read_text(encoding="utf-8")

    out2 = ws.write_docs(None, {"scenes": [{"title": "原理"}]})
    assert "plan_prev" in out2                     # 旧版归档
    docs = sorted(p.name for p in (ws.dirs().docs).iterdir())
    assert docs == ["brief.md", "plan-v2.md", "plan.md"]


# ── 报告（§17.3）───────────────────────────────────────────


def test_write_report_is_append_only_and_updates_index(tmp_path) -> None:
    root = tmp_path / "ws"
    sections = stage_report.build_sections({"material_notes": ["场景 4 无匹配"]},
                                           stage="material", status="ok",
                                           summary="候选素材 0 条")
    p1, s1 = stage_report.write_report(workspace_root=root, stage="material", status="ok",
                                       attempt=1, sections=sections, cost_usd=0.01,
                                       duration_ms=100)
    p2, s2 = stage_report.write_report(workspace_root=root, stage="material", status="ok",
                                       attempt=2, sections=sections, cost_usd=0.02,
                                       duration_ms=120)
    assert s1 == 1 and s2 == 2
    assert Path(p1).name == "01-material-1.md" and Path(p2).name == "02-material-2.md"
    assert Path(p1).is_file() and Path(p2).is_file()      # 两份都在（不覆盖）

    index = (root / "reports" / "INDEX.md").read_text(encoding="utf-8")
    assert index.count("| material |") == 2

    items = stage_report.list_reports(root)
    assert [m["seq"] for m in items] == [2, 1]
    assert items[0]["stage"] == "material"


def test_report_has_four_sections(tmp_path) -> None:
    root = tmp_path / "ws"
    sections = stage_report.build_sections({}, stage="edit", status="failed",
                                           summary="轨道 0 条")
    path, _ = stage_report.write_report(workspace_root=root, stage="edit", status="failed",
                                        attempt=1, sections=sections)
    text = Path(path).read_text(encoding="utf-8")
    for title in ("我做了什么", "结果如何", "发现的问题", "给下一位"):
        assert f"## {title}" in text


# ── run_stage 接入报告 ─────────────────────────────────────


def test_run_stage_writes_report_path(tmp_path, monkeypatch) -> None:
    ws = _ws(tmp_path)
    monkeypatch.setattr(stage_subagent, "agent_for",
                        lambda stage: _FakeAgent({"scenes": [{"title": "开场"}], "decision": "pass"}))
    task = _Task("task_docs_1", ws.dirs().root)
    result = asyncio.run(stage_subagent.run_stage(task, StageSpec(stage="structure")))

    assert result.report_seq == 1
    assert result.report_path and Path(result.report_path).is_file()
    text = Path(result.report_path).read_text(encoding="utf-8")
    assert "分镜 1 个" in text                      # 摘要进了报告
    assert "# structure · 第 1 次 · ok" in text


def test_run_stage_marks_degraded_when_report_write_fails(tmp_path, monkeypatch) -> None:
    ws = _ws(tmp_path)
    monkeypatch.setattr(stage_subagent, "agent_for",
                        lambda stage: _FakeAgent({"scenes": [{"title": "开场"}]}))

    def _boom(**kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(stage_report, "write_report", _boom)
    task = _Task("task_docs_2", ws.dirs().root)
    result = asyncio.run(stage_subagent.run_stage(task, StageSpec(stage="structure")))
    assert result.status == "degraded"
    assert result.report_path == ""
    assert any(d.get("kind") == "report_write_failed" for d in result.degradations)


# ── 主 agent 的工具：list_reports / read_report / edit_file ──


def test_list_and_read_report_tools(tmp_path) -> None:
    ws = _ws(tmp_path)
    sections = stage_report.build_sections({}, stage="material", status="ok", summary="候选 21 条")
    path, _ = stage_report.write_report(workspace_root=ws.dirs().root, stage="material",
                                        status="ok", attempt=1, sections=sections)

    listed = asyncio.run(ListReportsTool().execute(project_id=ws.project_id))
    assert str(listed.status).lower().endswith("success")
    assert listed.output["count"] == 1

    read = asyncio.run(ReadReportTool().execute(project_id=ws.project_id, seq=1))
    assert "候选 21 条" in read.output["content"]
    assert read.output["truncated"] is False

    tiny = asyncio.run(ReadReportTool().execute(path=path, max_chars=50))
    assert tiny.output["truncated"] is True
    assert tiny.output["hint"]


def test_edit_file_allows_docs_but_rejects_reports(tmp_path) -> None:
    ws = _ws(tmp_path)
    out = ws.write_docs({"theme": "A"}, {"scenes": [{"title": "s1"}]})

    ok = asyncio.run(EditFileTool().execute(path=out["brief"], old_string="A", new_string="B"))
    assert "B" in Path(out["brief"]).read_text(encoding="utf-8")
    assert ok.output["chars_after"] > 0

    report_path, _ = stage_report.write_report(
        workspace_root=ws.dirs().root, stage="edit", status="ok", attempt=1,
        sections=stage_report.build_sections({}, stage="edit", status="ok", summary="x"))
    blocked = asyncio.run(EditFileTool().execute(path=report_path, old_string="x", new_string="y"))
    assert "append-only" in str(blocked.error)

    missing = asyncio.run(EditFileTool().execute(path=out["plan"], old_string="不存在", new_string="z"))
    assert "未命中" in str(missing.error)

    dup = asyncio.run(EditFileTool().execute(path=out["plan"], old_string="-", new_string="*"))
    assert "必须唯一" in str(dup.error) or "未命中" in str(dup.error)


# ── 上下文装载（§17.6 / §17.11）───────────────────────────


def test_context_loader_includes_docs_reports_and_failure(tmp_path) -> None:
    ws = _ws(tmp_path)
    ws.write_docs({"theme": "手机越用越卡"}, {"scenes": [{"title": "开场"}]})
    stage_report.write_report(
        workspace_root=ws.dirs().root, stage="material", status="ok", attempt=1,
        sections={"我做了什么": "找素材", "结果如何": "候选 0 条",
                  "发现的问题": "素材库为空", "给下一位": "先补素材再剪"})

    task = _Task("task_ctx", ws.dirs().root)
    out = context_loader.load(task, failure={"category": "input_insufficient",
                                             "retryable": "needs_upstream",
                                             "blocking_point": "缺 candidate_clips",
                                             "needs": [{"field": "candidate_clips",
                                                        "from_stage": "material"}]})
    text = out["text"]
    assert "手机越用越卡" in text          # brief
    assert "开场" in text                  # plan 场景
    assert "先补素材再剪" in text          # 报告「给下一位」
    assert "input_insufficient" in text    # failure
    assert out["bytes"] > 0


def test_context_loader_trims_when_budget_tiny(tmp_path, monkeypatch) -> None:
    ws = _ws(tmp_path)
    ws.write_docs({"theme": "很长" * 300}, {"scenes": [{"title": "开场"}]})
    monkeypatch.setattr(settings, "context_budget_chars", 260)
    out = context_loader.load(_Task("task_ctx2", ws.dirs().root))
    assert out["trimmed"]                       # 有段被裁剪
    assert out["bytes"] <= 400                  # 没有失控


# ── Kernel 的观察/意图（§16.10.2）────────────────────────


def test_brief_prefers_summary_and_digest_keeps_facts() -> None:
    """摘要优先的口径从 Kernel 搬到了 tool loop（`_brief_of`），不变量不变：

    工具结果里有 `summary` 时就用它，而不是把整个 output dump 给模型；
    结构化事实（数值/短路径）必须进 context_digest，压缩后仍在。
    """
    from clipwright.schema.task import Task
    from clipwright.services.agent_loop import _brief_of
    from clipwright.services.agent_kernel import _record_digest

    outcome = {"status": "ok",
               "output": {"summary": "候选素材 20 条", "frames": 300,
                          "writes": ["candidate_clips"]},
               "cost_usd": 0.01}
    assert _brief_of(outcome) == "候选素材 20 条"

    task = Task(id="t_obs", request=TaskRequest(topic="x", plan_only=False))
    _record_digest(task, "delegate_agent", outcome)
    entry = task.context_digest[-1]
    assert entry["step_id"] == "delegate_agent"
    assert entry["facts"]["frames"] == 300
    assert entry["facts"]["summary"] == "候选素材 20 条"


def test_intent_text_prefers_say() -> None:
    from clipwright.schema.task import PlanStep
    from clipwright.services.agent_kernel import _intent_of

    step = PlanStep(id="p1", capability="delegate", title="找素材", why="因为要剪",
                    args={"stage": "material", "say": "我先看看素材库里有什么"})
    assert _intent_of(step) == "我先看看素材库里有什么"
    step2 = PlanStep(id="p2", capability="delegate", title="找素材", why="因为要剪",
                     args={"stage": "material"})
    assert _intent_of(step2) == "因为要剪"


# ── reports / docs API（§19.6 docs 标签数据源）────────────


def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from clipwright.api import workspace as workspace_api

    app = FastAPI()
    app.include_router(workspace_api.router)
    return TestClient(app)


def test_reports_api_lists_and_reads(tmp_path) -> None:
    ws = _ws(tmp_path, "proj_api_reports")
    sections = stage_report.build_sections({}, stage="material", status="ok", summary="候选 21 条")
    stage_report.write_report(workspace_root=ws.dirs().root, stage="material",
                              status="ok", attempt=1, sections=sections)

    client = _client()
    listed = client.get("/api/workspace/proj_api_reports/reports")
    assert listed.status_code == 200
    body = listed.json()
    assert body["count"] == 1 and body["reports"][0]["stage"] == "material"

    read = client.get("/api/workspace/proj_api_reports/reports/1")
    assert read.status_code == 200
    assert "候选 21 条" in read.json()["content"]

    missing = client.get("/api/workspace/proj_api_reports/reports/999")
    assert missing.status_code == 404


def test_docs_api_returns_brief_and_plan(tmp_path) -> None:
    ws = _ws(tmp_path, "proj_api_docs")
    ws.write_docs({"theme": "手机越用越卡"}, {"scenes": [{"title": "开场"}]})
    client = _client()
    res = client.get("/api/workspace/proj_api_docs/docs")
    assert res.status_code == 200
    docs = res.json()["docs"]
    names = {d["name"] for d in docs}
    assert {"brief.md", "plan.md"} <= names
    brief = next(d for d in docs if d["name"] == "brief.md")
    assert "手机越用越卡" in brief["content"]


# ── 能力名：delegate 新名 + run_stage 旧名兼容 ─────────────


def test_stage_dispatch_goes_through_agent_run(tmp_path, monkeypatch) -> None:
    """阶段派发的唯一入口是 `agent_run`（旧能力名适配层已在 closeout B11 退役）。

    这条用例以前测"run_stage 旧名会被映射"，现在测**真实链路**：
    `surface.execute("delegate_agent")` → `agent_run.run` → `stage_subagent.run_stage`。
    """
    from clipwright.services import agent_run
    from clipwright.tool import surface as sf

    ws = _ws(tmp_path, "proj_legacy_name")
    task = _Task("task_legacy", ws.dirs().root)
    monkeypatch.setattr(stage_subagent, "agent_for",
                        lambda stage: _FakeAgent({"scenes": [{"title": "s"}]}))
    out = asyncio.run(sf.execute("delegate_agent", {"stage": "structure"},
                                 pipeline_id="task_legacy", task=task))
    assert out["status"] == "ok"
    assert out["output"]["stage"] == "structure"
    assert agent_run.get_run(out["output"]["run_id"]) is not None


