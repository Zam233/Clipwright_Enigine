"""停止后的“继续”应走续跑，而不是规划书修订。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from clipwright.services.requirements_service import RequirementsService


@pytest.mark.asyncio
async def test_plan_ready_after_user_stop_continues(monkeypatch) -> None:
    svc = object.__new__(RequirementsService)
    session = {
        "status": "plan_ready",
        "messages": [
            {"role": "assistant", "content": "已停止",
             "metadata": {"user_stopped": True, "task_id": "task_old"}},
            {"role": "user", "content": "继续"},
        ],
        "production_plan": {"title": "x", "raw_scenes": [1]},
        "creative_brief": {"title": "x"},
    }
    called: dict[str, str] = {}

    async def fake_continue(session_id, session, text, **kwargs):
        called["text"] = text
        return {"task_id": "task_new", "note": "收到"}

    monkeypatch.setattr(svc, "_live_task_id", lambda _sid: "")
    monkeypatch.setattr(svc, "_continue_as_task", fake_continue)

    result = await svc._maybe_continue_as_task(
        "req_x", session, "继续", status="plan_ready",
    )
    assert result == {"task_id": "task_new", "note": "收到"}
    assert called["text"] == "继续"
