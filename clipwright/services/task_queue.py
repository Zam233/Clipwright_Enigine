"""任务队列 — 并发隔离 + 排队 + 限流。

Layer 1: 并发请求不会共享同一个 orchestrator 实例。
Layer 3: 支持批量提交。

用法:
    queue = TaskQueue(max_concurrent=3)
    task_id = await queue.submit("pipeline", handler_func, *args)
    status = queue.get_status(task_id)
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Optional

from clipwright.config import TIME_ZONE, logger


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskResult:
    """任务执行结果。"""
    def __init__(
        self,
        task_id: str,
        status: TaskStatus,
        result: Any = None,
        error: str = "",
        duration_sec: float = 0,
    ):
        self.task_id = task_id
        self.status = status
        self.result = result
        self.error = error
        self.duration_sec = duration_sec

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "duration_sec": self.duration_sec,
        }


class PipelineTask:
    """管线执行任务。"""
    def __init__(
        self,
        task_id: str,
        task_type: str,
        handler: Callable,
        args: tuple,
        kwargs: dict,
    ):
        self.task_id = task_id
        self.task_type = task_type  # "pipeline" / "batch"
        self.handler = handler
        self.args = args
        self.kwargs = kwargs
        self.status = TaskStatus.PENDING
        self.result: Any = None
        self.error: str = ""
        self.duration_sec: float = 0
        self.progress: float = 0
        self.progress_text: str = ""
        self.created_at = datetime.now(tz=TIME_ZONE) if TIME_ZONE else datetime.now(timezone.utc)
        self.started_at: Optional[datetime] = None
        self.completed_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "status": self.status.value,
            "progress": self.progress,
            "progress_text": self.progress_text,
            "error": self.error,
            "duration_sec": self.duration_sec,
            "created_at": self.created_at.isoformat() if self.created_at else "",
            "started_at": self.started_at.isoformat() if self.started_at else "",
        }


class TaskQueue:
    """并发任务队列 — 最多 N 个任务同时执行，其余排队。"""

    def __init__(self, max_concurrent: int = 3, task_timeout_sec: int = 900):
        self.max_concurrent = max_concurrent
        self.task_timeout_sec = task_timeout_sec
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._tasks: dict[str, PipelineTask] = {}
        self._pending_queue: list[str] = []

    async def submit(
        self,
        task_type: str,
        handler: Callable,
        *args: Any,
        **kwargs: Any,
    ) -> str:
        """提交任务到队列。

        Args:
            task_type: 任务类型标识
            handler: 异步处理函数
            *args / **kwargs: 传给 handler 的参数

        Returns:
            task_id
        """
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        task = PipelineTask(task_id, task_type, handler, args, kwargs)
        self._tasks[task_id] = task
        self._pending_queue.append(task_id)

        # 启动处理（不等待）
        asyncio.create_task(self._process_queue())
        return task_id

    async def _process_queue(self) -> None:
        """从队列取任务执行（受信号量限制）。"""
        while self._pending_queue:
            task_id = self._pending_queue.pop(0)
            task = self._tasks.get(task_id)
            if not task:
                continue

            async with self._semaphore:
                task.status = TaskStatus.RUNNING
                task.started_at = datetime.now(tz=TIME_ZONE) if TIME_ZONE else datetime.now(timezone.utc)
                try:
                    task.result = await asyncio.wait_for(
                        task.handler(*task.args, **task.kwargs),
                        timeout=self.task_timeout_sec,
                    )
                    task.status = TaskStatus.COMPLETED
                except asyncio.TimeoutError:
                    task.status = TaskStatus.FAILED
                    task.error = f"任务执行超时（>{self.task_timeout_sec}s）"
                    logger.error("任务 %s 超时: >%ss", task_id, self.task_timeout_sec)
                except asyncio.CancelledError:
                    task.status = TaskStatus.CANCELLED
                    task.error = "任务被取消"
                except Exception as e:
                    task.status = TaskStatus.FAILED
                    task.error = str(e)
                    logger.exception("任务 %s 失败: %s", task_id, e)
                finally:
                    now = datetime.now(tz=TIME_ZONE) if TIME_ZONE else datetime.now(timezone.utc)
                    task.completed_at = now
                    if task.started_at:
                        task.duration_sec = (now - task.started_at).total_seconds()
                    task.progress = 100
                    self._cleanup_finished()

    def _cleanup_finished(self, keep: int = 200) -> None:
        """清理已完成任务，防止 _tasks 无限增长（保留最近 keep 个）。"""
        finished = [
            t for t in self._tasks.values()
            if t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)
        ]
        if len(finished) <= keep:
            return
        finished.sort(key=lambda t: t.created_at.timestamp() if t.created_at else 0.0)
        for t in finished[: len(finished) - keep]:
            self._tasks.pop(t.task_id, None)

    def get_task(self, task_id: str) -> PipelineTask | None:
        return self._tasks.get(task_id)

    def get_status(self, task_id: str) -> dict:
        task = self._tasks.get(task_id)
        return task.to_dict() if task else {"task_id": task_id, "status": "not_found"}

    def cancel(self, task_id: str) -> bool:
        """取消一个任务（仅支持 pending 状态）。"""
        task = self._tasks.get(task_id)
        if task and task.status == TaskStatus.PENDING:
            task.status = TaskStatus.CANCELLED
            self._pending_queue = [t for t in self._pending_queue if t != task_id]
            return True
        return False

    def update_progress(self, task_id: str, progress: float, text: str = "") -> None:
        """更新任务进度（由 handler 内部调用）。"""
        task = self._tasks.get(task_id)
        if task:
            task.progress = min(progress, 99)
            task.progress_text = text

    def list_tasks(
        self,
        task_type: str = "",
        status: str = "",
        limit: int = 50,
    ) -> list[dict]:
        """列出任务。"""
        tasks = list(self._tasks.values())
        if task_type:
            tasks = [t for t in tasks if t.task_type == task_type]
        if status:
            tasks = [t for t in tasks if t.status.value == status]
        tasks.sort(key=lambda t: t.created_at.timestamp() if t.created_at else 0.0, reverse=True)
        return [t.to_dict() for t in tasks[:limit]]

    @property
    def running_count(self) -> int:
        return sum(1 for t in self._tasks.values() if t.status == TaskStatus.RUNNING)

    @property
    def pending_count(self) -> int:
        return len(self._pending_queue)


# 全局单例
_task_queue = TaskQueue(max_concurrent=3)


def get_task_queue() -> TaskQueue:
    """获取全局任务队列单例。"""
    return _task_queue
