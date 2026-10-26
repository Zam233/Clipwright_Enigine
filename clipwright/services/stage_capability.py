"""`run_stage` / `get_stage` / `list_stages` 能力（PRD §3.6）。

与 `subagent_capability`（只读调研员）的区别：这里派的是**阶段子代理**——
它有结构化输入、会写产物、结果合并进黑板。它是"移除 6 阶段管线"之后主 Agent
唯一的干活通道，所以返回体必须让主 Agent 能直接决策：
`status / output.summary / output.writes / failure.category`。
"""

from __future__ import annotations

from typing import Any

from clipwright.schema.stage import STAGE_NAMES, StageSpec
from clipwright.schema.task import PlanStep, Task

#: 这些 args 是「派发控制参数」，不是阶段 Agent 的业务输入
CONTROL_ARGS: frozenset[str] = frozenset({
    "stage", "provider", "model", "timeout_sec", "max_turns",
    "dry_run", "merge", "idem_key", "inputs",
})

_QUERY_CAPS: frozenset[str] = frozenset({"get_stage", "list_stages"})
#: 阶段派发能力名（`run_stage` 为旧名，仅为兼容历史检查点保留）
_DISPATCH_CAPS: frozenset[str] = frozenset({"delegate", "run_stage"})


def enabled() -> bool:
    from clipwright.config import settings

    return str(getattr(settings, "agent_mode", "off") or "off").lower() == "on"


async def run(task: Task, step: PlanStep) -> dict[str, Any]:
    """Kernel 执行器入口。"""
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
            "error": f"未知能力：{step.capability}（派发请用 delegate）",
        }
    return await _run_stage(task, step)


def _query(task: Task, step: PlanStep) -> dict[str, Any]:
    from clipwright.services import stage_subagent

    args = dict(step.args or {})
    if step.capability == "list_stages":
        items = stage_subagent.list_stages(task.id)
        from clipwright.services import task_blackboard as bb

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
        return {"status": "refused", "error": "get_stage 需要 stage（如 「edit」）"}
    result = stage_subagent.get_stage(task.id, stage)
    if result is None:
        return {
            "status": "error",
            "error": f"该任务还没有派过阶段 {stage}",
            "failure": {"category": "not_found", "retryable": "needs_upstream",
                        "blocking_point": f"未派出 {stage}", "evidence": []},
        }
    return {"status": "ok", "output": result}


async def _run_stage(task: Task, step: PlanStep) -> dict[str, Any]:
    from clipwright.services import stage_subagent

    args = dict(step.args or {})
    stage = str(args.get("stage") or "").strip()
    if not stage:
        return {
            "status": "refused",
            "error": "派阶段必须给 stage（structure/material/edit/animation/audio/quality）",
            "failure": {"category": "input_insufficient", "retryable": "needs_human",
                        "blocking_point": "缺少 stage 参数", "evidence": []},
        }
    if stage not in STAGE_NAMES:
        return {
            "status": "refused",
            "error": f"未知阶段 {stage}（可选：{'/'.join(STAGE_NAMES)}）",
            "failure": {"category": "input_insufficient", "retryable": "needs_human",
                        "blocking_point": f"stage={stage}", "evidence": []},
        }

    payload_args = {k: v for k, v in args.items() if k not in CONTROL_ARGS}
    try:
        spec = StageSpec(
            stage=stage,  # type: ignore[arg-type]
            args=payload_args,
            # 显式输入（S2）：给了就不读黑板；`{"artifact": "art_x"}` 会被运行时解析
            inputs=dict(args.get("inputs") or {}),
            provider=str(args.get("provider") or ""),
            model=str(args.get("model") or ""),
            timeout_sec=float(args.get("timeout_sec") or StageSpec.model_fields["timeout_sec"].default),
            max_turns=int(args.get("max_turns") or StageSpec.model_fields["max_turns"].default),
            dry_run=bool(args.get("dry_run") or False),
            merge=bool(args.get("merge", True)),
            idem_key=str(args.get("idem_key") or ""),
        )
    except Exception as exc:  # noqa: BLE001 — 规格单非法要如实拒绝
        return {
            "status": "refused",
            "error": f"阶段规格非法：{exc}",
            "failure": {"category": "input_insufficient", "retryable": "needs_human",
                        "blocking_point": str(exc)[:200], "evidence": []},
        }

    return await stage_subagent.run(task, spec)


__all__ = ["CONTROL_ARGS", "enabled", "run"]
