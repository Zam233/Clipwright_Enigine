"""exec_store：审批/历史持久化与脱敏。"""

from __future__ import annotations

from types import SimpleNamespace

from clipwright.schema.exec import ExecApproval, ExecHistoryItem, ExecRiskReport
from clipwright.services import exec_store


def _task(tmp_path):
    return SimpleNamespace(id="task_store", request=SimpleNamespace(extra={"workspace_root": str(tmp_path)}))


def test_approval_roundtrip(tmp_path) -> None:
    task = _task(tmp_path)
    approval = ExecApproval(
        approval_id="ap_1", exec_request_id="ex_1", task_id="task_store",
        kind="shell", command="echo hi", cwd=str(tmp_path), risk="high",
        risk_report=ExecRiskReport(risk="high", reasons=["test"]),
        command_sha256="c", risk_sha256="r",
    )
    exec_store.create_approval(task, approval)
    assert exec_store.get_approval(task, "ap_1")["command"] == "echo hi"
    assert exec_store.list_pending(task)[0]["approval_id"] == "ap_1"
    exec_store.update_approval(task, "ap_1", status="executed")
    assert exec_store.list_pending(task) == []


def test_history_redacts_secret(tmp_path) -> None:
    task = _task(tmp_path)
    exec_store.append_history(task, ExecHistoryItem(
        exec_request_id="ex_2", kind="shell", text="echo hi",
        output_tail="API_KEY=supersecret", status="done",
    ))
    rows = exec_store.list_history(task)
    assert rows and "supersecret" not in rows[-1]["output_tail"]
