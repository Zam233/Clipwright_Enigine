"""远程渲染 Worker 内存任务存储 — 进程内 job 状态管理。

单进程内并发安全：所有读写经由 threading.Lock 保护；每个 job 只更新自己的
键，互不共享可变状态。过期条目（超过 ``_TTL_SECONDS`` 未更新）在任意访问时
被惰性清理。
"""

from __future__ import annotations

import threading
import time
from typing import Any

_TTL_SECONDS = 60.0

# job 状态字段缺省值（status/progress/phase/detail/error/output_path）
_DEFAULTS: dict[str, Any] = {
    "status": "queued",
    "progress": 0.0,
    "phase": "",
    "detail": "",
    "error": "",
    "output_path": "",
}


class JobStore:
    """线程安全的进程内 job 存储，附带 TTL 过期清理。"""

    __slots__ = ("_ttl", "_jobs", "_updated", "_lock")

    def __init__(self, ttl: float = _TTL_SECONDS) -> None:
        self._ttl = ttl
        self._jobs: dict[str, dict[str, Any]] = {}
        self._updated: dict[str, float] = {}
        self._lock = threading.Lock()

    def create_job(self, job_id: str, **fields: Any) -> dict[str, Any]:
        """创建 job，返回其当前状态的副本。"""
        with self._lock:
            self._prune_locked()
            job = {**_DEFAULTS, **fields}
            self._jobs[job_id] = job
            self._updated[job_id] = time.monotonic()
            return dict(job)

    def update_job(self, job_id: str, **fields: Any) -> dict[str, Any] | None:
        """按字段更新 job，刷新其最后更新时间；job 不存在时返回 None。"""
        with self._lock:
            self._prune_locked()
            job = self._jobs.get(job_id)
            if job is None:
                return None
            job.update(fields)
            self._updated[job_id] = time.monotonic()
            return dict(job)

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        """读取 job 当前状态的副本；不存在或已过期清理时返回 None。"""
        with self._lock:
            self._prune_locked()
            job = self._jobs.get(job_id)
            return dict(job) if job is not None else None

    def _prune_locked(self) -> None:
        """清理超过 TTL 未更新的过期条目（须在持锁下调用）。"""
        now = time.monotonic()
        stale = [jid for jid, ts in self._updated.items() if now - ts > self._ttl]
        for jid in stale:
            self._jobs.pop(jid, None)
            self._updated.pop(jid, None)


# 全局单例，供 API 层与渲染 runner 共用
store = JobStore()


def create_job(job_id: str, **fields: Any) -> dict[str, Any]:
    """模块级便捷函数：创建 job。"""
    return store.create_job(job_id, **fields)


def update_job(job_id: str, **fields: Any) -> dict[str, Any] | None:
    """模块级便捷函数：更新 job。"""
    return store.update_job(job_id, **fields)


def get_job(job_id: str) -> dict[str, Any] | None:
    """模块级便捷函数：读取 job。"""
    return store.get_job(job_id)
