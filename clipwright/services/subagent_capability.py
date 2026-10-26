"""`spawn_subagent` / `get_subagent_status` / `list_subagents` / `cancel_subagent`
能力（路线 C / C5 收口）。

**这一层的唯一职责：把子代理的真实情况如实交回主代理。**
之前只回一句 `status: ok/error` + 截断字符串，主代理无法判断"是它失败了、还是在等我审核、
还是根本没派出去"，于是它只能选择"收尾"——这不是"进一步判断"，是猜。

现在返回统一结构（`spawn` 与 `worker_registry` 提供）：
- `status`：done / failed / needs_human_review / refused / cancelled
- `output`：子代理结论（≤2000 字）
- `failure`：{category, retryable, blocking_point, evidence[], tool_calls}
- `tool_calls`：它到底调了哪些工具、成没成（≤20 条）
- `cost_usd` / `tokens` / `duration_ms`：这次排查花了多少

安全边界（不放宽）：
- 只读工具（read_file/list_dir/list_subagents/get_subagent_status/write_file/web_*）
  可直接派；`write_file` 属 write_local，仍过白名单；
- `run_shell` / `run_python` **不由 Kernel 直接派**——必须走 v1.5 的两级审核门
  （独立 LLM 判风险 + 人工审核），这里明确拒绝并给出正确路径。
"""

from __future__ import annotations

from typing import Any

from clipwright.schema.task import PlanStep, Task

#: Kernel 可直接授予的只读/低危工具（与 supervisor.SUPERVISOR_TOOLS 的交集）
READONLY_TOOLS: frozenset[str] = frozenset({
    # PRD §17.5.2：子 agent **不得**写文件（报告由 runtime 落盘），
    # 所以 write_file 不在这里——调研员只能读与检索。
    "read_file", "list_dir", "list_subagents", "get_subagent_status",
    "web_search", "web_fetch",
})
EXEC_TOOLS: frozenset[str] = frozenset({"run_shell", "run_python"})

_QUERY_CAPS: frozenset[str] = frozenset({
    "get_subagent_status", "list_subagents", "cancel_subagent",
})


def enabled() -> bool:
    from clipwright.config import settings

    return str(getattr(settings, "agent_mode", "off") or "off").lower() == "on"


async def run(task: Task, step: PlanStep) -> dict[str, Any]:
    if not enabled():
        return {
            "status": "refused",
            "error": "agent_mode != on：子代理能力仅在主 Agent 模式下可用",
        }
    if step.capability in _QUERY_CAPS:
        return _query(task, step)
    return await _spawn(task, step)


# ── 查询/取消（让主代理能"再看看"，而不是只能瞎等）────────────────


def _query(task: Task, step: PlanStep) -> dict[str, Any]:
    from clipwright.supervisor.tracker import get_tracker

    tracker = get_tracker(task.id)
    args = dict(step.args or {})

    if step.capability == "list_subagents":
        nodes = tracker.list_nodes()
        return {
            "status": "ok",
            "output": {
                "count": len(nodes),
                "subagents": [
                    {"id": n.id, "role": n.role, "status": n.status,
                     "cost_usd": n.cost_usd, "last_output": (n.last_output or "")[:200]}
                    for n in nodes
                ],
            },
        }

    subagent_id = str(args.get("subagent_id") or "")
    if not subagent_id:
        return {"status": "refused", "error": f"{step.capability} 需要 subagent_id"}

    if step.capability == "get_subagent_status":
        node = tracker.get(subagent_id)
        if node is None:
            return {"status": "error", "error": f"子代理不存在: {subagent_id}",
                    "failure": {"category": "not_found", "retryable": "permanent",
                                "blocking_point": "subagent_id 不存在", "evidence": []}}
        return {
            "status": "ok",
            "output": {
                "id": node.id, "role": node.role, "status": node.status,
                "cost_usd": node.cost_usd, "duration_ms": node.duration_ms,
                "last_output": (node.last_output or "")[:500],
            },
        }

    cancelled = tracker.cancel(subagent_id)
    if not cancelled:
        return {
            "status": "error",
            "error": f"子代理不可取消或已结束: {subagent_id}",
            "failure": {"category": "not_cancellable", "retryable": "permanent",
                        "blocking_point": "状态不允许取消", "evidence": []},
        }
    return {"status": "ok", "output": {"id": subagent_id, "status": "cancelled"}}


# ── 派生 ────────────────────────────────────────────────


async def _spawn(task: Task, step: PlanStep) -> dict[str, Any]:
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
        return {
            "status": "refused",
            "error": f"派生被拒绝: {e}",
            "failure": {"category": "spawn_rejected", "retryable": "needs_human",
                        "blocking_point": str(e)[:200], "evidence": []},
        }
    except Exception as e:  # noqa: BLE001 — 派生失败不影响主任务
        return {"status": "error", "error": f"{type(e).__name__}: {e}",
                "failure": {"category": "unknown", "retryable": "unknown",
                            "blocking_point": str(e)[:200], "evidence": []}}

    raw_status = str(result.get("status") or "")
    common = {
        "subagent_id": result.get("subagent_id"),
        "role": role,
        "cost_usd": result.get("cost_usd"),
        "duration_ms": result.get("duration_ms"),
        "tool_calls": result.get("tool_calls") or [],
        "tokens": result.get("tokens"),
    }

    if raw_status == "pending_review":
        # 不是失败：它在等人审。主代理必须知道"别重试、去催人"
        review = result.get("review") or {}
        return {
            "status": "needs_human_review",
            "approval_id": result.get("worker_id"),
            "risk": review.get("risk"),
            "reasons": review.get("reasons") or [],
            "note": "该子代理带执行权限，等待人工审核；审核通过后会用同一 worker_id 执行",
            **common,
        }

    if raw_status == "done":
        return {
            "status": "ok",
            "output": {
                "subagent_id": common["subagent_id"],
                "role": role,
                "conclusion": str(result.get("output") or "")[:2000],
                "tool_calls": common["tool_calls"],
                "cost_usd": common["cost_usd"],
                "tokens": common["tokens"],
                "duration_ms": common["duration_ms"],
            },
        }

    return {
        "status": "error",
        "error": str(result.get("error") or f"子代理状态 {raw_status}"),
        "failure": result.get("failure") or {
            "category": "unknown", "retryable": "unknown",
            "blocking_point": str(result.get("error") or raw_status)[:200],
            "evidence": [],
        },
        **common,
    }
