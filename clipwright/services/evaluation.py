"""评估与回放（modern-agent PRD §2.6 / §5.5 M4）。

目标不是"再做一个仪表盘"，而是让**改动可被证伪**：

- `EvaluationRecorder.record_run`：每次 run 落一条可比指标（成功/步数/工具调用/
  子 agent 数/成本/重规划次数/耗时），成本未知就记 unknown，不填 0；
- `golden_cases`：固定选题集，前后同一口径跑；
- `compare(baseline, current)`：给出差值，而不是"感觉变好了"；
- `replay_bundle(task_id)`：把事件流 + run 结果 + 记忆证据打成一份可复现包，
  失败样本能据此重跑。

所有函数只做记录与计算，不改变执行路径（评估不得影响生产决策）。
"""

from __future__ import annotations

import json
import statistics
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from clipwright.config import logger

_MAX_RECORDS = 1000
_RECORDS: list[dict[str, Any]] = []


def _cost_of(value: Any) -> tuple[Optional[float], bool]:
    if isinstance(value, (int, float)):
        return round(float(value), 6), False
    return None, True


@dataclass
class Metric:
    """一条 run 级指标（字段固定，便于跨版本对比）。"""

    task_id: str
    run_id: str
    kind: str
    status: str
    turns: int = 0
    tool_calls: int = 0
    child_runs: int = 0
    replans: int = 0
    duration_ms: int = 0
    cost_usd: Optional[float] = None
    cost_unknown: bool = True
    provider: str = ""
    model: str = ""
    ts: float = field(default_factory=time.time)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_public(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id, "run_id": self.run_id, "kind": self.kind,
            "status": self.status, "turns": self.turns, "tool_calls": self.tool_calls,
            "child_runs": self.child_runs, "replans": self.replans,
            "duration_ms": self.duration_ms, "cost_usd": self.cost_usd,
            "cost_unknown": self.cost_unknown, "provider": self.provider,
            "model": self.model, "ts": self.ts, "extra": dict(self.extra),
        }


def record_run(*, task_id: str, run_id: str = "", kind: str = "task",
               status: str = "ok", turns: int = 0, tool_calls: int = 0,
               child_runs: int = 0, replans: int = 0, duration_ms: int = 0,
               cost_usd: Any = None, provider: str = "", model: str = "",
               extra: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """记一条指标（成本缺失时如实记 unknown）。"""
    cost, unknown = _cost_of(cost_usd)
    metric = Metric(
        task_id=str(task_id), run_id=str(run_id) or f"m_{uuid.uuid4().hex[:8]}",
        kind=kind, status=status, turns=int(turns), tool_calls=int(tool_calls),
        child_runs=int(child_runs), replans=int(replans),
        duration_ms=int(duration_ms), cost_usd=cost, cost_unknown=unknown,
        provider=provider, model=model, extra=dict(extra or {}),
    )
    _RECORDS.append(metric.to_public())
    if len(_RECORDS) > _MAX_RECORDS:
        del _RECORDS[: len(_RECORDS) - _MAX_RECORDS]
    return metric.to_public()


def records(task_id: str = "") -> list[dict[str, Any]]:
    if task_id:
        return [r for r in _RECORDS if r["task_id"] == task_id]
    return list(_RECORDS)


def reset() -> None:
    _RECORDS.clear()


def summary(items: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
    """聚合：成功率、平均/中位步数与成本、重规划率、未知成本占比。"""
    data = items if items is not None else _RECORDS
    if not data:
        return {"count": 0}
    costs = [r["cost_usd"] for r in data if isinstance(r.get("cost_usd"), (int, float))]
    unknown = sum(1 for r in data if r.get("cost_unknown"))
    ok = sum(1 for r in data if r.get("status") in ("ok", "done", "degraded"))
    return {
        "count": len(data),
        "success_rate": round(ok / len(data), 4),
        "avg_turns": round(statistics.fmean([r.get("turns", 0) for r in data]), 2),
        "avg_tool_calls": round(statistics.fmean([r.get("tool_calls", 0) for r in data]), 2),
        "avg_child_runs": round(statistics.fmean([r.get("child_runs", 0) for r in data]), 2),
        "avg_duration_ms": round(statistics.fmean([r.get("duration_ms", 0) for r in data]), 1),
        "cost_known": len(costs),
        "cost_unknown_count": unknown,
        "cost_total_usd": round(sum(costs), 6) if costs else None,
        "cost_avg_usd": round(statistics.fmean(costs), 6) if costs else None,
        "replan_rate": round(
            sum(1 for r in data if r.get("replans")) / len(data), 4),
    }


def compare(baseline: list[dict[str, Any]],
            current: list[dict[str, Any]]) -> dict[str, Any]:
    """两次跑分对比（差值 + 方向），拒绝"感觉变好了"。"""
    a, b = summary(baseline), summary(current)
    keys = ("success_rate", "avg_turns", "avg_tool_calls", "avg_child_runs",
            "avg_duration_ms", "cost_avg_usd", "replan_rate")
    delta: dict[str, Any] = {}
    for key in keys:
        av, bv = a.get(key), b.get(key)
        if isinstance(av, (int, float)) and isinstance(bv, (int, float)):
            delta[key] = {"baseline": av, "current": bv, "diff": round(bv - av, 6)}
        else:
            delta[key] = {"baseline": av, "current": bv, "diff": None}
    return {"baseline": a, "current": b, "delta": delta}


# ── golden 用例 ────────────────────────────────────────


def load_golden(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                out.append(json.loads(line))
    except Exception as exc:  # noqa: BLE001 — 坏用例要报出来而不是静默跳过
        logger.warning("golden 用例读取失败 %s: %s", p, exc)
    return out


def append_golden(path: str | Path, case: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(case, ensure_ascii=False) + "\n")


# ── replay bundle ──────────────────────────────────────


def replay_bundle(task_id: str, *, root: str | Path | None = None) -> dict[str, Any]:
    """把一次任务的证据打成一包：事件 + run 结果 + 记忆召回 + 指标。

    这是"失败样本能复盘"的最小充分集：没有事件就没有因果，没有 run 结果
    就不知道谁失败，没有记忆召回就不知道为什么它那样决策。
    """
    from clipwright.services import memory, task_events

    bundle: dict[str, Any] = {
        "task_id": task_id,
        "built_at": time.time(),
        "events": [],
        "runs": [],
        "memory": {},
        "metrics": records(task_id),
    }
    try:
        bundle["events"] = [e.model_dump(mode="json")
                            for e in task_events.events(task_id)]
    except Exception as exc:  # noqa: BLE001
        bundle["events_error"] = f"{type(exc).__name__}: {exc}"
    try:
        from clipwright.services import agent_run

        bundle["runs"] = agent_run.list_runs(task_id)
    except Exception as exc:  # noqa: BLE001
        bundle["runs_error"] = f"{type(exc).__name__}: {exc}"
    try:
        bundle["memory"] = {
            "describe": memory.describe(task_id),
            "episodic": memory.recall("", task_id=task_id, layers=("episodic",), k=20),
        }
    except Exception as exc:  # noqa: BLE001
        bundle["memory_error"] = f"{type(exc).__name__}: {exc}"

    if root is not None:
        try:
            path = Path(root) / "traces" / f"{task_id}.replay.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2),
                            encoding="utf-8")
            bundle["path"] = str(path)
        except Exception as exc:  # noqa: BLE001
            bundle["path_error"] = f"{type(exc).__name__}: {exc}"
    return bundle


__all__ = ["Metric", "append_golden", "compare", "load_golden", "record_run",
           "records", "replay_bundle", "reset", "summary"]
