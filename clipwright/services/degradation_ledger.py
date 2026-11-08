"""降级处置台账的**任务级持久化**（PRD §4.2 D1′，2026-10-30）。

为什么要持久化：台账原本是进程内两个 dict（`_PENDING` / `_RECEIPTS`），服务一重启，
「哪些降级还没处置、之前凭什么接受」就全丢了——而主 agent 正是靠这份台账向用户解释
与决定要不要返工。丢台账 = 重启后无法解释、也无法守门。

存储：`<workspace>/reports/degradation_ledger.json`（一个任务一份），结构：

    {"task_id": ..., "pending": [...], "receipts": [...], "updated_at": ...}

三条纪律：
1. **懒加载**：第一次访问某任务时读盘（`_load`），之后走内存，写操作后立刻回写；
2. **读失败不抛**：坏 JSON / 无权限只记日志，按"空台账"继续（宁可少一份历史，也不能让任务挂掉）；
3. **不改变语义**：`register/accept/mark_remediated/outstanding/summary` 的行为与内存版完全一致，
   只有"重启后还能读回来"这一条不同。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from clipwright.config import logger

#: 任务 → 待处置降级
_PENDING: dict[str, list[dict[str, Any]]] = {}
#: 任务 → 收据列表
_RECEIPTS: dict[str, list[dict[str, Any]]] = {}
#: 已从磁盘加载过的任务（避免同进程反复读；`clear()` 会清掉）
_LOADED: set[str] = set()
#: 持久化文件名
LEDGER_NAME = "degradation_ledger.json"


@dataclass
class Disposition:
    kind: str
    scope: str = ""
    mode: str = ""          # remediated / accepted
    reason: str = ""
    impact: str = ""
    at: float = field(default_factory=time.time)

    def to_public(self) -> dict[str, Any]:
        return {"kind": self.kind, "scope": self.scope, "mode": self.mode,
                "reason": self.reason, "impact": self.impact,
                "at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(self.at))}


# ── 持久化 ────────────────────────────────────────────────────


def _path(task_id: str) -> Path | None:
    try:
        from clipwright.services.workspace import Workspace

        root = Workspace(str(task_id)).dirs().root
        path = Path(root) / "reports" / LEDGER_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    except Exception as exc:  # noqa: BLE001 — 工作区不可用时退化为纯内存
        logger.warning("降级台账路径不可用 %s: %s", task_id, exc)
        return None


def _flush(task_id: str) -> None:
    path = _path(task_id)
    if path is None:
        return
    payload = {
        "task_id": str(task_id),
        "pending": list(_PENDING.get(str(task_id)) or []),
        "receipts": list(_RECEIPTS.get(str(task_id)) or []),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("降级台账落盘失败 %s: %s", path, exc)


def _load(task_id: str) -> None:
    """首次访问读盘；坏文件按空台账处理（不抛）。"""
    tid = str(task_id)
    if tid in _LOADED:
        return
    _LOADED.add(tid)
    if tid in _PENDING or tid in _RECEIPTS:
        return
    path = _path(tid)
    if path is None or not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — 坏台账不该让任务挂掉
        logger.warning("降级台账读取失败（按空台账继续）%s: %s", path, exc)
        return
    pend = [d for d in (data.get("pending") or []) if isinstance(d, dict)]
    recs = [d for d in (data.get("receipts") or []) if isinstance(d, dict)]
    if pend:
        _PENDING[tid] = pend
    if recs:
        _RECEIPTS[tid] = recs


def _ensure(task_id: str) -> str:
    tid = str(task_id)
    _load(tid)
    return tid


# ── 对外 API（语义与内存版一致） ───────────────────────────────


def pending(task_id: str) -> list[dict[str, Any]]:
    return list(_PENDING.get(_ensure(task_id)) or [])


#: **不需要收据**的降级（信息型 / 已自愈）。它们必须照实上报、照实进事件流，
#: 但拿它去拦任务会在"片其实已经能出"的时候把任务判失败：
#: - `llm_json_*`：解析失败后已经重试/兜底，产物本身没问题；
#: - `mg_critique_skipped` / `mg_motion_gate_accepted` / `text_repair`：自检环节的取舍记录；
#: - `web_search_unavailable`：外部 provider 没配，属于环境事实。
#: - `ask_user_timeout_default`：用户没答、按默认项继续，本身就是"已知取舍"。
#: 影响成片的（占位画面、弱动效、工具失败率、报告落盘失败…）仍然必须给收据。
RECEIPT_EXEMPT_KINDS: frozenset[str] = frozenset({
    "llm_json_invalid", "llm_json_truncated", "llm_json_empty",
    "mg_critique_skipped", "mg_motion_gate_accepted", "text_repair",
    "web_search_unavailable", "ask_user_timeout_default",
})


def needs_receipt(item: dict[str, Any]) -> bool:
    """这条降级是否需要 remediated/accepted 收据。"""
    return str(item.get("kind") or "") not in RECEIPT_EXEMPT_KINDS


def register(task_id: str, items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """登记一批降级（按 (kind, scope) 去重），返回真正新增的条目。"""
    tid = _ensure(task_id)
    bucket = _PENDING.setdefault(tid, [])
    known = {(str(d.get("kind")), str(d.get("scope"))) for d in bucket}
    fresh: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        key = (str(item.get("kind")), str(item.get("scope")))
        if key in known:
            continue
        known.add(key)
        bucket.append(dict(item))
        fresh.append(dict(item))
    if fresh:
        _flush(tid)
    return fresh


def _add_receipt(task_id: str, disp: Disposition) -> None:
    _RECEIPTS.setdefault(str(task_id), []).append(disp.to_public())


def mark_remediated(task_id: str, stage: str) -> int:
    """重派某阶段成功 → 把该阶段相关降级标记为已修复。返回标记条数。"""
    tid = _ensure(task_id)
    bucket = _PENDING.get(tid) or []
    hit = [d for d in bucket if str(d.get("scope")) == str(stage)]
    for item in hit:
        _add_receipt(tid, Disposition(
            kind=str(item.get("kind")), scope=str(stage), mode="remediated",
            reason=f"重派 {stage} 阶段并成功",
        ))
    if hit:
        keep = [d for d in bucket if str(d.get("scope")) != str(stage)]
        if keep:
            _PENDING[tid] = keep
        else:
            _PENDING.pop(tid, None)
        _flush(tid)
    return len(hit)


def accept(task_id: str, kinds: Iterable[str], *, reason: str, impact: str = "") -> int:
    """显式接受：给这些 kind 写 `accepted` 收据并移出待处置。返回条数。"""
    tid = _ensure(task_id)
    wanted = {str(k) for k in kinds if str(k).strip()}
    if not wanted:
        return 0
    bucket = _PENDING.get(tid) or []
    hit = [d for d in bucket if str(d.get("kind")) in wanted]
    for item in hit:
        _add_receipt(tid, Disposition(
            kind=str(item.get("kind")), scope=str(item.get("scope") or ""),
            mode="accepted", reason=reason, impact=impact,
        ))
    if hit:
        keep = [d for d in bucket if str(d.get("kind")) not in wanted]
        if keep:
            _PENDING[tid] = keep
        else:
            _PENDING.pop(tid, None)
        _flush(tid)
    return len(hit)


def receipts(task_id: str) -> list[dict[str, Any]]:
    return list(_RECEIPTS.get(_ensure(task_id)) or [])


def outstanding(task_id: str) -> list[dict[str, Any]]:
    """还没收据、且**需要**收据的降级（= 未处置）。

    信息型降级（见 `RECEIPT_EXEMPT_KINDS`）留在台账里可查，但不参与收尾守门。
    """
    return [d for d in (_PENDING.get(_ensure(task_id)) or []) if needs_receipt(d)]


def summary(task_id: str) -> dict[str, Any]:
    tid = _ensure(task_id)
    return {
        "pending": len(outstanding(tid)),
        "receipts": len(receipts(tid)),
        "kinds": sorted({str(d.get("kind")) for d in outstanding(tid)}),
    }


def _remove_file(task_id: str) -> None:
    path = _path(task_id)
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("降级台账删除失败 %s: %s", path, exc)


def forget_all_persisted() -> int:
    """删掉工作区里所有台账文件（**测试隔离 / 全局重置**用，不参与业务）。

    为什么要它：`clear()` 只知道内存里的 task_id，删不掉"上次运行遗留"的文件；
    测试复用短 id（如 `t`）时会读到陈旧台账，表现为"刚 register 就报重复"。
    """
    removed = 0
    try:
        from clipwright.services.workspace import workspace_root

        for path in Path(workspace_root()).glob(f"*/reports/{LEDGER_NAME}"):
            try:
                path.unlink(missing_ok=True)
                removed += 1
            except Exception:  # noqa: BLE001
                continue
    except Exception as exc:  # noqa: BLE001 — 清扫失败不影响业务
        logger.warning("清扫降级台账文件失败: %s", exc)
    return removed


def clear(task_id: str = "") -> None:
    """清台账 = **忘掉**这个任务的降级历史（内存 + 磁盘）。

    注意与"标记已处置"的区别：处置走 `accept` / `mark_remediated`（会留收据）；
    `clear` 是测试隔离与"用户主动重置"用的，清了就真的不再有这条历史。
    """
    if task_id:
        tid = str(task_id)
        _PENDING.pop(tid, None)
        _RECEIPTS.pop(tid, None)
        _LOADED.discard(tid)
        _remove_file(tid)
    else:
        for tid in set(_PENDING) | set(_RECEIPTS) | set(_LOADED):
            _remove_file(tid)
        _PENDING.clear()
        _RECEIPTS.clear()
        _LOADED.clear()
        forget_all_persisted()


__all__ = [
    "LEDGER_NAME", "RECEIPT_EXEMPT_KINDS", "accept", "clear", "forget_all_persisted",
    "needs_receipt",
    "mark_remediated", "outstanding", "pending", "receipts", "register", "summary",
]
