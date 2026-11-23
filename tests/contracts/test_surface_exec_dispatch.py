"""surface.execute 把 run_shell/run_python 交给 Kernel Exec。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from clipwright.config import settings
from clipwright.tool import surface


def _task(tmp_path):
    return SimpleNamespace(
        id="task_surface_exec",
        request=SimpleNamespace(extra={"workspace_root": str(tmp_path)}, topic="t"),
        pending_exec_approval_id="",
    )


def test_surface_dispatches_exec(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "kernel_exec_enabled", True, raising=False)
    monkeypatch.setattr(settings, "agent_mode", "on", raising=False)
    fake = AsyncMock(return_value={"status": "ok", "output": {"exit_code": 0}})
    with patch("clipwright.services.exec_capability.run", fake):
        out = asyncio.run(surface.execute(
            "run_shell", {"command": "echo hi"}, task=_task(tmp_path),
        ))
    assert out["status"] == "ok"
    assert fake.await_count == 1
