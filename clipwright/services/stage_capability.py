"""`delegate` / `get_stage` / `list_stages` / `wait_for_stage` 能力。

**薄适配层**（modern-agent PRD §5.3 M2）：这里不再解释"能力名 → 该干什么"，
只做三件事——参数解析、`RunSpec` 组装、`AgentResult` 转工具结果。真正的执行、
重试、取消、超时、报告、产物、成本全部在 `services.agent_run`（阶段与调研共用）。

历史上这里是 if/elif 的能力解释器（`_DISPATCH_CAPS` / `_QUERY_CAPS` /
`CONTROL_ARGS` 三张表逐项判断），那正是"agent 外衣的编排管线"留下的痕迹：
模型说一句派活，代码要再翻译一遍。现在只有一条路。
"""

from __future__ import annotations

from typing import Any

from clipwright.schema.task import PlanStep, Task

#: 这些 args 是「派发控制参数」，不是阶段 Agent 的业务输入
CONTROL_ARGS: frozenset[str] = frozenset({
    "stage", "provider", "model", "timeout_sec", "max_turns",
    "dry_run", "merge", "idem_key", "inputs", "expect",
})

_QUERY_CAPS: frozenset[str] = frozenset({"get_stage", "list_stages", "wait_for_stage"})
#: 阶段派发能力名（`run_stage` 为旧名，仅为兼容历史检查点保留）
_DISPATCH_CAPS: frozenset[str] = frozenset({"delegate", "run_stage"})


def enabled() -> bool:
    from clipwright.config import settings

    return str(getattr(settings, "agent_mode", "off") or "off").lower() == "on"


async def run(task: Task, step: PlanStep) -> dict[str, Any]:
    """Kernel 执行器入口（plan adapter 与工具面共用）。"""
    if not enabled():
        return {
            "status": "refused",
            "error": "agent_mode != on：阶段子代理仅在主 Agent 模式下可用",
            "failure": {
                "category": "policy_refused",
                "retryable": "needs_human",
                "blocking_point": "agent_mode != on",
                "evidence": [],
            },
        }
    if step.capability in _QUERY_CAPS:
        return _query(task, step)
    if step.capability not in _DISPATCH_CAPS:
        return {
            "status": "refused",
            "error": f"未知能力：{step.capability}（派发请用 delegate_agent）",
        }
    return await _delegate(task, step)


def _query(task: Task, step: PlanStep) -> dict[str, Any]:
    from clipwright.services import stage_subagent, task_blackboard as bb

    args = dict(step.args or {})
    if step.capability == "list_stages":
        items = stage_subagent.list_stages(task.id)
        return {
            "status": "ok",
            "output": {
                "count": len(items),
                "stages": [
                    {"stage": r.get("stage"), "status": r.get("status"),
                     "summary": r.get("summary"), "writes": r.get("writes"),
                     "cost_usd": r.get("cost_usd")}
                    for r in items
                ],
                "blackboard": bb.describe(task.id),
            },
        }

    stage = str(args.get("stage") or "")
    if not stage:
        return {"status": "refused", "error": f"{step.capability} 需要 stage（如 「edit」）"}
    result = stage_subagent.get_stage(task.id, stage)
    if result is None:
        return {
            "status": "error",
            "error": f"该任务还没有派过阶段 {stage}",
            "failure": {"category": "not_found", "retryable": "needs_upstream",
                        "blocking_point": f"未派出 {stage}", "evidence": []},
        }
    if step.capability == "wait_for_stage":
        return {"status": "ok",
                "output": {**result, "waited": True,
                           "converged": str(result.get("status")) not in ("pending", "running")}}
    return {"status": "ok", "output": result}


def spec_of(step: PlanStep) -> Any:
    """把 PlanStep 翻成统一 `RunSpec`（唯一装配点）。"""
    from clipwright.schema.run import RunSpec

    args = dict(step.args or {})
    return RunSpec(
        kind="stage",
        stage=str(args.get("stage") or "").strip(),
        brief=str(args.get("brief") or ""),
        inputs=dict(args.get("inputs") or {}),
        expect=dict(args.get("expect") or {}),
        provider=str(args.get("provider") or ""),
        model=str(args.get("model") or ""),
        timeout_sec=float(args.get("timeout_sec") or 900.0),
        max_turns=int(args.get("max_turns") or 8),
        depth=1,
        idem_key=str(args.get("idem_key") or ""),
    )


async def _delegate(task: Task, step: PlanStep) -> dict[str, Any]:
    from clipwright.schema.stage import STAGE_NAMES
    from clipwright.services import agent_run

    spec = spec_of(step)
    if not spec.stage:
        return {
            "status": "refused",
            "error": "派阶段必须给 stage（structure/material/edit/animation/audio/quality）",
            "failure": {"category": "input_insufficient", "retryable": "needs_human",
                        "blocking_point": "缺少 stage 参数", "evidence": []},
        }
    if spec.stage not in STAGE_NAMES:
        return {
            "status": "refused",
            "error": f"未知阶段 {spec.stage}（可选：{'/'.join(STAGE_NAMES)}）",
            "failure": {"category": "input_insufficient", "retryable": "needs_human",
                        "blocking_point": f"stage={spec.stage}", "evidence": []},
        }
    if not spec.trace_id:
        spec.trace_id = task.id

    result = await agent_run.run(spec, task)
    payload = result.to_tool_payload()
    status = "ok" if result.status in ("ok", "degraded", "needs") else "error"
    return {
        "status": status,
        "output": {
            **payload,
            # 兼容历史字段：主 Agent 的既有提示词与前端仍读这几个键
            "summary": result.summary,
            "writes": result.writes,
            "artifacts": result.outputs,
            "report_path": result.report_path,
            "report_seq": result.report_seq,
            "needs": result.needs,
            "issues": result.issues,
            "iterations": result.iterations,
            "cost_usd": result.cost_usd,
            "cost_unknown": result.cost_unknown,
        },
        "error": result.error or None,
        "failure": result.failure,
        "cost_usd": result.cost_usd,
    }


__all__ = ["CONTROL_ARGS", "enabled", "run", "spec_of"]
