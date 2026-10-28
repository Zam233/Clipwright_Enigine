"""B10：M4 评估闭环（closeout plan B10 / PRD §5.5 / §6 E·F）。

四块，各有一条可证伪断言：

1. **replay CLI**：`python -m clipwright.services.replay --bundle …` 自洽返回 0、
   缺证据返回非零——"没证据"不许被当成"没问题"；
2. **prompt/few-shot 候选注册表**：缺 version/change_note/rollback 直接拒绝登记；
3. **离线门禁**：成功率下降永远不放过（不允许"省钱但更容易失败"的暗换）；
4. **≥30 步关键事实保持**：长任务压缩后，早期关键数值仍在上下文里。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from clipwright.services import evaluation, prompt_registry, replay
from clipwright.services import memory


# ── 1. replay CLI ───────────────────────────────────


def _bundle(task_id: str = "t_replay", *, with_end: bool = True,
            with_causation: bool = True) -> dict:
    events = [
        {"seq": 1, "type": "task_start", "message": "开始", "detail": {},
         "trace_id": task_id},
        {"seq": 2, "type": "agent_run_start", "message": "派出 edit",
         "detail": {"run_id": "run_1", "stage": "edit"}, "trace_id": task_id},
        {"seq": 3, "type": "tool_call", "message": "delegate_agent",
         "detail": {"tool": "delegate_agent"}, "trace_id": task_id},
        {"seq": 4, "type": "tool_result", "message": "ok",
         "detail": {"tool": "delegate_agent", "status": "ok"},
         "trace_id": task_id, **({"causation_id": "seq:3"} if with_causation else {})},
        {"seq": 5, "type": "agent_run_end", "message": "edit ok",
         "detail": {"run_id": "run_1", "status": "ok"}, "trace_id": task_id},
    ]
    if with_end:
        events.append({"seq": 6, "type": "task_end", "message": "完成",
                       "detail": {"reason": "tool_loop_completed"}, "trace_id": task_id})
    return {"task_id": task_id, "events": events,
            "runs": [{"run_id": "run_1", "status": "ok"}], "metrics": []}


def test_replay_accepts_self_consistent_bundle():
    result = replay.replay(_bundle())
    assert result["ok"] is True, result["issues"]
    assert result["summary"]["finish_reason"] == "tool_loop_completed"


def test_replay_flags_missing_evidence():
    """没证据 ≠ 没问题：缺 events / 缺 task_end / 工具结果无因果都要报。"""
    empty = replay.replay({"task_id": "t", "events": [], "runs": [], "metrics": []})
    assert empty["ok"] is False
    assert any("events 为空" in i for i in empty["issues"])

    no_end = replay.replay(_bundle(with_end=False))
    assert any("task_end" in i for i in no_end["issues"])

    no_cause = replay.replay(_bundle(with_causation=False))
    assert any("causation" in i for i in no_cause["issues"])


def test_replay_flags_unfinished_agent_run():
    b = _bundle()
    b["events"] = [e for e in b["events"] if e["type"] != "agent_run_end"]
    result = replay.replay(b)
    assert any("没有 end" in i for i in result["issues"])


def test_replay_cli_exit_codes(tmp_path):
    good = tmp_path / "good.json"
    good.write_text(json.dumps(_bundle()), encoding="utf-8")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"task_id": "x", "events": [], "runs": [], "metrics": []}),
                   encoding="utf-8")

    ok = subprocess.run([sys.executable, "-m", "clipwright.services.replay",
                         "--bundle", str(good), "--json"],
                        capture_output=True, text=True, cwd=str(Path.cwd()))
    assert ok.returncode == 0, ok.stderr + ok.stdout
    assert json.loads(ok.stdout)["ok"] is True

    nok = subprocess.run([sys.executable, "-m", "clipwright.services.replay",
                          "--bundle", str(bad)],
                         capture_output=True, text=True, cwd=str(Path.cwd()))
    assert nok.returncode == 1


def test_replay_missing_file_is_loud(tmp_path):
    r = subprocess.run([sys.executable, "-m", "clipwright.services.replay",
                        "--bundle", str(tmp_path / "nope.json")],
                       capture_output=True, text=True, cwd=str(Path.cwd()))
    assert r.returncode == 1
    assert "不存在" in (r.stderr + r.stdout)


# ── 2. 候选注册表 ────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean():
    prompt_registry.reset()
    evaluation.reset()
    memory.reset()
    yield
    prompt_registry.reset()
    evaluation.reset()
    memory.reset()


def test_candidate_requires_full_metadata():
    bad = prompt_registry.Candidate(name="x", version="", change_note="",
                                    applies_to=[], rollback_to="")
    with pytest.raises(ValueError, match="候选不合法"):
        prompt_registry.register(bad)

    good = prompt_registry.Candidate(
        name="edit-prompt", version="v2", change_note="减少跳剪",
        applies_to=["数码评测"], rollback_to="v1")
    pub = prompt_registry.register(good)
    assert pub["status"] == "candidate"
    assert prompt_registry.promote(good.id, status="canary")["status"] == "canary"


# ── 3. 离线门禁 ──────────────────────────────────────


def test_gate_rejects_success_rate_regression():
    base = {"success_rate": 0.9, "cost_avg_usd": 0.10, "avg_turns": 4.0, "replan_rate": 0.1}
    cand = {"success_rate": 0.8, "cost_avg_usd": 0.05, "avg_turns": 3.0, "replan_rate": 0.1}
    out = prompt_registry.gate(base, cand)
    assert out["passed"] is False
    assert any("成功率下降" in f for f in out["failures"])


def test_gate_allows_equal_or_better():
    base = {"success_rate": 0.9, "cost_avg_usd": 0.10, "avg_turns": 4.0, "replan_rate": 0.1}
    cand = {"success_rate": 0.9, "cost_avg_usd": 0.09, "avg_turns": 3.8, "replan_rate": 0.1}
    out = prompt_registry.gate(base, cand)
    assert out["passed"] is True, out["failures"]


def test_gate_rejects_cost_blowup():
    base = {"success_rate": 0.9, "cost_avg_usd": 0.10, "avg_turns": 4.0, "replan_rate": 0.1}
    cand = {"success_rate": 0.9, "cost_avg_usd": 0.30, "avg_turns": 4.0, "replan_rate": 0.1}
    out = prompt_registry.gate(base, cand)
    assert out["passed"] is False
    assert any("cost_avg_usd" in f for f in out["failures"])


def test_three_run_report_reports_spread():
    runs = [
        {"success_rate": 1.0, "avg_turns": 4, "cost_avg_usd": 0.1},
        {"success_rate": 0.0, "avg_turns": 5, "cost_avg_usd": 0.2},
        {"success_rate": 1.0, "avg_turns": 4, "cost_avg_usd": 0.1},
    ]
    report = prompt_registry.three_run_report(runs)
    assert report["runs"] == 3
    assert report["metrics"]["success_rate"]["spread"] == 1.0
    assert report["warnings"], "成功率波动必须被标出来"


# ── 4. 长任务关键事实保持 ────────────────────────────


def test_long_task_key_facts_survive_compaction():
    """>=30 步之后，早期关键事实仍要能被召回（PRD §6 E）。"""
    from clipwright.schema.task import TaskRequest
    from clipwright.services import agent_kernel as ak
    from clipwright.services import context_loader

    memory.remember("关键事实：第 1 段必须用实拍 5 号机位", task_id="task_long",
                    layer="episodic", kind="decision",
                    provenance={"source": "user_chat"})
    facts = memory.recall("关键事实 实拍 机位", task_id="task_long")
    assert facts, "关键事实必须能被召回"

    # ≥30 步工具调用后工作记忆有界，但最近事实仍在
    task = ak.AgentKernel().create(TaskRequest(topic="长任务", plan_only=False))
    for i in range(32):
        ak._record_digest(task, "media_probe",  # noqa: SLF001
                          {"status": "ok", "output": {"step": i, "frames": 300 + i}})
    assert len(task.context_digest) <= ak._MAX_DIGEST + 1
    bundle = ak.context_bundle(task)
    assert "frames=" in bundle
    assert "step=31" in bundle

    ctx = context_loader.load(task)
    assert ctx["text"]

def test_replay_cli_survives_non_utf8_console(tmp_path):
    """Windows 控制台是 GBK 时，报告不能因为符号编码而崩（刚才真崩过）。"""
    import os
    import subprocess
    import sys

    good = tmp_path / "good.json"
    good.write_text(json.dumps(_bundle()), encoding="utf-8")
    env = dict(os.environ, PYTHONIOENCODING="gbk")
    r = subprocess.run([sys.executable, "-m", "clipwright.services.replay",
                        "--bundle", str(good)],
                       capture_output=True, text=True, cwd=str(Path.cwd()), env=env)
    assert r.returncode == 0, r.stdout + r.stderr
