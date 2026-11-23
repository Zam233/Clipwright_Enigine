"""Exec approval/history 持久化（PRD WP4）。

文件布局：
  <workspace>/.exec/approvals.json
  <workspace>/.exec/history.jsonl
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from clipwright.schema.exec import ExecApproval, ExecHistoryItem

_LOCK = threading.RLock()
_SECRET = re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*\S+")


def _root(task: Any) -> Path:
    from clipwright.services.workspace_guard import resolve_workspace_root

    root = resolve_workspace_root(task) / ".exec"
    root.mkdir(parents=True, exist_ok=True)
    return root


def redact(text: str, limit: int = 400) -> str:
    clean = _SECRET.sub(r"\1=***", str(text or ""))
    return clean[:limit]


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name, dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def create_approval(task: Any, approval: ExecApproval) -> dict[str, Any]:
    with _LOCK:
        path = _root(task) / "approvals.json"
        items = _read(path, {})
        items[approval.approval_id] = approval.model_dump(mode="json")
        _write_json(path, items)
        return items[approval.approval_id]


def get_approval(task: Any, approval_id: str) -> dict[str, Any] | None:
    return (_read(_root(task) / "approvals.json", {}) or {}).get(str(approval_id))


def list_pending(task: Any) -> list[dict[str, Any]]:
    now = time.time()
    out: list[dict[str, Any]] = []
    for item in (_read(_root(task) / "approvals.json", {}) or {}).values():
        if item.get("status") != "pending":
            continue
        raw = str(item.get("expires_at") or "")
        try:
            from datetime import datetime

            if raw and datetime.fromisoformat(raw).timestamp() < now:
                continue
        except ValueError:
            pass
        out.append(item)
    return out


def update_approval(task: Any, approval_id: str, **patch: Any) -> dict[str, Any] | None:
    with _LOCK:
        path = _root(task) / "approvals.json"
        items = _read(path, {})
        item = items.get(str(approval_id))
        if item is None:
            return None
        item.update(patch)
        _write_json(path, items)
        return item


def append_history(task: Any, item: ExecHistoryItem) -> None:
    with _LOCK:
        path = _root(task) / "history.jsonl"
        row = item.model_dump(mode="json")
        row["text"] = redact(str(row.get("text") or ""), 2000)
        row["output_tail"] = redact(str(row.get("output_tail") or ""), 400)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def list_history(task: Any, limit: int = 20) -> list[dict[str, Any]]:
    path = _root(task) / "history.jsonl"
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines()[-max(1, limit):]:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        return []
    return rows


def new_ids() -> tuple[str, str]:
    return f"ex_{uuid4().hex[:12]}", f"ap_{uuid4().hex[:12]}"
