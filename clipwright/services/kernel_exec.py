"""Kernel Exec Step 门面（PRD WP2）。"""

from __future__ import annotations

from typing import Any

from clipwright.schema.task import PlanStep


async def run(task: Any, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    if name not in ("run_shell", "run_python"):
        return {"status": "refused", "error": f"不是 Exec 能力: {name}"}
    if task is None:
        return {"status": "refused", "error": "Exec 需要任务上下文"}
    from clipwright.services import exec_capability

    step = PlanStep(
        id=f"exec:{name}", title=name, capability=name,
        kind="meta", args=dict(args or {}),
    )
    return await exec_capability.run(task, step)
