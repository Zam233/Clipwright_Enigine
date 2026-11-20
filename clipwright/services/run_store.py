"""RunStore：`AgentResult` 的持久化事实源（Coding Plan B04）。

- **唯一真相**：磁盘上的 run JSON；`_MEM` 只是热缓存，重启后按需读盘。
- **task scope**：`run_id` 与 `idem_key` 都按 task 隔离，跨任务同 idem 不互相命中。
- **attempt 原子分配**：用 `O_CREAT|O_EXCL` 标记文件抢占序号，进程崩溃不会重号。
- **无工作区时降级为内存**：单测/脚本直调仍能跑，但不承诺重启恢复。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from clipwright.config import logger
from clipwright.schema.run import AgentResult

_MAX_MEM = 800
_MEM: dict[str, AgentResult] = {}
_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def _safe(name: str, limit: int = 96) -> str:
    raw = str(name or "")
    cleaned = _SAFE.sub("_", raw).strip("._") or "run"
    if cleaned == raw and len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[:limit]}-{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:8]}"


def _runs_dir(task_id: str) -> Path | None:
    """任务工作区的 `runs/` 目录；拿不到工作区返回 None（内存模式）。"""
    task_id = str(task_id or "").strip()
    if not task_id:
        return None
    try:
        from clipwright.services.workspace import Workspace

        root = Workspace(task_id).dirs().root / "runs"
        root.mkdir(parents=True, exist_ok=True)
        return root
    except Exception as exc:  # noqa: BLE001 — 无工作区不是错误，降级内存
        logger.debug("RunStore 无工作区（%s）：%s", task_id, exc)
        return None


def _path_of(task_id: str, run_id: str) -> Path | None:
    base = _runs_dir(task_id)
    if base is None:
        return None
    return base / f"{_safe(run_id)}.json"


def save(result: AgentResult) -> None:
    """写内存 + 磁盘（原子替换）；磁盘失败只记日志，不阻断业务。"""
    _MEM[result.run_id] = result
    if len(_MEM) > _MAX_MEM:
        _MEM.pop(next(iter(_MEM)))
    path = _path_of(result.task_id, result.run_id)
    if path is None:
        return
    try:
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(result.model_dump_json(), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        logger.warning("RunStore 写入失败 %s: %s", path, exc)


def _load(path: Path) -> AgentResult | None:
    try:
        return AgentResult.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — 坏文件跳过，不影响其余 run
        logger.warning("RunStore 读取失败 %s", path)
        return None


def get(run_id: str) -> AgentResult | None:
    hit = _MEM.get(str(run_id or ""))
    if hit is not None:
        return hit
    rid = str(run_id or "")
    # 无 task_id 时只能靠内存；list_task 会带 task 扫描磁盘
    return None


def get_for_task(task_id: str, run_id: str) -> AgentResult | None:
    rid = str(run_id or "")
    if not rid:
        return None
    hit = _MEM.get(rid)
    if hit is not None:
        return hit if str(hit.task_id) == str(task_id) else None
    path = _path_of(str(task_id), rid)
    if path is None or not path.is_file():
        return None
    loaded = _load(path)
    if loaded is None:
        return None
    if str(loaded.task_id) != str(task_id):
        return None
    _MEM[loaded.run_id] = loaded
    return loaded


def list_task(task_id: str) -> list[AgentResult]:
    key = str(task_id or "")
    out: dict[str, AgentResult] = {
        r.run_id: r for r in _MEM.values() if str(r.task_id) == key
    }
    base = _runs_dir(key)
    if base is not None:
        for path in base.glob("*.json"):
            name = path.stem
            if name in out:
                continue
            loaded = _load(path)
            if loaded is None or str(loaded.task_id) != key:
                continue
            out[loaded.run_id] = loaded
            if len(_MEM) < _MAX_MEM:
                _MEM[loaded.run_id] = loaded
    return sorted(out.values(), key=lambda r: (float(r.created_at or 0), r.run_id))


# ── 幂等：task + idem_key → run_id ──────────────────────────────────────


def _idem_path(task_id: str, idem_key: str) -> Path | None:
    base = _runs_dir(task_id)
    if base is None:
        return None
    digest = hashlib.sha1(f"{task_id}\n{idem_key}".encode("utf-8")).hexdigest()
    folder = base / "_idem"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{digest}.json"


def bind_idem(task_id: str, idem_key: str, run_id: str) -> None:
    key = str(idem_key or "").strip()
    if not key or not run_id:
        return
    path = _idem_path(str(task_id), key)
    if path is None:
        return
    try:
        path.write_text(json.dumps({"task_id": str(task_id), "idem_key": key,
                                    "run_id": str(run_id)}, ensure_ascii=False),
                        encoding="utf-8")
    except OSError as exc:
        logger.warning("RunStore 幂等索引写入失败: %s", exc)


def lookup_idem(task_id: str, idem_key: str) -> AgentResult | None:
    key = str(idem_key or "").strip()
    if not key:
        return None
    path = _idem_path(str(task_id), key)
    if path is None or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return get_for_task(str(task_id), str(data.get("run_id") or ""))


# ── attempt：task + stage 的原子序号 ────────────────────────────────────


def next_attempt(task_id: str, stage: str) -> int:
    """该 task/stage 的下一个 attempt（1 起）。标记文件独占创建，崩溃不重号。"""
    task_id, stage = str(task_id or ""), str(stage or "")
    if not task_id or not stage:
        return 1
    base = _runs_dir(task_id)
    mem_max = max([int(r.attempt or 1) for r in _MEM.values()
                   if str(r.task_id) == task_id and str(r.stage) == stage] or [0])
    if base is None:
        return mem_max + 1
    digest = hashlib.sha1(f"{task_id}\n{stage}".encode("utf-8")).hexdigest()[:12]
    folder = base / "_attempt"
    folder.mkdir(parents=True, exist_ok=True)
    best = max(mem_max, 0)
    for marker in folder.glob(f"{digest}-*.seq"):
        try:
            best = max(best, int(marker.stem.rsplit("-", 1)[-1]))
        except ValueError:
            continue
    candidate = best + 1
    for _ in range(50):
        marker = folder / f"{digest}-{candidate}.seq"
        try:
            fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return candidate
        except FileExistsError:
            candidate += 1
        except OSError:
            return candidate
    return candidate


def clear(task_id: str = "", *, memory_only: bool = False) -> None:
    """清缓存（测试用）。`memory_only=True` 模拟重启：磁盘保留、内存清空。"""
    key = str(task_id or "")
    if key:
        for rid in [rid for rid, r in _MEM.items() if str(r.task_id) == key]:
            _MEM.pop(rid, None)
        if memory_only:
            return
        base = _runs_dir(key)
        if base is not None:
            for path in list(base.glob("*.json")) + list(base.glob("_idem/*.json")) \
                    + list(base.glob("_attempt/*.seq")):
                try:
                    path.unlink()
                except OSError:
                    pass
        return
    _MEM.clear()
    # 无 task_id 时不扫全局磁盘（工作区可能被多个任务共享，避免误删他人 run）；
    # 需要删盘时按 task_id 调用。


__all__ = [
    "list_task",
    "bind_idem",
    "clear",
    "get",
    "get_for_task",
    "lookup_idem",
    "next_attempt",
    "save",
]
