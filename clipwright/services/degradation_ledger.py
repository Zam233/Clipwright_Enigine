"""降级处置台账（PRD modern-agent-review P0：处置必须有**机器可验证的收据**）。

问题现场：Kernel 的降级守门只会"再推一轮"，模型口头说一句「接受」就能收尾，
`TaskFinish.unfinished` 虽然记了降级类型，任务仍然是 `done`。于是"降级被处置"
这件事无法验证。

本台账把处置变成结构化事实，只有两种**合法**收据：
- `remediated`：重派同一阶段并跑成功（`mark_remediated(task, stage)`）；
- `accepted`：显式调用 `acknowledge_degradation(kinds=[...], reason=..., impact=...)`
  （reason 必须给出真实理由，≥10 字）。

`outstanding()` 返回既没收据也没被重派修复的降级；Kernel 收尾时按
`settings.agent_degradation_policy`（`require_receipt` 默认 / `warn`）决定是否判失败。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterable

#: 任务 → 待处置降级
_PENDING: dict[str, list[dict[str, Any]]] = {}
#: 任务 → 收据列表
_RECEIPTS: dict[str, list[dict[str, Any]]] = {}


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


def pending(task_id: str) -> list[dict[str, Any]]:
    return list(_PENDING.get(str(task_id)) or [])


#: **不需要收据**的降级（信息型 / 已自愈）。它们必须照实上报、照实进事件流，
#: 但拿它去拦任务会在"片其实已经能出"的时候把任务判失败：
#: - `llm_json_*`：解析失败后已经重试/兜底，产物本身没问题；
#: - `mg_critique_skipped` / `mg_motion_gate_accepted` / `text_repair`：自检环节的取舍记录；
#: - `web_search_unavailable`：外部provider没配，属于环境事实。
#: 影响成片的（占位画面、弱动效、工具失败率、报告落盘失败…）仍然必须给收据。
RECEIPT_EXEMPT_KINDS: frozenset[str] = frozenset({
    "llm_json_invalid", "llm_json_truncated", "llm_json_empty",
    "mg_critique_skipped", "mg_motion_gate_accepted", "text_repair",
    "web_search_unavailable",
})


def needs_receipt(item: dict[str, Any]) -> bool:
    """这条降级是否需要 remediated/accepted 收据。"""
    return str(item.get("kind") or "") not in RECEIPT_EXEMPT_KINDS


def register(task_id: str, items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """登记一批降级（按 (kind, scope) 去重），返回真正新增的条目。"""
    tid = str(task_id)
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
    return fresh


def _add_receipt(task_id: str, disp: Disposition) -> None:
    _RECEIPTS.setdefault(str(task_id), []).append(disp.to_public())


def mark_remediated(task_id: str, stage: str) -> int:
    """重派某阶段成功 → 把该阶段相关降级标记为已修复。返回标记条数。"""
    tid = str(task_id)
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
    return len(hit)


def accept(task_id: str, kinds: Iterable[str], *, reason: str, impact: str = "") -> int:
    """显式接受：给这些 kind 写 `accepted` 收据并移出待处置。返回条数。"""
    tid = str(task_id)
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
    return len(hit)


def receipts(task_id: str) -> list[dict[str, Any]]:
    return list(_RECEIPTS.get(str(task_id)) or [])


def outstanding(task_id: str) -> list[dict[str, Any]]:
    """还没收据、且**需要**收据的降级（= 未处置）。

    信息型降级（见 `RECEIPT_EXEMPT_KINDS`）留在台账里可查，但不参与收尾守门。
    """
    return [d for d in (_PENDING.get(str(task_id)) or []) if needs_receipt(d)]


def summary(task_id: str) -> dict[str, Any]:
    return {
        "pending": len(outstanding(task_id)),
        "receipts": len(receipts(task_id)),
        "kinds": sorted({str(d.get("kind")) for d in outstanding(task_id)}),
    }


def clear(task_id: str = "") -> None:
    if task_id:
        _PENDING.pop(str(task_id), None)
        _RECEIPTS.pop(str(task_id), None)
    else:
        _PENDING.clear()
        _RECEIPTS.clear()


__all__ = [
    "RECEIPT_EXEMPT_KINDS", "accept", "clear", "needs_receipt", "mark_remediated", "outstanding", "pending",
    "receipts", "register", "summary",
]
