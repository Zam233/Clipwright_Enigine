"""`spawn_subagent` 能力（路线 C / C5）：把"派个人去看看"变成 Kernel 的一步。

沿用 v1.5 的子代理语义（星型拓扑、并发上限、开销单列），但**限制它的工具面**：

- 只读档（`read_file`/`list_dir`/`list_subagents`/`get_subagent_status`）可直接派；
- 带执行权限（`run_shell`/`run_python`）**不由 Kernel 直接派**——那必须走 v1.5 的
  两级审核门（独立 LLM 判风险 + 人工审核），所以这里明确拒绝并提示正确路径。

这条限制是 C5 里最容易做错的地方：C 让 agent 有了自主性，但"自主派一个有 shell 的
子代理"等于绕过整条授权链。所以宁可少给能力，也不给一条暗门。
"""

from __future__ import annotations

from typing import Any

from clipwright.schema.task import PlanStep, Task

#: Kernel 可直接授予的只读工具（与 supervisor.SUPERVISOR_TOOLS 的交集）
READONLY_TOOLS: frozenset[str] = frozenset({
    "read_file", "list_dir", "list_subagents", "get_subagent_status",
})
EXEC_TOOLS: frozenset[str] = frozenset({"run_shell", "run_python"})


def enabled() -> bool:
    from clipwright.config import settings

    return str(getattr(settings, "agent_mode", "off") or "off").lower() == "on"


async def run(task: Task, step: PlanStep) -> dict[str, Any]:
    if not enabled():
        return {
            "status": "refused",
            "error": "agent_mode != on：子代理能力仅在主 Agent 模式下可用",
        }

    args = dict(step.args or {})
    role = str(args.get("role") or "").strip()
    if not role:
        return {"status": "refused", "error": "派子代理必须给 role（例如「素材可用性核查员」）"}

    requested = {str(t) for t in (args.get("tools") or ["read_file", "list_dir"])}
    forbidden = requested & EXEC_TOOLS
    if forbidden:
        return {
            "status": "refused",
            "error": (
                f"子代理不能由 Kernel 直接授予执行权限（{sorted(forbidden)}）："
                "带 shell/python 的子代理必须经 /api/pipeline/{pid}/subagents 派发，"
                "由独立风险判定 + 人工审核把关"
            ),
        }
    tools = requested & READONLY_TOOLS
    if not tools:
        return {"status": "refused", "error": "子代理工具集必须是非空的只读子集"}

    from types import SimpleNamespace

    from clipwright.supervisor.spawn import SpawnError, spawn_subagent

    ctx = SimpleNamespace(pipeline_id=task.id, extra_params={})
    try:
        result = await spawn_subagent(
            ctx,
            role=role,
            system_prompt=str(args.get("system_prompt") or "")[:4000],
            tools=sorted(tools),
            provider=str(args.get("provider") or ""),
            model=str(args.get("model") or ""),
            timeout_sec=float(args.get("timeout_sec") or 45.0),
            max_turns=int(args.get("max_turns") or 4),
        )
    except SpawnError as e:
        return {"status": "refused", "error": f"派生被拒绝: {e}"}
    except Exception as e:  # noqa: BLE001 — 子代理失败不影响主任务
        return {"status": "error", "error": f"{type(e).__name__}: {e}"}

    return {
        "status": "ok" if result.get("status") == "done" else "error",
        "output": {
            "subagent_id": result.get("subagent_id"),
            "role": role,
            "cost_usd": result.get("cost_usd"),
            "duration_ms": result.get("duration_ms"),
            "conclusion": str(result.get("output") or "")[:500],
        },
        "error": result.get("error"),
    }
