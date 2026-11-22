"""R10：只读复盘访谈。"""

from __future__ import annotations

import asyncio

from clipwright.services import agent_interview


def test_interview_uses_read_only_roles() -> None:
    review = {
        "task_id": "t", "task": {"status": "done"},
        "runs": [{"stage": "material", "role": "material", "status": "ok"}],
        "tools": {"vision_llm": 3}, "blackboard_keys": ["final_timeline"],
        "reports": [],
    }
    seen: list[tuple[str, str]] = []

    async def fake_ask(system: str, user: str) -> str:
        seen.append((system, user))
        return "用得不趁手：vision_llm\\n缺少：batch"

    out = asyncio.run(agent_interview.interview(
        review, roles=["main", "material"], ask=fake_ask,
    ))
    assert out["read_only"] is True
    assert set(out["answers"]) == {"main", "material"}
    assert len(seen) == 2
    assert "不得调用工具" in seen[0][0]
    assert "vision_llm" in seen[0][1]
