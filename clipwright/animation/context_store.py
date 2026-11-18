"""Animation context store: session history + task blackboard + call records.

Engine-owned contract (see docs/animation-quality-improvement-plan.md §5.14).
Artifacts are referenced by id; the blackboard never stores large HTML/video.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


class ContextConflict(Exception):
    """Blackboard version conflict (HTTP 409 at API layer)."""


@dataclass
class CallRecord:
    call_id: str
    kind: str  # tool|skill
    name: str
    status: str
    input_summary: str = ""
    output_summary: str = ""
    artifact_ref: str = ""
    duration_ms: int = 0
    error: str = ""
    idempotency_key: str = ""
    children: list[str] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TaskContext:
    session_id: str
    task_id: str
    version: int = 1
    history: list[dict[str, Any]] = field(default_factory=list)
    history_summary: str = ""
    blackboard: dict[str, Any] = field(default_factory=dict)
    calls: list[CallRecord] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)
    updated_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "task_id": self.task_id,
            "version": self.version,
            "history": self.history,
            "history_summary": self.history_summary,
            "blackboard": self.blackboard,
            "calls": [c.as_dict() for c in self.calls],
            "artifacts": self.artifacts,
            "updated_at": self.updated_at,
        }


class AnimationContextStore:
    """In-memory store with optional JSON snapshot; Mongo can implement same API."""

    def __init__(self, root: str | Path | None = None) -> None:
        self._lock = threading.RLock()
        self._tasks: dict[str, TaskContext] = {}
        self._root = Path(root) if root else None
        if self._root:
            self._root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _key(session_id: str, task_id: str) -> str:
        return f"{session_id}:{task_id}"

    def _get(self, session_id: str, task_id: str) -> TaskContext:
        key = self._key(session_id, task_id)
        with self._lock:
            ctx = self._tasks.get(key)
            if ctx is None:
                ctx = TaskContext(session_id=session_id, task_id=task_id)
                self._tasks[key] = ctx
            return ctx

    def get(self, session_id: str, task_id: str) -> dict[str, Any]:
        with self._lock:
            return self._get(session_id, task_id).as_dict()

    def append_history(self, session_id: str, task_id: str, role: str, content: str, **meta: Any) -> None:
        with self._lock:
            ctx = self._get(session_id, task_id)
            ctx.history.append({"role": role, "content": content[:4000], "meta": meta, "at": time.time()})
            ctx.history = ctx.history[-200:]

    def summarize_history(self, session_id: str, task_id: str, summary: str) -> None:
        with self._lock:
            ctx = self._get(session_id, task_id)
            ctx.history_summary = summary[:4000]

    def patch_blackboard(self, session_id: str, task_id: str, patch: dict[str, Any], expected_version: int | None = None) -> dict[str, Any]:
        with self._lock:
            ctx = self._get(session_id, task_id)
            if expected_version is not None and expected_version != ctx.version:
                raise ContextConflict(f"blackboard version {expected_version} != {ctx.version}")
            ctx.blackboard.update(patch)
            ctx.version += 1
            ctx.updated_at = time.time()
            return ctx.as_dict()

    def record_call(self, session_id: str, task_id: str, kind: str, name: str,
                    status: str, **kwargs: Any) -> CallRecord:
        with self._lock:
            ctx = self._get(session_id, task_id)
            rec = CallRecord(
                call_id=kwargs.pop("call_id", f"call_{uuid.uuid4().hex[:10]}"),
                kind=kind, name=name, status=status, **kwargs,
            )
            ctx.calls.append(rec)
            ctx.calls = ctx.calls[-500:]
            return rec

    def find_call(self, session_id: str, task_id: str, idempotency_key: str) -> CallRecord | None:
        if not idempotency_key:
            return None
        with self._lock:
            for rec in reversed(self._get(session_id, task_id).calls):
                if rec.idempotency_key == idempotency_key and rec.status == "success":
                    return rec
        return None

    def put_artifact(self, session_id: str, task_id: str, artifact_id: str, content: str) -> str:
        with self._lock:
            ctx = self._get(session_id, task_id)
            ctx.artifacts[artifact_id] = content
            if self._root:
                target = self._root / session_id / task_id
                target.mkdir(parents=True, exist_ok=True)
                (target / artifact_id).write_text(content, encoding="utf-8")
            return artifact_id

    def get_artifact(self, session_id: str, task_id: str, artifact_id: str) -> str:
        with self._lock:
            ctx = self._get(session_id, task_id)
            if artifact_id in ctx.artifacts:
                return ctx.artifacts[artifact_id]
        if self._root:
            p = self._root / session_id / task_id / artifact_id
            if p.exists():
                return p.read_text(encoding="utf-8")
        raise KeyError(artifact_id)

    def snapshot(self, session_id: str, task_id: str) -> None:
        if not self._root:
            return
        with self._lock:
            target = self._root / session_id / task_id / "context.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(self.get(session_id, task_id), ensure_ascii=False, indent=2), encoding="utf-8")



_STORE: AnimationContextStore | None = None


def get_context_store() -> AnimationContextStore:
    global _STORE
    if _STORE is None:
        _STORE = AnimationContextStore()
    return _STORE
