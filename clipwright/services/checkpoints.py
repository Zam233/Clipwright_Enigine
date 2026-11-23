"""阶段检查点读写（按工作区 + 阶段落盘，原子写）。"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from clipwright.schema.checkpoint import StageCheckpoint

_SCHEMA_VERSION = 1
_MAX_OUTPUTS = 2000


def _path(workspace_root: str | Path, stage: str) -> Path:
    return Path(workspace_root) / "checkpoints" / f"{stage}.json"


def digest(value: Any) -> str:
    """稳定输入摘要；调用方只传阶段输入（不要带输出键）。"""
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load(workspace_root: str | Path, stage: str, input_digest: str) -> StageCheckpoint | None:
    path = _path(workspace_root, stage)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("schema_version") != _SCHEMA_VERSION:
        return None
    if str(data.get("input_digest") or "") != str(input_digest):
        return None
    try:
        return StageCheckpoint(**data)
    except Exception:
        return None


def save(workspace_root: str | Path, checkpoint: StageCheckpoint) -> None:
    path = _path(workspace_root, checkpoint.stage)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = checkpoint.model_dump(mode="json")
    payload["schema_version"] = _SCHEMA_VERSION
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    payload["partial_outputs"] = list(payload.get("partial_outputs") or [])[-_MAX_OUTPUTS:]
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(raw)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


__all__ = ["digest", "load", "save"]
