"""三层记忆（modern-agent PRD §2.5 / §5.5 M4）。

为什么要分三层：一次视频创作里，**不同寿命的信息混在一起**是长任务退化的主因。

- **working**（工作记忆）：本次 run 的即时上下文，随 run 结束蒸发；
- **episodic**（情景记忆）：这个任务发生过什么（决策、失败、派了谁、结论），
  只追加、可回放，是"为什么走到这一步"的唯一证据；
- **semantic**（语义记忆）：跨任务可复用的稳定事实（风格偏好、素材库惯例、
  用户口径），带**来源**（provenance）、**过期时间**（TTL）与**撤回**（retract）。

三条纪律（PRD §2.5）：

1. 任何写进 semantic 的东西必须带 provenance——没有来源的"知识"是幻觉的温床；
2. semantic 必须有 TTL 与撤回路径，否则错误事实会永久污染后续任务；
3. episodic 只追加、不改写；`recall` 命中什么必须记录成 `context_loaded` 证据。
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from clipwright.config import logger

#: 语义记忆默认寿命（秒）：30 天。过期的条目 recall 不再返回，但仍可审计。
DEFAULT_TTL_SEC = 30 * 24 * 3600
#: 单任务 episodic 上限（条）；超出只保留最近的（append-only 落盘仍在）
_MAX_EPISODIC = 800
_MAX_WORKING = 400


def _now() -> float:
    return time.time()


def _tokens(text: str) -> set[str]:
    """极简检索分词：中英混排按非字母数字切，中文按二元组补充。

    不引入 embedding（离线、可解释、零成本）；语义检索的质量由 provenance
    与 TTL 兜底，而不是靠一个黑盒向量。
    """
    text = (text or "").lower()
    words = {w for w in re.split(r"[^0-9a-z\u4e00-\u9fff]+", text) if w}
    cjk = "".join(re.findall(r"[\u4e00-\u9fff]", text))
    words |= {cjk[i:i + 2] for i in range(len(cjk) - 1)}
    return words


# ── 数据结构 ────────────────────────────────────────────


@dataclass
class MemoryEntry:
    id: str
    layer: str                      # working / episodic / semantic
    task_id: str
    text: str
    kind: str = ""                  # decision / outcome / failure / preference / fact
    provenance: dict[str, Any] = field(default_factory=dict)   # {source, run_id, ref}
    tags: list[str] = field(default_factory=list)
    pinned: bool = False
    created_at: float = field(default_factory=_now)
    expires_at: Optional[float] = None
    retracted: bool = False

    def expired(self, now: Optional[float] = None) -> bool:
        if self.expires_at is None:
            return False
        return (now or _now()) >= self.expires_at

    def alive(self, now: Optional[float] = None) -> bool:
        return not self.retracted and not self.expired(now)

    def to_public(self) -> dict[str, Any]:
        return {
            "id": self.id, "layer": self.layer, "task_id": self.task_id,
            "text": self.text[:1200], "kind": self.kind, "tags": list(self.tags),
            "pinned": self.pinned, "provenance": dict(self.provenance),
            "created_at": self.created_at, "expires_at": self.expires_at,
            "retracted": self.retracted,
        }


# ── 存储 ────────────────────────────────────────────────

_WORKING: dict[str, list[MemoryEntry]] = {}
_EPISODIC: dict[str, list[MemoryEntry]] = {}
_SEMANTIC: list[MemoryEntry] = []


def reset() -> None:
    _WORKING.clear()
    _EPISODIC.clear()
    _SEMANTIC.clear()
    _DUMPED.clear()


def _new(layer: str, task_id: str, text: str, *, kind: str = "",
         provenance: Optional[dict[str, Any]] = None,
         tags: Optional[Iterable[str]] = None,
         pinned: bool = False,
         ttl_sec: Optional[float] = None) -> MemoryEntry:
    entry = MemoryEntry(
        id=f"mem_{uuid.uuid4().hex[:10]}",
        layer=layer,
        task_id=task_id,
        text=str(text or "").strip(),
        kind=kind,
        provenance=dict(provenance or {}),
        tags=[str(t) for t in (tags or [])],
        pinned=pinned,
        expires_at=(_now() + float(ttl_sec)) if ttl_sec else (
            _now() + DEFAULT_TTL_SEC if layer == "semantic" else None),
    )
    if layer == "working":
        bucket = _WORKING.setdefault(task_id, [])
        bucket.append(entry)
        if len(bucket) > _MAX_WORKING:
            _WORKING[task_id] = bucket[-_MAX_WORKING:]
    elif layer == "episodic":
        bucket = _EPISODIC.setdefault(task_id, [])
        bucket.append(entry)
        if len(bucket) > _MAX_EPISODIC:
            _EPISODIC[task_id] = bucket[-_MAX_EPISODIC:]
    else:
        if not entry.provenance.get("source"):
            # 没有来源的语义事实 = 幻觉温床：降级成 working，不进长期记忆
            entry.layer = "working"
            _WORKING.setdefault(task_id, []).append(entry)
            return entry
        _SEMANTIC.append(entry)
    return entry


def remember(text: str, *, task_id: str = "", layer: str = "working",
             kind: str = "", provenance: Optional[dict[str, Any]] = None,
             tags: Optional[Iterable[str]] = None, pinned: bool = False,
             ttl_sec: Optional[float] = None) -> dict[str, Any]:
    """写一条记忆（layer = working / episodic / semantic）。"""
    if layer not in ("working", "episodic", "semantic"):
        raise ValueError(f"未知记忆层: {layer}")
    if not str(text or "").strip():
        raise ValueError("记忆内容不能为空")
    return _new(layer, task_id, text, kind=kind, provenance=provenance, tags=tags,
                pinned=pinned, ttl_sec=ttl_sec).to_public()


def entries(*, task_id: str = "", layer: str = "", k: int = 20,
            include_expired: bool = False) -> list[dict[str, Any]]:
    """按层列出条目（最新在前）。

    与 `recall` 的区别：`recall` 需要**查询词**，空查询按定义返回空——
    于是"把最近的情景记忆打进 replay 包"这种需求用 recall 会永远拿到 `[]`
    （旧 `evaluation.replay_bundle` 就踩了这个坑）。列条目是另一种操作，别混。
    """
    pool: list[MemoryEntry] = []
    if layer in ("", "working"):
        pool.extend(_WORKING.get(task_id, []) if task_id else
                    [e for b in _WORKING.values() for e in b])
    if layer in ("", "episodic"):
        pool.extend(_EPISODIC.get(task_id, []) if task_id else
                    [e for b in _EPISODIC.values() for e in b])
    if layer in ("", "semantic"):
        pool.extend(_SEMANTIC)
    if not include_expired:
        pool = [e for e in pool if e.alive()]
    pool.sort(key=lambda e: -e.created_at)
    return [e.to_public() for e in pool[:k]]


def recall(query: str, *, task_id: str = "", layers: Iterable[str] = ("working", "episodic",
                                                                     "semantic"),
             k: int = 6, include_expired: bool = False) -> list[dict[str, Any]]:
    """按关键词重合度召回，返回带 score 的条目（供 context_loader 记证据）。"""
    q = _tokens(query)
    if not q:
        return []
    pool: list[MemoryEntry] = []
    for layer in layers:
        if layer == "working" and task_id:
            pool.extend(_WORKING.get(task_id, []))
        elif layer == "episodic" and task_id:
            pool.extend(_EPISODIC.get(task_id, []))
        elif layer == "semantic":
            pool.extend(_SEMANTIC)
    scored: list[tuple[float, MemoryEntry]] = []
    for entry in pool:
        if not include_expired and not entry.alive():
            continue
        tokens = _tokens(entry.text) | set(entry.tags)
        if not tokens:
            continue
        overlap = len(q & tokens)
        if not overlap:
            continue
        score = overlap / max(1, len(q))
        if entry.pinned:
            score += 0.15
        scored.append((score, entry))
    scored.sort(key=lambda x: (-x[0], -x[1].created_at))
    return [{**e.to_public(), "score": round(s, 4)} for s, e in scored[:k]]


def forget(entry_id: str, *, reason: str = "") -> bool:
    """撤回一条记忆（semantic 撤回后 recall 不再返回，但审计可见）。"""
    for entry in _SEMANTIC:
        if entry.id == entry_id:
            entry.retracted = True
            entry.provenance["retracted_reason"] = reason[:300]
            return True
    for bucket in list(_WORKING.values()) + list(_EPISODIC.values()):
        for entry in bucket:
            if entry.id == entry_id:
                entry.retracted = True
                entry.provenance["retracted_reason"] = reason[:300]
                return True
    return False


def pin(entry_id: str, pinned: bool = True) -> bool:
    """钉住/取消钉住：钉住的条目在压缩时不会被丢。"""
    for bucket in list(_WORKING.values()) + list(_EPISODIC.values()):
        for entry in bucket:
            if entry.id == entry_id:
                entry.pinned = pinned
                return True
    for entry in _SEMANTIC:
        if entry.id == entry_id:
            entry.pinned = pinned
            return True
    return False


def compact(task_id: str, *, budget_chars: int = 2400) -> str:
    """把工作记忆压成一段文本（长任务压缩，PRD §5.5）。

    顺序：钉住的 → 最新的；只压到预算内，并明确标出"被省略多少条"，
    避免模型以为看到的就是全部。episodic 不受压缩影响（它是证据链）。
    """
    bucket = list(_WORKING.get(task_id, []))
    alive = [e for e in bucket if e.alive()]
    ordered = sorted(alive, key=lambda e: (not e.pinned, -e.created_at))
    out: list[str] = []
    used = 0
    dropped = 0
    for entry in ordered:
        line = f"- [{entry.kind or entry.layer}] {entry.text.strip()}"
        if used + len(line) > budget_chars:
            dropped += 1
            continue
        out.append(line)
        used += len(line)
    if dropped:
        out.append(f"- （另有 {dropped} 条工作记忆因预算被省略）")
    return "\n".join(out)


def describe(task_id: str = "") -> dict[str, Any]:
    """记忆体量概览（前端/诊断用）。"""
    def _live(items: Iterable[MemoryEntry]) -> int:
        return sum(1 for e in items if e.alive())

    work = _WORKING.get(task_id, []) if task_id else [
        e for bucket in _WORKING.values() for e in bucket]
    epi = _EPISODIC.get(task_id, []) if task_id else [
        e for bucket in _EPISODIC.values() for e in bucket]
    return {
        "task_id": task_id,
        "working": {"total": len(work), "alive": _live(work)},
        "episodic": {"total": len(epi), "alive": _live(epi)},
        "semantic": {"total": len(_SEMANTIC), "alive": _live(_SEMANTIC)},
        "pinned": sum(1 for e in list(work) + list(epi) + _SEMANTIC if e.pinned),
        "retracted": sum(1 for e in list(work) + list(epi) + _SEMANTIC if e.retracted),
    }


# ── 落盘（episodic append-only / semantic 快照）──────────────


#: 已落盘的 episodic 条数（每任务一个游标）——避免同一批条目被重复追加
_DUMPED: dict[str, int] = {}


def dump_episodic(task_id: str, root: str | Path) -> Optional[str]:
    """把某任务**新增**的 episodic 追加落盘（append-only，便于事后 replay）。

    游标 `_DUMPED[task_id]` 记住已经写过多少条：旧实现每次把整表再追加一遍，
    调用两次就出现两份同样的证据，replay 时会当成"发生过两次"。
    """
    items = _EPISODIC.get(task_id, [])
    start = _DUMPED.get(task_id, 0)
    fresh = items[start:]
    if not fresh:
        return None
    try:
        path = Path(root) / "memory" / "episodic.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            for entry in fresh:
                fh.write(json.dumps(entry.to_public(), ensure_ascii=False) + "\n")
        _DUMPED[task_id] = len(items)
        return str(path)
    except Exception as exc:  # noqa: BLE001 — 落盘失败不能带崩主循环
        logger.warning("episodic 落盘失败 %s: %s", task_id, exc)
        return None


def dump_semantic(root: str | Path) -> Optional[str]:
    """把 semantic 快照整体写盘（唯一可整体覆盖的一层：它有 TTL 与撤回）。"""
    try:
        path = Path(root) / "memory" / "semantic.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps([e.to_public() for e in _SEMANTIC], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return str(path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("semantic 落盘失败: %s", exc)
        return None


def load_episodic(root: str | Path, task_id: str) -> int:
    """从 `<root>/memory/episodic.jsonl` 恢复**该任务**的情景记忆。

    与 semantic 不同，episodic 是追加日志：按 count 逐行读，恢复后把游标
    `_DUMPED[task_id]` 对齐到已读条数，避免下���次 dump 把同一批再写一遍。
    """
    path = Path(root) / "memory" / "episodic.jsonl"
    if not path.exists():
        return 0
    known = {e.id for e in _EPISODIC.get(task_id, [])}
    added = 0
    total = 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception as exc:  # noqa: BLE001 — 坏文件要报出来，但不阻断启动
        logger.warning("episodic 恢复失败 %s: %s", path, exc)
        return 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except Exception:  # noqa: BLE001 — 单行坏数据跳过
            continue
        if not isinstance(item, dict) or str(item.get("task_id") or "") != task_id:
            continue
        total += 1
        if str(item.get("id") or "") in known:
            continue
        try:
            entry = MemoryEntry(
                id=str(item.get("id") or f"mem_{uuid.uuid4().hex[:10]}"),
                layer="episodic",
                task_id=task_id,
                text=str(item.get("text") or ""),
                kind=str(item.get("kind") or ""),
                provenance=dict(item.get("provenance") or {}),
                tags=[str(t) for t in (item.get("tags") or [])],
                pinned=bool(item.get("pinned")),
                created_at=float(item.get("created_at") or _now()),
                expires_at=item.get("expires_at"),
                retracted=bool(item.get("retracted")),
            )
        except Exception:  # noqa: BLE001
            continue
        if not entry.text:
            continue
        _EPISODIC.setdefault(task_id, []).append(entry)
        added += 1
    _DUMPED[task_id] = total
    return added


def load(root: str | Path, *, task_id: str = "") -> dict[str, int]:
    """启动时恢复记忆（**唯一入口**，别在各处零散调）。

    语义层全量恢复；情景层按任务恢复（给了 task_id 才恢复）。
    返回值给日志/诊断用：`{"semantic": n, "episodic": m}`。
    """
    stats = {"semantic": 0, "episodic": 0}
    try:
        stats["semantic"] = load_semantic(root)
    except Exception as exc:  # noqa: BLE001
        logger.warning("semantic 恢复异常: %s", exc)
    if task_id:
        try:
            stats["episodic"] = load_episodic(root, task_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("episodic 恢复异常: %s", exc)
    return stats


def load_semantic(root: str | Path) -> int:
    """从 `<root>/memory/semantic.json` 恢复语义记忆（进程重启后仍有效）。

    只写不读等于把"长期记忆"做成了单次会话缓存——语义记忆必须能跨重启，
    否则 TTL / 撤回这些规则都只是在内存里成立。
    """
    path = Path(root) / "memory" / "semantic.json"
    if not path.exists():
        return 0
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — 坏文件要报出来，但不能阻断启动
        logger.warning("semantic 恢复失败 %s: %s", path, exc)
        return 0
    if not isinstance(raw, list):
        return 0
    known = {e.id for e in _SEMANTIC}
    added = 0
    for item in raw:
        if not isinstance(item, dict) or item.get("id") in known:
            continue
        try:
            entry = MemoryEntry(
                id=str(item.get("id") or f"mem_{uuid.uuid4().hex[:10]}"),
                layer="semantic",
                task_id=str(item.get("task_id") or ""),
                text=str(item.get("text") or ""),
                kind=str(item.get("kind") or ""),
                provenance=dict(item.get("provenance") or {}),
                tags=[str(t) for t in (item.get("tags") or [])],
                pinned=bool(item.get("pinned")),
                created_at=float(item.get("created_at") or _now()),
                expires_at=item.get("expires_at"),
                retracted=bool(item.get("retracted")),
            )
        except Exception:  # noqa: BLE001 — 单条坏数据跳过
            continue
        if not entry.text:
            continue
        _SEMANTIC.append(entry)
        added += 1
    return added


__all__ = ["DEFAULT_TTL_SEC", "MemoryEntry", "compact", "describe", "dump_episodic",
           "dump_semantic", "entries", "forget", "load", "load_episodic",
           "load_semantic", "pin", "recall", "remember", "reset"]
