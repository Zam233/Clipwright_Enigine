"""终态任务只读复盘快照。

R10 的第一层：**先提供事实**，不启动生产 tool loop、不派发阶段、不写黑板。
Agent 的“工具是否趁手”访谈可以在这份快照之上单独做（只读会话），
但绝不能把“复盘”当成“继续任务”。
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from clipwright.config import logger
from clipwright.services import run_store, task_blackboard, task_events


def _report_files(task: Any) -> list[dict[str, Any]]:
    try:
        extra = dict(getattr(getattr(task, "request", None), "extra", {}) or {})
        root = Path(str(extra.get("workspace_root") or ""))
        base = root / "reports"
        if not base.is_dir():
            return []
        return [
            {"name": p.name, "bytes": p.stat().st_size}
            for p in sorted(base.glob("*.md"))[-30:]
        ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("复盘报告枚举失败: %s", exc)
        return []


def build_review(task: Any) -> dict[str, Any]:
    """只读汇总：任务、runs、事件统计、黑板键、报告。"""
    tid = str(getattr(task, "id", "") or "")
    runs: list[dict[str, Any]] = []
    try:
        for r in run_store.list_task(tid):
            runs.append({
                "stage": str(getattr(r, "stage", "") or ""),
                "role": str(getattr(r, "role", "") or ""),
                "kind": str(getattr(r, "kind", "") or ""),
                "status": str(getattr(r, "status", "") or ""),
                "attempt": int(getattr(r, "attempt", 1) or 1),
                "duration_ms": int(getattr(r, "duration_ms", 0) or 0),
                "tool_calls": int(getattr(r, "tool_calls", 0) or 0),
                "summary": str(getattr(r, "summary", "") or "")[:500],
                "degradations": len(getattr(r, "degradations", []) or []),
                "failure": getattr(r, "failure", None),
            })
    except Exception as exc:  # noqa: BLE001
        logger.warning("复盘 run 读取失败 %s: %s", tid, exc)

    tool_counter: Counter[str] = Counter()
    tool_calls: list[dict[str, Any]] = []
    event_types: Counter[str] = Counter()
    try:
        for e in task_events.events(tid):
            event_types[str(getattr(e, "type", "") or "")] += 1
            detail = dict(getattr(e, "detail", None) or {})
            tool = str(detail.get("tool") or "")
            if tool:
                tool_counter[tool] += 1
                if len(tool_calls) < 200:
                    tool_calls.append({"type": str(getattr(e, "type", "") or ""),
                                       "tool": tool,
                                       "detail": detail})
    except Exception as exc:  # noqa: BLE001
        logger.warning("复盘事件读取失败 %s: %s", tid, exc)

    board = task_blackboard.get(tid) if tid else {}
    return {
        "task_id": tid,
        "read_only": True,
        "task": {
            "status": str(getattr(task, "status", "") or ""),
            "topic": str(getattr(getattr(task, "request", None), "topic", "") or ""),
            "terminal_reason": str(getattr(getattr(task, "terminal_reason", None), "value", "")
                                  or getattr(task, "terminal_reason", "") or ""),
        },
        "runs": runs,
        "events": {"total": sum(event_types.values()), "by_type": dict(event_types)},
        "tools": dict(tool_counter.most_common(30)),
        "tool_calls": tool_calls,
        "blackboard_keys": sorted(board.keys()),
        "reports": _report_files(task),
        "notes": [
            "本快照不修改任务、黑板、工作区或时间线",
            "复盘不得复用 resume；生产 resume 对 done/failed 仍返回 409",
        ],
    }
