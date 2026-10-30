"""阶段进度播报（PRD §14）：Kernel 确定性触发 + 同时落 `reports/`。

为什么要独立一个模块：进度播报必须是**程序事实**，不能靠模型自觉。三条纪律：

1. **触发点是状态转换**：开工 / 阶段开始 / 阶段结束 / 首次降级 / 预算过半 /
   同一工具连续失败 / 收尾前 / 需要用户决策，逐个在代码里挂，不写在提示词里；
2. **播报不得替代工具调用**：这里只发事件与写文件，绝不代替真正的派发/工具执行；
3. **落盘不丢**：事件热层会淘汰（`_MAX_EVENTS_PER_TASK`），所以每条同时追加
   `reports/progress.jsonl`（机器读）与 `reports/progress.md`（人读），
   并在 `reports/INDEX.md` 维护「进度时间线」小节。

去重：首次降级按 `(task, kind)`、预算过半按维度、其余按 `(trigger, stage)` 的显式 key，
由调用方传 `once_key`；同一 key 只发一次。写盘失败记日志并继续，不让业务因播报失败。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Iterable, Optional

from clipwright.config import logger

#: 已发过的 once_key（进程内；重启后重新计一次，代价只是多一条播报）
_EMITTED: set[str] = set()
#: 进度文件
JSONL_NAME = "progress.jsonl"
MD_NAME = "progress.md"
_MAX_MD_LINES = 400


def _reports_dir(task_id: str) -> Path | None:
    try:
        from clipwright.services.workspace import Workspace

        root = Workspace(str(task_id)).dirs().root
        path = Path(root) / "reports"
        path.mkdir(parents=True, exist_ok=True)
        return path
    except Exception as exc:  # noqa: BLE001 — 工作区不可用就不落盘（事件照发）
        logger.warning("进度播报落盘路径不可用 %s: %s", task_id, exc)
        return None


def _append_files(task_id: str, record: dict[str, Any]) -> None:
    base = _reports_dir(task_id)
    if base is None:
        return
    try:
        with (base / JSONL_NAME).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001
        logger.warning("progress.jsonl 写入失败 %s: %s", task_id, exc)
    try:
        md = base / MD_NAME
        bits = [f"- `{record.get('ts', '')}` **{record.get('progress_text', '')}**"]
        if record.get("stage"):
            bits.append(f"`{record['stage']}`")
        if record.get("action"):
            bits.append(str(record["action"]))
        if record.get("conclusion"):
            bits.append(f"· {record['conclusion']}")
        if record.get("next_action"):
            bits.append(f"→ {record['next_action']}")
        line = " ".join(bits)
        header = "# 进度时间线\n\n> 由 Kernel 确定性播报（PRD §14），与任务事件 `progress` 同源。\n\n"
        if not md.is_file():
            md.write_text(header + line + "\n", encoding="utf-8")
        else:
            existing = md.read_text(encoding="utf-8").splitlines()
            body = [ln for ln in existing if ln.startswith("- `")]
            body.append(line)
            body = body[-_MAX_MD_LINES:]
            md.write_text(header + "\n".join(body) + "\n", encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("progress.md 写入失败 %s: %s", task_id, exc)


def _progress_text(completed: int, total: int) -> str:
    if total > 0:
        return f"{completed}/{total}"
    return "阶段进度（总数未定）"


def emit_progress(task_id: str, *, trigger: str, stage: str = "",
                  completed: int = 0, total: int = 0,
                  action: str = "", conclusion: str = "", next_action: str = "",
                  decision_required: bool = False,
                  decision_options: Optional[Iterable[dict[str, Any]]] = None,
                  budget: Optional[dict[str, Any]] = None,
                  failure_streak: Optional[dict[str, Any]] = None,
                  once_key: str = "", source: str = "kernel",
                  span: str = "kernel") -> dict[str, Any] | None:
    """发一条 `progress` 事件并落盘。返回 detail；被去重则返回 None。"""
    tid = str(task_id or "")
    if not tid or not trigger:
        return None
    key = f"{tid}:{once_key or trigger}" if not stage else f"{tid}:{once_key or trigger}:{stage}"
    if once_key:
        if key in _EMITTED:
            return None
        _EMITTED.add(key)

    pct = int(round(completed * 100 / total)) if total > 0 else None
    text = _progress_text(int(completed), int(total))
    detail: dict[str, Any] = {
        "trigger": str(trigger), "stage": str(stage),
        "completed": int(completed), "total": int(total), "percent": pct,
        "action": str(action)[:300], "conclusion": str(conclusion)[:300],
        "next_action": str(next_action)[:300],
        "decision_required": bool(decision_required),
        "source": str(source),
        "progress_text": text,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    if decision_options:
        detail["decision_options"] = [
            {"id": str(o.get("id") or ""), "label": str(o.get("label") or "")[:120]}
            for o in list(decision_options)[:8] if isinstance(o, dict)
        ]
    if budget:
        detail["budget"] = budget
    if failure_streak:
        detail["failure_streak"] = failure_streak

    try:
        from clipwright.services.task_events import emit

        emit(tid, "progress",
             f"[{text}] {stage or trigger} · {conclusion or action or trigger}"[:200],
             detail, span=span)
    except Exception as exc:  # noqa: BLE001 — 事件通道失败不影响业务
        logger.warning("progress 事件发送失败 %s: %s", tid, exc)

    record = {"task_id": tid, **detail}
    _append_files(tid, record)
    return detail


#: usage 字段 → limits 字段（名字不一样：turns / max_turns…）
_LIMIT_FIELD = {"turns": "max_turns", "tool_calls": "max_tool_calls",
                "wall_sec": "max_wall_sec", "budget_usd": "budget_usd"}


def budget_ratio(task: Any, dimension: str) -> tuple[float, dict[str, Any]]:
    """算某个预算维度的使用率（turns/tool_calls/wall_sec/budget_usd）。"""
    limits = getattr(task, "limits", None)
    usage = getattr(task, "usage", None)
    used = float(getattr(usage, dimension, 0) or 0)
    limit = float(getattr(limits, _LIMIT_FIELD.get(dimension, dimension), 0) or 0)
    ratio = (used / limit) if limit > 0 else 0.0
    return ratio, {"dimension": dimension, "used": used, "limit": limit,
                   "ratio": round(ratio, 4)}


def on_budget_half(task: Any, stage: str = "") -> dict[str, Any] | None:
    """任一维度首次过半 → 播报一次（每个维度各自只发一次）。"""
    for dim in ("turns", "tool_calls", "wall_sec", "budget_usd"):
        ratio, payload = budget_ratio(task, dim)
        if ratio >= 0.5:
            detail = emit_progress(
                str(getattr(task, "id", "") or ""), trigger="budget_half",
                stage=stage, budget=payload,
                conclusion=f"{payload['dimension']} 已用 {payload['used']:.0f}/{payload['limit']:.0f}"
                           f"（{ratio:.0%}）",
                next_action="收紧收尾策略：先把已有产物收敛成时间线，停止扩大探索",
                once_key=f"budget_half:{dim}")
            if detail is not None:
                return detail
    return None


def reset() -> None:
    """测试用：清去重表。"""
    _EMITTED.clear()


__all__ = ["JSONL_NAME", "MD_NAME", "budget_ratio", "emit_progress", "on_budget_half", "reset"]
