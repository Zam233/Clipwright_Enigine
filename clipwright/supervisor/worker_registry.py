"""临时 worker 注册表（PRD F10 / 开发计划 B8）。

与 `AgentRegistry`（插件注册表）刻意分开：

- 这里的 worker **只服务一次管线**，管线结束即回收；
- 不写入 `AgentRegistry`，不会变成常驻能力（PRD 红线三）；
- 带执行权限的 worker 在审核通过前**不得实例化**（PRD F10 审核门）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from clipwright.config import logger

_MAX_PIPELINES = 200

#: 主代理可授予的工具全集（子集校验用）
SUPERVISOR_TOOLS: frozenset[str] = frozenset({
    "spawn_subagent",
    "get_subagent_status",
    "list_subagents",
    "cancel_subagent",
    "run_shell",
    "run_python",
    "read_file",
    "list_dir",
    # 文件写 + 联网：子代理也要能查资料、落结论（写仍限白名单目录）
    "write_file",
    "web_search",
    "web_fetch",
})

#: 执行类工具：授予即代表该 worker 有「动手」能力 → 必须过两级审核门
EXEC_TOOLS: frozenset[str] = frozenset({"run_shell", "run_python"})


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class WorkerSpec:
    """临时 worker 的规格单（PRD §8 `WorkerSpec`）。"""

    role: str
    system_prompt: str = ""
    tools: tuple[str, ...] = ()
    provider: str = ""
    model: str = ""
    timeout_sec: float = 60.0
    budget_usd: Optional[float] = None
    max_turns: int = 5
    kind: str = "ephemeral"

    @property
    def needs_exec_review(self) -> bool:
        return bool(set(self.tools) & EXEC_TOOLS)

    def to_public(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "system_prompt": self.system_prompt[:2000],
            "tools": list(self.tools),
            "provider": self.provider,
            "model": self.model,
            "timeout_sec": self.timeout_sec,
            "budget_usd": self.budget_usd,
            "max_turns": self.max_turns,
            "kind": self.kind,
            "needs_exec_review": self.needs_exec_review,
        }


@dataclass
class WorkerEntry:
    id: str
    pipeline_id: str
    spec: WorkerSpec
    status: str = "draft"  # draft|pending_review|approved|rejected|running|done|failed
    subagent_id: str = ""
    review: dict[str, Any] = field(default_factory=dict)
    #: worker 内部的工具调用轨迹（主代理据此判断"它到底查了什么"）
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=_utcnow)
    ts: datetime = field(default_factory=_utcnow)

    def note_call(self, tool: str, *, ok: bool, error: str = "", brief: str = "") -> None:
        """记录一次工具调用（有界 20 条：主代理只需要知道大概做了哪些事）。"""
        self.tool_calls.append({
            "tool": str(tool)[:40],
            "ok": bool(ok),
            "error": str(error)[:200],
            "brief": str(brief)[:200],
        })
        if len(self.tool_calls) > 20:
            del self.tool_calls[: len(self.tool_calls) - 20]
        self.ts = _utcnow()

    def to_public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "pipeline_id": self.pipeline_id,
            "status": self.status,
            "subagent_id": self.subagent_id,
            "review": self.review,
            "tool_calls": list(self.tool_calls),
            "spec": self.spec.to_public(),
            "created_at": self.created_at.isoformat(),
        }


class WorkerRegistry:
    """一条管线的临时 worker 集合。"""

    def __init__(self, pipeline_id: str) -> None:
        self.pipeline_id = pipeline_id
        self._workers: dict[str, WorkerEntry] = {}

    def register(self, spec: WorkerSpec) -> WorkerEntry:
        entry = WorkerEntry(
            id=f"w_{uuid4().hex[:8]}",
            pipeline_id=self.pipeline_id,
            spec=spec,
            status="pending_review" if spec.needs_exec_review else "draft",
        )
        self._workers[entry.id] = entry
        return entry

    def get(self, worker_id: str) -> Optional[WorkerEntry]:
        return self._workers.get(worker_id)

    def list(self) -> list[WorkerEntry]:
        return list(self._workers.values())

    def pending_reviews(self) -> list[WorkerEntry]:
        return [w for w in self._workers.values() if w.status == "pending_review"]

    def mark(self, worker_id: str, status: str, **extra: Any) -> Optional[WorkerEntry]:
        entry = self._workers.get(worker_id)
        if entry is None:
            return None
        entry.status = status
        entry.ts = _utcnow()
        for key, value in extra.items():
            if hasattr(entry, key):
                setattr(entry, key, value)
        return entry

    def gc(self) -> int:
        """管线结束时回收（返回回收数量）。"""
        n = len(self._workers)
        self._workers.clear()
        return n


# ── 注册表 ──────────────────────────────────────────────────

_REGISTRIES: dict[str, WorkerRegistry] = {}


def get_workers(pipeline_id: str) -> WorkerRegistry:
    reg = _REGISTRIES.get(pipeline_id)
    if reg is None:
        if len(_REGISTRIES) >= _MAX_PIPELINES:
            oldest = next(iter(_REGISTRIES))
            _REGISTRIES.pop(oldest, None)
            logger.debug("WorkerRegistry: 淘汰最旧管线 %s", oldest)
        reg = WorkerRegistry(pipeline_id)
        _REGISTRIES[pipeline_id] = reg
    return reg


def find_worker(worker_id: str) -> Optional[WorkerEntry]:
    for reg in _REGISTRIES.values():
        entry = reg.get(worker_id)
        if entry is not None:
            return entry
    return None


def all_pending_reviews() -> list[WorkerEntry]:
    out: list[WorkerEntry] = []
    for reg in _REGISTRIES.values():
        out.extend(reg.pending_reviews())
    return out


def drop_workers(pipeline_id: str) -> None:
    reg = _REGISTRIES.pop(pipeline_id, None)
    if reg is not None:
        reg.gc()


def clear_workers() -> None:
    _REGISTRIES.clear()


def registry_count() -> int:
    return len(_REGISTRIES)
