"""通用 batch job 状态（R8 基础设施）。

只记录状态与计数，不执行任务；执行器在完成/失败/取消时更新。
进程内、有界、只读查询；不写任务黑板。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

_MAX_JOBS = 300


@dataclass
class ToolJob:
    job_id: str
    kind: str
    total: int
    status: str = "queued"  # queued|running|done|partial|failed|cancelled
    completed: int = 0
    failed: int = 0
    error: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id, "kind": self.kind, "status": self.status,
            "total": self.total, "completed": self.completed, "failed": self.failed,
            "error": self.error, "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


_JOBS: OrderedDict[str, ToolJob] = OrderedDict()


def create(kind: str, total: int) -> ToolJob:
    job = ToolJob(job_id=f"job_{uuid.uuid4().hex[:12]}", kind=str(kind or "tool"),
                  total=max(0, int(total or 0)), status="running")
    _JOBS[job.job_id] = job
    while len(_JOBS) > _MAX_JOBS:
        _JOBS.popitem(last=False)
    return job


def update(job_id: str, *, completed: int | None = None, failed: int | None = None,
           status: str = "", error: str = "") -> dict[str, Any] | None:
    job = _JOBS.get(str(job_id))
    if job is None:
        return None
    if job.status == "cancelled":
        return job.as_dict()
    if completed is not None:
        job.completed = max(0, int(completed))
    if failed is not None:
        job.failed = max(0, int(failed))
    if status:
        job.status = str(status)
    elif job.completed + job.failed >= job.total and job.total:
        job.status = "done" if job.failed == 0 else "partial"
    if error:
        job.error = str(error)[:500]
    job.updated_at = time.time()
    return job.as_dict()


def finish(job_id: str, *, failed: int = 0, error: str = "") -> dict[str, Any] | None:
    job = _JOBS.get(str(job_id))
    if job is None:
        return None
    if job.status == "cancelled":
        return job.as_dict()
    return update(str(job_id), completed=job.total, failed=failed, error=error,
                  status="partial" if failed else "done")


def is_cancelled(job_id: str) -> bool:
    job = _JOBS.get(str(job_id))
    return job is not None and job.status == "cancelled"


def cancel(job_id: str, reason: str = "cancelled") -> dict[str, Any] | None:
    job = _JOBS.get(str(job_id))
    if job is None:
        return None
    job.status = "cancelled"
    job.error = str(reason)[:500]
    job.updated_at = time.time()
    return job.as_dict()


def get(job_id: str) -> dict[str, Any] | None:
    job = _JOBS.get(str(job_id))
    return job.as_dict() if job else None


def get_job(job_id: str) -> ToolJob | None:
    return _JOBS.get(str(job_id))


def list_jobs(limit: int = 50) -> list[dict[str, Any]]:
    return [j.as_dict() for j in reversed(list(_JOBS.values()))][: max(1, int(limit or 50))]


def clear() -> None:
    _JOBS.clear()


def emit_progress(task_id: str, job: ToolJob, *, message: str = "") -> None:
    """把 job 进度写进任务事件流（可选；无 task_id 时静默）。"""
    if not task_id:
        return
    try:
        from clipwright.services import task_events

        task_events.emit(
            str(task_id), "subagent_progress",
            message or f"{job.kind} 进度 {job.completed + job.failed}/{job.total}",
            {"kind": "tool_job_progress", "job_id": job.job_id, "tool": job.kind,
             "status": job.status, "total": job.total,
             "completed": job.completed, "failed": job.failed},
            span="tool",
        )
    except Exception:  # noqa: BLE001 — 进度事件不影响批处理
        pass


async def _heartbeat(job_id: str, task_id: str, interval: float = 10.0) -> None:
    while True:
        await asyncio.sleep(max(1.0, float(interval)))
        job = _JOBS.get(str(job_id))
        if job is None or job.status in ("done", "partial", "failed", "cancelled"):
            return
        emit_progress(task_id, job, message=f"{job.kind} 心跳 {job.completed + job.failed}/{job.total}")


def start_heartbeat(task_id: str, job: ToolJob, interval: float = 10.0):
    """为长批量任务启动 10s 心跳；无 task_id 时返回 None。"""
    if not task_id:
        return None
    try:
        return asyncio.create_task(_heartbeat(job.job_id, task_id, interval))
    except RuntimeError:
        return None


def stop_heartbeat(task) -> None:
    if task is not None:
        task.cancel()
