"""Exec bridge：cwd/审批/持久化基础行为。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from clipwright.schema.task import PlanStep
from clipwright.services import exec_capability


def _task(tmp_path):
    return SimpleNamespace(
        id="task_exec_test",
        request=SimpleNamespace(extra={"workspace_root": str(tmp_path)}, topic="t"),
        pending_exec_approval_id="",
    )


def test_disabled_refuses(tmp_path, monkeypatch) -> None:
    from clipwright.config import settings

    monkeypatch.setattr(settings, "agent_mode", "off", raising=False)
    out = asyncio.run(exec_capability.run(_task(tmp_path), PlanStep(
        id="s", capability="run_shell", args={"command": "echo hi"},
    )))
    assert out["status"] == "refused"


def test_pending_approval_is_persisted(tmp_path, monkeypatch) -> None:
    from clipwright.config import settings
    from clipwright.services import exec_store

    monkeypatch.setattr(settings, "agent_mode", "on", raising=False)
    monkeypatch.setattr(settings, "kernel_exec_enabled", True, raising=False)
    fake = AsyncMock(return_value={
        "status": "pending_approval",
        "approval": {
            "id": "ap_test", "riskLevel": "high", "riskReasons": ["测试"],
            "cwd": str(tmp_path), "command": "echo hi",
        },
    })
    with patch("clipwright.supervisor.gateway.run_command", fake):
        out = asyncio.run(exec_capability.run(_task(tmp_path), PlanStep(
            id="s", capability="run_shell", args={"command": "echo hi"},
        )))
    assert out["status"] == "pending_approval"
    assert out["approval_id"] == "ap_test"
    assert exec_store.get_approval(_task(tmp_path), "ap_test") is not None


def test_cwd_outside_refused(tmp_path, monkeypatch) -> None:
    from clipwright.config import settings

    monkeypatch.setattr(settings, "agent_mode", "on", raising=False)
    monkeypatch.setattr(settings, "kernel_exec_enabled", True, raising=False)
    out = asyncio.run(exec_capability.run(_task(tmp_path), PlanStep(
        id="s", capability="run_shell",
        args={"command": "echo hi", "cwd": str(tmp_path.parent)},
    )))
    assert out["status"] == "refused"
    assert out["code"] == "cwd_outside_workspace"
