"""ResultAssembler —— `AgentResult` 的唯一终态出口（Coding Plan B02）。

纪律（PRD v0.4 §5）：
1. 早退（缺参/越权/熔断/取消/异常）与正常结束走**同一个** build；
2. 终态原因由 `terminal_reason` 给出（机器路由），自由文本只做展示；
3. `needs` 每字段一条（`Need`），缺项与 `redo_request` 互斥；
4. 报告状态区分 written / unavailable / not_applicable；
5. 未知费用保持 `cost_usd=None + cost_unknown=True`，不伪造 0。
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from clipwright.schema.agent import Need, RedoRequest
from clipwright.schema.run import AgentResult, RunKind, RunStatus

_TERMINAL_BY_STATUS: dict[str, str] = {
    "ok": "completed",
    "degraded": "degraded",
    "needs": "input_insufficient",
    "failed": "internal_defect",
    "refused": "policy_refused",
    "cancelled": "user_cancelled",
    "dry_run": "validated_only",
}

_NEED_KEYS = {"field", "why", "from_stage", "resolution", "evidence_ref"}
_MAX_HANDOFF = 600


def terminal_reason_of(status: str, failure: Any = None) -> str:
    """从终态 + 失败归因推出机器可读原因（显式传入时优先）。"""
    if isinstance(failure, dict):
        category = str(failure.get("category") or "").strip()
        if category:
            return category
    return _TERMINAL_BY_STATUS.get(str(status), "unknown")


def normalize_needs(raw: Iterable[Any] | None) -> list[Need]:
    """把 dict / Need 混合列表归一成 `Need[]`；未知键显式丢弃（不静默改语义）。"""
    out: list[Need] = []
    for item in raw or []:
        if isinstance(item, Need):
            out.append(item)
            continue
        if not isinstance(item, dict):
            continue
        data = {k: v for k, v in item.items() if k in _NEED_KEYS and v not in (None, "")}
        if data:
            out.append(Need(**data))
    return out


def _handoff(status: str, summary: str, needs: list[Need], redo_request: Optional[RedoRequest],
             degradations: list[dict[str, Any]], error: str) -> str:
    """生成非空交接摘要（正文自由 Markdown，机器只读这句）。

    摘要先保留业务结论，再补「缺什么 / 建议重做 / 降级」——Kernel 先看 needs 再考虑 redo。
    """
    base = summary.strip() or ""
    if not base:
        if error:
            base = error[:200]
        else:
            base = f"阶段终态 {status}"
    suffix: list[str] = []
    if needs:
        suffix.append("缺 " + "、".join(n.field for n in needs[:4]) + "，补齐后重派")
    if redo_request is not None:
        suffix.append(f"建议重做 {redo_request.target_stage or '上游'}"
                      f"（scope={redo_request.scope or 'global'}）")
    if degradations:
        kinds = [str(d.get("kind") or "") for d in degradations[:3] if d.get("kind")]
        if kinds:
            suffix.append("降级：" + "、".join(kinds))
    text = base if not suffix else base.rstrip("。；;") + "；" + "；".join(suffix)
    return text[:_MAX_HANDOFF]


def _report_state(status: str, report_path: str, explicit: Optional[str]) -> str:
    if explicit:
        return explicit
    if report_path:
        return "written"
    return "not_applicable" if status == "dry_run" else "unavailable"


def _trace_state(total: int, path: str, explicit: Optional[str]) -> str:
    if explicit:
        return explicit
    if total <= 0:
        return "empty"
    return "complete" if path else "partial"


def build(
    *,
    run_id: str,
    kind: RunKind,
    status: RunStatus,
    mode: str = "task",
    task_id: str = "",
    trace_id: str = "",
    attempt: int = 1,
    stage: str = "",
    role: str = "",
    terminal_reason: str = "",
    summary: str = "",
    brief: str = "",
    outputs: dict[str, Any] | None = None,
    writes: list[str] | None = None,
    needs: Iterable[Any] | None = None,
    failure: Any = None,
    error: str = "",
    issues: list[dict[str, Any]] | None = None,
    redo_agent: str = "",
    redo_hint: str = "",
    redo_request: RedoRequest | dict[str, Any] | None = None,
    degradations: list[dict[str, Any]] | None = None,
    report_path: str = "",
    report_seq: int = 0,
    report_state: Optional[str] = None,
    handoff_summary: str = "",
    tool_calls: list[dict[str, Any]] | None = None,
    tool_calls_path: str = "",
    tool_calls_total: Optional[int] = None,
    trace_state: Optional[str] = None,
    tokens: int = 0,
    cost_usd: Optional[float] = None,
    cost_unknown: Optional[bool] = None,
    duration_ms: int = 0,
    provider: str = "",
    model: str = "",
    replayed: bool = False,
    replay_of: str = "",
    spec_ref: str = "",
    details: dict[str, Any] | None = None,
) -> AgentResult:
    """构造一次终态。所有出参字段都先归一，再交给模型做结构校验。"""
    failure_dict = failure
    if failure is not None and hasattr(failure, "model_dump"):
        failure_dict = failure.model_dump(mode="json")

    need_list = normalize_needs(needs)
    # 兼容迁移期：阶段把缺项挂在 failure.needs 镜像里，这里并入顶层（B03 后删除镜像）
    if not need_list and isinstance(failure_dict, dict):
        need_list = normalize_needs(failure_dict.get("needs") or [])

    redo = redo_request
    if isinstance(redo, dict):
        redo = RedoRequest(**redo)

    degradations = list(degradations or [])
    calls = list(tool_calls or [])
    total = int(tool_calls_total if tool_calls_total is not None else len(calls))
    reason = terminal_reason or terminal_reason_of(status, failure_dict)

    return AgentResult(
        run_id=run_id,
        kind=kind,
        mode=mode,  # type: ignore[arg-type]
        task_id=task_id,
        trace_id=trace_id,
        attempt=max(1, int(attempt or 1)),
        stage=stage,
        role=role,
        status=status,
        terminal_reason=reason,
        summary=summary,
        brief=brief,
        outputs=dict(outputs or {}),
        writes=list(writes or []),
        needs=need_list,
        report_path=report_path,
        report_seq=int(report_seq or 0),
        report_state=_report_state(str(status), report_path, report_state),  # type: ignore[arg-type]
        handoff_summary=handoff_summary or _handoff(str(status), summary, need_list, redo,
                                                    degradations, error),
        issues=list(issues or []),
        redo_agent=redo_agent,
        redo_hint=redo_hint,
        redo_request=redo,
        details=dict(details or {}),
        degradations=degradations,
        tool_calls=calls,
        tool_calls_path=tool_calls_path,
        tool_calls_total=total,
        trace_state=_trace_state(total, tool_calls_path, trace_state),  # type: ignore[arg-type]
        tokens=int(tokens or 0),
        cost_usd=cost_usd,
        cost_unknown=(cost_usd is None) if cost_unknown is None else bool(cost_unknown),
        duration_ms=int(duration_ms or 0),
        provider=provider,
        model=model,
        replayed=replayed,
        replay_of=replay_of,
        spec_ref=spec_ref,
        failure=failure_dict,
        error=error,
    )


def report_io_failure(result: AgentResult, detail: str) -> AgentResult:
    """报告写盘失败：只加诊断，不覆盖原终态（失败/取消仍是失败/取消）。"""
    data = result.model_dump(mode="json")
    data["report_state"] = "unavailable"
    data["degradations"] = list(data.get("degradations") or []) + [{
        "kind": "report_io_failed",
        "detail": str(detail)[:300],
        "scope": result.stage or "report",
        "impact": "报告不可读；业务结果与 run 仍可查",
    }]
    if data.get("status") == "ok":
        data["status"] = "degraded"
        data["terminal_reason"] = data.get("terminal_reason") or "report_io_failed"
    return AgentResult(**data)


__all__ = [
    "build",
    "normalize_needs",
    "report_io_failure",
    "terminal_reason_of",
]
