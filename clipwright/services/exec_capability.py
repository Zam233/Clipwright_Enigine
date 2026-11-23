"""执行能力桥（Kernel run_shell / run_python 的唯一入口）。"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from clipwright.schema.exec import (
    ExecApproval,
    ExecHistoryItem,
    ExecRequest,
    ExecRiskReport,
)
from clipwright.schema.task import PlanStep, Task


def enabled() -> bool:
    from clipwright.config import settings

    return (
        str(getattr(settings, "agent_mode", "off") or "off").lower() == "on"
        and bool(getattr(settings, "kernel_exec_enabled", False))
    )


def _sha(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _emit(task: Task, event_type: str, message: str, detail: dict[str, Any]) -> None:
    try:
        from clipwright.services.task_events import emit

        emit(str(task.id), event_type, message, detail, span="tool")
    except Exception:  # noqa: BLE001 — 审计事件失败不能阻断执行
        pass


def _history(task: Task, limit: int = 20) -> list[ExecHistoryItem]:
    from clipwright.config import settings
    from clipwright.services import exec_store

    limit = int(getattr(settings, "exec_history_limit", limit) or limit)
    try:
        rows = exec_store.list_history(task, limit=limit)
    except Exception:  # noqa: BLE001
        return []
    out: list[ExecHistoryItem] = []
    for row in rows:
        try:
            out.append(ExecHistoryItem.model_validate(row))
        except Exception:  # noqa: BLE001
            continue
    return out


def _risk_report(raw: dict[str, Any], kind: str) -> ExecRiskReport:
    nested = raw.get("riskReport") or raw.get("risk_report") or {}
    if not isinstance(nested, dict):
        nested = {}
    return ExecRiskReport(
        risk=str((nested.get("risk") or raw.get("risk")
                  or raw.get("riskLevel") or "high")),  # type: ignore[arg-type]
        reasons=[str(x) for x in (
            nested.get("reasons") or raw.get("reasons")
            or raw.get("riskReasons") or []
        )][:8],
        hard_floor=bool(nested.get("hardFloor") or nested.get("hard_floor")
                        or raw.get("hardFloor") or raw.get("hard_floor")),
        shell=str(nested.get("shell") or raw.get("shell") or ""),
    )


def _approval_from_pending(task: Task, request: ExecRequest,
                           pending: dict[str, Any]) -> ExecApproval:
    risk = str(pending.get("riskLevel") or pending.get("risk") or "high")
    report = _risk_report(pending, request.kind)
    raw_exp = str(pending.get("expiresAt") or pending.get("expires_at") or "")
    try:
        expires = datetime.fromisoformat(raw_exp) if raw_exp else datetime.now(timezone.utc) + timedelta(seconds=600)
    except ValueError:
        expires = datetime.now(timezone.utc) + timedelta(seconds=600)
    return ExecApproval(
        approval_id=str(pending.get("id") or ""),
        exec_request_id=request.exec_request_id,
        task_id=str(task.id),
        kind=request.kind,
        command=request.command or request.code,
        cwd=request.cwd,
        risk=risk,  # type: ignore[arg-type]
        risk_report=report,
        needs_confirm=risk in ("high", "critical"),
        needs_double_confirm=bool(pending.get("needsDoubleConfirm") or risk == "critical"),
        hard_floor=bool(pending.get("hardFloor")),
        history=request.history,
        command_sha256=request.command_sha256,
        risk_sha256=_sha(f"{risk}|{request.command_sha256}|{request.cwd_sha256}"),
        requested_by="kernel",
        expires_at=expires,
        status="pending",
    )


async def run(task: Task, step: PlanStep) -> dict[str, Any]:
    """执行一步 shell / python（经网关）。"""
    if not enabled():
        return {
            "status": "refused",
            "error": "kernel exec 未启用（需要 agent_mode=on 且 kernel_exec_enabled=true）",
        }

    from clipwright.services import exec_store
    from clipwright.services import workspace_guard
    from clipwright.supervisor import gateway as gw

    args = dict(step.args or {})
    try:
        guarded = workspace_guard.assert_cwd_in_workspace(
            task, str(args.get("cwd") or ""),
        )
    except workspace_guard.WorkspaceGuardError as exc:
        return {"status": "refused", "error": str(exc), "code": exc.category,
                **exc.detail}

    kind = "python" if step.capability == "run_python" else "shell"
    command = str(args.get("command") or "")
    code = str(args.get("code") or "")
    if kind == "shell" and not command.strip():
        return {"status": "refused", "error": "run_shell 需要 command"}
    if kind == "python" and not code.strip():
        return {"status": "refused", "error": "run_python 需要 code"}

    request_id, _ = exec_store.new_ids()
    request = ExecRequest(
        exec_request_id=request_id,
        task_id=str(task.id),
        kind=kind,  # type: ignore[arg-type]
        command=command,
        code=code,
        cwd=guarded.as_posix(),
        timeout_sec=float(args.get("timeout_sec") or 60.0),
        purpose=str(args.get("purpose") or ""),
        history=_history(task),
        command_sha256=_sha(command or code),
        cwd_sha256=_sha(guarded.as_posix()),
    )
    risk_context = {
        "task_goal": str(getattr(getattr(task, "request", None), "topic", "") or ""),
        "workspace_root": str(guarded.workspace_root),
        "history": [h.model_dump(mode="json") for h in request.history],
        "current": {"kind": kind, "text": command or code, "cwd": request.cwd},
        "purpose": request.purpose,
        "command_sha256": request.command_sha256,
        "cwd_sha256": request.cwd_sha256,
    }
    _emit(task, "exec_request", "请求执行命令", {
        "exec_request_id": request_id, "kind": kind, "cwd": request.cwd,
        "command": (command or code)[:500], "purpose": request.purpose,
        "history_count": len(request.history),
    })
    try:
        if kind == "python":
            out = await gw.run_python(
                task.id, code, cwd=request.cwd, timeout_sec=request.timeout_sec,
                risk_context=risk_context, exec_request_id=request_id,
            )
        else:
            out = await gw.run_command(
                task.id, command, cwd=request.cwd, timeout_sec=request.timeout_sec,
                kind="shell", risk_context=risk_context, exec_request_id=request_id,
            )
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

    status = str(out.get("status") or "")
    if status == "pending_approval":
        approval_raw = out.get("approval") or {}
        approval = _approval_from_pending(task, request, approval_raw)
        approval.status = "pending"
        try:
            exec_store.create_approval(task, approval)
            task.pending_exec_approval_id = approval.approval_id
        except Exception:  # noqa: BLE001
            pass
        _emit(task, "exec_risk", f"执行风险 {approval.risk}", {
            "exec_request_id": request_id, "risk": approval.risk,
            "reasons": approval.risk_report.reasons,
            "risk_report": approval.risk_report.model_dump(mode="json"),
        })
        return {
            "status": "pending_approval",
            "approval_id": approval.approval_id,
            "exec_request_id": request_id,
            "risk": approval.risk,
            "reasons": approval.risk_report.reasons,
            "command": approval.command[:200],
            "cwd": approval.cwd,
            "history": [h.model_dump(mode="json") for h in approval.history],
            "approval": approval.model_dump(mode="json"),
            "note": "命令被判定需要人工授权，任务已暂停；授权后 resume 会继续",
        }
    if status == "rejected":
        return {"status": "refused", "error": str(out.get("reason") or "被安全策略拒绝")}

    exit_code = out.get("exitCode")
    ok = exit_code == 0
    history = ExecHistoryItem(
        exec_request_id=request_id, kind=kind,  # type: ignore[arg-type]
        text=(command or code)[:2000], cwd=request.cwd,
        exit_code=exit_code, status="done" if ok else "failed",
        risk=str(out.get("riskLevel") or out.get("risk") or "high"),  # type: ignore[arg-type]
        decided_by="kernel", output_tail=str(out.get("outputTail") or "")[:400],
    )
    try:
        exec_store.append_history(task, history)
    except Exception:  # noqa: BLE001
        pass
    _emit(task, "exec_risk", f"执行完成（{out.get('riskLevel') or 'unknown'}）", {
        "exec_request_id": request_id,
        "risk": str(out.get("riskLevel") or "high"),
        "reasons": list(out.get("riskReasons") or []),
    })
    _emit(task, "exec_result", "命令执行完成", {
        "exec_request_id": request_id, "status": "done" if ok else "failed",
        "exit_code": exit_code, "cwd": request.cwd,
        "shell": out.get("shell") or "",
        "output_tail": str(out.get("outputTail") or "")[:400],
        "duration_ms": out.get("durationMs"),
    })
    return {
        "status": "ok" if ok else "error",
        "exec_request_id": request_id,
        "output": {
            "exit_code": exit_code,
            "output_tail": str(out.get("outputTail") or "")[:4000],
            "duration_ms": out.get("durationMs"),
            "cwd": request.cwd,
            "shell": out.get("shell") or "",
        },
        "error": None if ok else f"退出码 {exit_code}",
    }


async def resume_approved(task: Task, approval_id: str) -> dict[str, Any]:
    """用户批准后执行精确绑定的一条命令（不再二次 LLM 判定）。"""
    from clipwright.services import exec_store
    from clipwright.supervisor import gateway as gw

    item = exec_store.get_approval(task, approval_id)
    if item is None:
        return {"status": "refused", "error": "approval 不存在"}
    if str(item.get("status") or "") != "approved":
        return {"status": "refused", "error": "approval 尚未批准或已消费"}
    command = str(item.get("command") or "")
    cwd = str(item.get("cwd") or "")
    kind = str(item.get("kind") or "shell")
    # command 字段对 python 存的是代码前 2000 字；执行前以请求参数为准由 approve 侧处理
    try:
        if kind == "python":
            out = await gw.run_python(
                task.id, command, cwd=cwd, timeout_sec=60.0,
                approval_id=approval_id, skip_risk=True,
            )
        else:
            out = await gw.run_command(
                task.id, command, cwd=cwd, timeout_sec=60.0,
                approval_id=approval_id, skip_risk=True,
            )
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    return {"status": "ok" if out.get("exitCode") == 0 else "error", "output": out}
