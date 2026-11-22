"""唯一阶段领域 Adapter（Coding Plan B03）。

职责边界：
- **Adapter（本模块）**：RunSpec → 输入装配 → 领域执行 → `AgentResult`（唯一公开结果）；
- **领域执行（stage_subagent._execute_stage）**：跑 Agent、落报告/产物/黑板，返回 `StageResult`；
- **`stage_subagent.run_stage`**：仅为过渡保留的窄转发，不承载转换逻辑。

`_map_status` 的 `dry_run → ok` 折叠只存在于已删除的旧 `stage_subagent.run` dict 包装里；
本模块直接把 `StageResult.status` 映射到 v2 终态，`dry_run` 原样保留。
"""

from __future__ import annotations

from typing import Any

from clipwright.schema.run import AgentResult, RunSpec
from clipwright.services import result_assembler as _ra
from clipwright.services import stage_subagent

#: 领域执行状态 → v2 终态（1:1，不再有 dry_run→ok）
_STATUS_MAP: dict[str, str] = {
    "ok": "ok",
    "degraded": "degraded",
    "failed": "failed",
    "refused": "refused",
    "cancelled": "cancelled",
    "dry_run": "dry_run",
}


def map_status(stage_status: str) -> str:
    return _STATUS_MAP.get(str(stage_status), "failed")


async def run_stage(spec: RunSpec, task: Any, run_id: str, trace_id: str) -> AgentResult:
    """唯一阶段 Adapter：RunSpec → AgentResult（早退也走同一个 assembler）。"""
    from clipwright.schema.stage import STAGE_NAMES, StageSpec

    task_id = str(getattr(task, "id", "") or "")
    if not spec.stage:
        return _ra.build(
            run_id=run_id, kind="stage", task_id=task_id, trace_id=trace_id,
            status="refused",
            error="派阶段必须给 stage（requirements/structure/material/edit/animation/audio/quality）",
            failure={"category": "input_insufficient", "retryable": "needs_human",
                     "blocking_point": "缺少 stage 参数", "evidence": []},
        )
    if str(spec.stage) not in STAGE_NAMES:
        return _ra.build(
            run_id=run_id, kind="stage", task_id=task_id, trace_id=trace_id,
            stage=str(spec.stage), status="refused",
            error=f"未知阶段 {spec.stage}（可选：{'/'.join(STAGE_NAMES)}）",
            failure={"category": "input_insufficient", "retryable": "needs_human",
                     "blocking_point": f"stage={spec.stage}", "evidence": []},
        )

    stage_spec = StageSpec(
        stage=spec.stage,  # type: ignore[arg-type]
        args={"brief": spec.brief} if spec.brief else {},
        inputs=dict(spec.inputs or {}),
        provider=spec.provider,
        model=spec.model,
        timeout_sec=float(spec.timeout_sec or 900.0),
        max_turns=int(spec.max_turns or 8),
        idem_key=spec.idem_key,
        run_id=run_id,
        trace_id=trace_id,
    )
    sr = await stage_subagent.run_stage(task, stage_spec)
    # B21b：领域执行已直接产出 AgentResult（唯一铁厂 = result_assembler）。
    # 身份（run_id/trace_id/task_id/mode）由 Adapter 统一盖章，领域层不猜。
    update = {"run_id": run_id, "trace_id": trace_id, "task_id": task_id,
              "kind": "stage"}
    if getattr(spec, "mode", ""):
        update["mode"] = spec.mode
    return sr.model_copy(update=update)


async def run_inline(stage: str, input_obj: Any, context: Any, *,
                     timeout_sec: float | None = None,
                     run_id: str = "", task_id: str = "",
                     mode: str = "inline") -> AgentResult:
    """预览/局部修补的统一结果：同一 assembler，不写生产 Task 黑板。"""
    run_id = run_id or f"inline_{stage}_{id(input_obj):x}"
    try:
        output = await stage_subagent.run_inline(stage, input_obj, context,
                                                 timeout_sec=timeout_sec)
    except Exception as exc:  # noqa: BLE001 — 预览失败也要是结构化终态
        _cat = str(getattr(exc, "category", "") or "internal_defect")
        return _ra.build(
            run_id=run_id, kind="stage", mode=mode,  # type: ignore[arg-type]
            task_id=task_id or str(getattr(context, "pipeline_id", "") or ""),
            trace_id=task_id or str(getattr(context, "pipeline_id", "") or ""),
            stage=stage, role=stage, status="failed",
            error=f"{type(exc).__name__}: {exc}",
            failure={"category": _cat,
                     "retryable": "retry_same" if _cat == "provider_error" else "needs_human",
                     "blocking_point": str(exc)[:200], "evidence": []},
        )

    failure_obj = getattr(output, "failure", None)
    failure = failure_obj.model_dump(mode="json") if hasattr(failure_obj, "model_dump") else failure_obj
    decision = str(getattr(output, "decision", "") or "")
    error = str(getattr(output, "error", "") or "")
    status = "failed" if (failure or (decision.endswith("fail") and error)) else "ok"
    payload = output.model_dump(mode="json") if hasattr(output, "model_dump") else dict(output or {})
    needs = [dict(n) for n in (payload.get("needs") or []) if isinstance(n, dict)]
    degradations = [d.model_dump(mode="json") if hasattr(d, "model_dump") else dict(d)
                    for d in (getattr(output, "degradations", None) or [])]
    if status == "ok" and degradations:
        status = "degraded"
    return _ra.build(
        run_id=run_id, kind="stage", mode=mode,  # type: ignore[arg-type]
        task_id=task_id or str(getattr(context, "pipeline_id", "") or ""),
        trace_id=task_id or str(getattr(context, "pipeline_id", "") or ""),
        stage=stage, role=stage, status=status,  # type: ignore[arg-type]
        summary=str(payload.get("summary") or "") or f"{stage} inline 完成",
        outputs=payload,
        needs=needs,
        failure=failure,
        degradations=degradations,
        error=error,
    )


__all__ = ["map_status", "run_inline", "run_stage"]
