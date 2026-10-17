"""子代理派生（PRD F10 / 开发计划 B8）。

四条硬约束（每条都有对应验收）：

1. **不得再派生**：星型拓扑，子代理内调用直接拒绝（A19）；
2. **工具子集**：worker 的工具只能是 `SUPERVISOR_TOOLS` 的子集（不越权）；
3. **执行权限两级门**：带 shell/python 的 worker 先由独立 LLM 判风险，再人工审核，
   **未过门不得实例化**（A22/A23）；
4. **不写 AgentRegistry**：临时 worker 只活在本次管线，管线结束回收（A17）。
"""

from __future__ import annotations

from typing import Any, Optional

from clipwright.config import logger
from clipwright.supervisor import review
from clipwright.supervisor.tracker import get_tracker
from clipwright.supervisor.worker_registry import (
    EXEC_TOOLS,
    SUPERVISOR_TOOLS,
    WorkerSpec,
    find_worker,
    get_workers,
)


class SpawnError(ValueError):
    """派生被拒绝（参数/拓扑/权限/预算）。"""


def _validate_tools(tools: list[str]) -> tuple[str, ...]:
    cleaned = tuple(dict.fromkeys(str(t).strip() for t in (tools or []) if str(t).strip()))
    unknown = [t for t in cleaned if t not in SUPERVISOR_TOOLS]
    if unknown:
        raise SpawnError(f"worker 工具越权: {unknown}（只能是主代理工具的子集）")
    return cleaned


async def spawn_subagent(
    context: Any,
    *,
    role: str,
    system_prompt: str = "",
    tools: Optional[list[str]] = None,
    provider: str = "",
    model: str = "",
    timeout_sec: float = 60.0,
    max_turns: int = 5,
) -> dict[str, Any]:
    """派生一个子代理。带执行权限时返回 `pending_review`，不实例化。"""
    pid = str(getattr(context, "pipeline_id", "") or "")
    if not pid:
        raise SpawnError("缺少 pipeline_id")
    if not str(role or "").strip():
        raise SpawnError("role 不能为空")

    # 1) 拓扑：子代理不得再派生
    if (getattr(context, "extra_params", {}) or {}).get("host_agent"):
        raise SpawnError("子代理不得再派生（星型拓扑，深度固定 1 层）")

    # 2) 工具子集
    spec = WorkerSpec(
        role=str(role).strip()[:80],
        system_prompt=str(system_prompt or "")[:4000],
        tools=_validate_tools(list(tools or [])),
        provider=str(provider or ""),
        model=str(model or ""),
        timeout_sec=float(timeout_sec or 60.0),
        max_turns=int(max_turns or 5),
    )

    # 3) 预算闸（未配置预算恒不阻断）
    try:
        from clipwright.services.budget_guard import is_blocked

        if is_blocked(pid):
            raise SpawnError("预算已耗尽，拒绝派生新子代理")
    except SpawnError:
        raise
    except Exception:  # noqa: BLE001
        pass

    # 4) 登记（tracker 负责观测，registry 负责规格与审核状态）
    tracker = get_tracker(pid)
    node = tracker.register(
        spec.role,
        kind=spec.kind,
        provider=spec.provider,
        model=spec.model,
        cancellable=True,
    )
    entry = get_workers(pid).register(spec)
    entry.subagent_id = node.id

    # 5) 执行权限 → 两级门的第一级（独立 LLM）
    if spec.needs_exec_review:
        assessment = await review.assess({
            "role": spec.role,
            "system_prompt": spec.system_prompt,
            "tools": list(spec.tools),
            "timeout_sec": spec.timeout_sec,
            "max_turns": spec.max_turns,
        })
        entry.review = assessment
        get_workers(pid).mark(entry.id, "pending_review", review=assessment)
        tracker.update(node.id, status="running", last_output="等待审核")
        return {
            "worker_id": entry.id,
            "subagent_id": node.id,
            "status": "pending_review",
            "review": assessment,
            "needs_human_review": True,
        }

    # 6) 无执行权限 → 直接实例化并执行
    return await _run_worker(pid, entry.id)


async def review_worker(
    worker_id: str, *, approve: bool, confirm_high_risk: bool = False,
) -> dict[str, Any]:
    """两级门的第二级：人工审核。"""
    entry = find_worker(worker_id)
    if entry is None:
        raise KeyError(f"worker 不存在: {worker_id}")
    if entry.status not in ("pending_review", "draft"):
        raise SpawnError(f"worker 当前状态不可审核: {entry.status}")

    pid = entry.pipeline_id
    reg = get_workers(pid)

    if not approve:
        reg.mark(worker_id, "rejected")
        get_tracker(pid).cancel(entry.subagent_id)
        return {"worker_id": worker_id, "status": "rejected"}

    if review.requires_confirm(entry.review) and not confirm_high_risk:
        raise SpawnError("风险等级为 high/critical，需显式二次确认（confirm_high_risk=true）")

    reg.mark(worker_id, "approved")
    return await _run_worker(pid, worker_id)


async def _run_worker(pid: str, worker_id: str) -> dict[str, Any]:
    """实例化并执行临时 worker（不写 AgentRegistry）。"""
    entry = find_worker(worker_id)
    if entry is None:
        raise KeyError(f"worker 不存在: {worker_id}")
    reg = get_workers(pid)
    tracker = get_tracker(pid)
    spec = entry.spec
    node_id = entry.subagent_id

    reg.mark(worker_id, "running")
    if node_id:
        tracker.update(node_id, status="running", last_output="执行中")

    prompt = (
        f"你的角色：{spec.role}\n"
        f"{spec.system_prompt}\n\n"
        "请完成本次任务并输出结论。只输出结论正文，不要寒暄。"
    )
    try:
        from clipwright.services.llm import LLMService
        from clipwright.services.model_router import ModelRoute, use_route

        route = (
            ModelRoute(provider=spec.provider, model=spec.model)
            if (spec.provider or spec.model) else None
        )
        svc = LLMService()
        with use_route(route):
            resp = await svc.ask(prompt)
        text = str(getattr(resp, "content", "") or "")
        if not getattr(resp, "success", True):
            raise RuntimeError(f"worker LLM 调用失败: {getattr(resp, 'status_code', '?')}")
        reg.mark(worker_id, "done")
        if node_id:
            tracker.finish(node_id, status="done", last_output=text[:500])
        return {
            "worker_id": worker_id,
            "subagent_id": node_id,
            "status": "done",
            "output": text[:2000],
        }
    except Exception as e:  # noqa: BLE001 — worker 失败不影响管线
        logger.warning("临时 worker 执行失败 %s: %s", worker_id, e)
        reg.mark(worker_id, "failed")
        if node_id:
            tracker.finish(node_id, status="failed", last_output=str(e)[:200])
        return {
            "worker_id": worker_id,
            "subagent_id": node_id,
            "status": "failed",
            "error": str(e)[:300],
        }


def worker_tool_matrix() -> dict[str, list[str]]:
    """运维/前端用：当前允许的工具与「需审核」的工具。"""
    return {
        "allowed": sorted(SUPERVISOR_TOOLS),
        "exec_requires_review": sorted(EXEC_TOOLS),
    }
