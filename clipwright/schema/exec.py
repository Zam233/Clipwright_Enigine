"""Kernel Exec 契约（PRD kernel-exec-approval）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

ExecKind = Literal["shell", "python"]
RiskLevel = Literal["low", "med", "high", "critical"]
ExecStatus = Literal[
    "pending_approval", "approved", "running", "done", "failed",
    "rejected", "expired", "refused", "re_approval_required",
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ExecHistoryItem(BaseModel):
    exec_request_id: str
    kind: ExecKind
    text: str
    cwd: str = ""
    exit_code: int | None = None
    status: str = ""
    risk: RiskLevel = "low"
    decided_by: str = ""
    output_tail: str = ""
    created_at: datetime = Field(default_factory=_utcnow)


class ExecRiskReport(BaseModel):
    risk: RiskLevel = "high"
    reasons: list[str] = Field(default_factory=list)
    affected_paths: list[str] = Field(default_factory=list)
    external_network: bool = False
    destructive: bool = False
    credential_access: bool = False
    hard_floor: bool = False
    history_considered: bool = True
    shell: str = ""
    model: str = ""
    prompt_version: str = ""
    fallback: bool = False
    assessed_at: datetime = Field(default_factory=_utcnow)


class ExecRequest(BaseModel):
    exec_request_id: str
    task_id: str
    kind: ExecKind
    command: str = ""
    code: str = ""
    cwd: str
    timeout_sec: float = 60.0
    requested_by: str = "kernel"
    purpose: str = ""
    history: list[ExecHistoryItem] = Field(default_factory=list)
    command_sha256: str = ""
    cwd_sha256: str = ""
    created_at: datetime = Field(default_factory=_utcnow)


class ExecApproval(BaseModel):
    approval_id: str
    exec_request_id: str
    task_id: str
    kind: ExecKind
    command: str
    cwd: str
    risk: RiskLevel
    risk_report: ExecRiskReport
    needs_confirm: bool = False
    needs_double_confirm: bool = False
    hard_floor: bool = False
    history: list[ExecHistoryItem] = Field(default_factory=list)
    command_sha256: str
    risk_sha256: str
    status: Literal[
        "pending", "approved", "rejected", "expired", "executed",
        "re_approval_required",
    ] = "pending"
    requested_by: str = "kernel"
    created_at: datetime = Field(default_factory=_utcnow)
    expires_at: datetime = Field(default_factory=lambda: _utcnow() + timedelta(seconds=600))
    decided_at: datetime | None = None


class ExecResult(BaseModel):
    exec_request_id: str
    task_id: str
    kind: ExecKind
    status: ExecStatus
    exit_code: int | None = None
    output_tail: str = ""
    duration_ms: int = 0
    error: str = ""
    risk: RiskLevel = "high"
    cwd: str = ""
    shell: str = ""
