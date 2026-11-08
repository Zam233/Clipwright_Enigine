"""Prompt / few-shot 候选注册表与离线门禁（closeout B10 / PRD §5.5）。

规则很朴素，但没有它"改 prompt 变好了"永远是感觉：

1. 候选必须**带版本、变更说明、适用任务类型、回滚指针**——缺一项就不许登记；
2. 门禁只比**同一份 golden 集**上跑出来的指标（成功率 / 步数 / 成本 / 重规划率）；
3. 门禁默认**保守**：不允许"成功率下降换成本下降"这种暗换（必须显式允许）；
4. 通过门禁 ≠ 上线。通过只是"可以进 canary"，回滚指针一直在。
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from clipwright.config import logger

#: 门禁阈值（保守默认；改这里要代码评审）
DEFAULT_GATE = {
    "min_success_rate": 1.0,      # 成功率不得低于基线的这个比例
    "max_cost_ratio": 1.15,       # 成本不得高于基线的 1.15 倍
    "max_turns_ratio": 1.20,      # 平均步数不得高于基线的 1.20 倍
    "max_replan_ratio": 1.50,     # 重规划率不得高于基线的 1.50 倍
}


@dataclass
class Candidate:
    """一次 prompt/few-shot 改动。"""

    name: str
    version: str
    change_note: str
    applies_to: list[str] = field(default_factory=list)
    rollback_to: str = ""
    prompt_ref: str = ""
    created_at: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: f"pc_{uuid.uuid4().hex[:8]}")
    status: str = "candidate"        # candidate / canary / adopted / rolled_back

    def validate(self) -> list[str]:
        """登记前自检：缺这些就不许进注册表。"""
        problems: list[str] = []
        if not self.name.strip():
            problems.append("缺 name")
        if not self.version.strip():
            problems.append("缺 version")
        if not self.change_note.strip():
            problems.append("缺 change_note（为什么改）")
        if not self.applies_to:
            problems.append("缺 applies_to（适用哪些任务类型）")
        if not self.rollback_to.strip():
            problems.append("缺 rollback_to（回滚到哪个版本）")
        return problems

    def to_public(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "version": self.version,
            "change_note": self.change_note, "applies_to": list(self.applies_to),
            "rollback_to": self.rollback_to, "prompt_ref": self.prompt_ref,
            "created_at": self.created_at, "status": self.status,
        }


_CANDIDATES: dict[str, Candidate] = {}


def reset() -> None:
    _CANDIDATES.clear()


def register(candidate: Candidate) -> dict[str, Any]:
    """登记候选（自检不过直接拒绝，不写进注册表）。"""
    problems = candidate.validate()
    if problems:
        raise ValueError("候选不合法：" + "；".join(problems))
    _CANDIDATES[candidate.id] = candidate
    return candidate.to_public()


def list_candidates(status: str = "") -> list[dict[str, Any]]:
    items = [c for c in _CANDIDATES.values() if not status or c.status == status]
    return [c.to_public() for c in sorted(items, key=lambda c: -c.created_at)]


def promote(candidate_id: str, *, status: str = "canary") -> dict[str, Any]:
    if status not in ("candidate", "canary", "adopted", "rolled_back"):
        raise ValueError(f"未知状态: {status}")
    c = _CANDIDATES.get(candidate_id)
    if c is None:
        raise KeyError(f"候选不存在: {candidate_id}")
    c.status = status
    return c.to_public()


def gate(baseline: dict[str, Any], candidate: dict[str, Any],
         *, thresholds: Optional[dict[str, float]] = None,
         allow_cost_trade: bool = False) -> dict[str, Any]:
    """离线门禁：候选指标对比基线，返回 `{passed, failures, deltas}`。

    `baseline` / `candidate` 都是 `evaluation.summary()` 的产物。
    `allow_cost_trade=False`（默认）时，**任何成功率下降都算不通过**——
    不允许"省了钱但更容易失败"这种暗换。
    """
    th = {**DEFAULT_GATE, **(thresholds or {})}
    failures: list[str] = []
    deltas: dict[str, Any] = {}

    base_ok = float(baseline.get("success_rate") or 0.0)
    cand_ok = float(candidate.get("success_rate") or 0.0)
    deltas["success_rate"] = round(cand_ok - base_ok, 4)
    if cand_ok + 1e-9 < base_ok * th["min_success_rate"]:
        failures.append(f"成功率下降：{base_ok:.3f} → {cand_ok:.3f}")

    def _ratio(key: str) -> Optional[float]:
        b = baseline.get(key)
        c = candidate.get(key)
        if not isinstance(b, (int, float)) or not isinstance(c, (int, float)) or b <= 0:
            return None
        return c / b

    for key, limit_key in (("cost_avg_usd", "max_cost_ratio"),
                           ("avg_turns", "max_turns_ratio"),
                           ("replan_rate", "max_replan_ratio")):
        r = _ratio(key)
        if r is None:
            continue
        deltas[key] = round(r, 4)
        if r > th[limit_key]:
            failures.append(f"{key} 超限：{r:.2f}x > {th[limit_key]}x")

    if allow_cost_trade and failures and all("成功率" not in f for f in failures):
        # 只有"允许用成本换"时才可能放过；成功率下降永远不能被放过
        failures = [f for f in failures if "cost_avg_usd" in f or "avg_turns" in f]

    return {"passed": not failures, "failures": failures,
            "deltas": deltas, "thresholds": th}


# ── 三次对比报告 ──────────────────────────────────────

def three_run_report(runs: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """三次（或更多）独立运行的对比报告：均值 + 极差，不拿一次幸运跑当门禁。

    `runs` 里每项是 `evaluation.summary()` 的产物（一次完整跑）。
    """
    items = [r for r in runs if isinstance(r, dict)]
    if not items:
        return {"ok": False, "reason": "没有运行数据"}

    def _vals(key: str) -> list[float]:
        return [float(r[key]) for r in items if isinstance(r.get(key), (int, float))]

    report: dict[str, Any] = {"ok": True, "runs": len(items), "metrics": {}}
    for key in ("success_rate", "avg_turns", "avg_tool_calls", "avg_child_runs",
                "avg_duration_ms", "cost_avg_usd", "replan_rate"):
        vals = _vals(key)
        if not vals:
            report["metrics"][key] = {"n": 0}
            continue
        report["metrics"][key] = {
            "n": len(vals),
            "mean": round(sum(vals) / len(vals), 6),
            "min": round(min(vals), 6),
            "max": round(max(vals), 6),
            "spread": round(max(vals) - min(vals), 6),
        }
    # 成功率如果三次不一致，说明结果不稳，标出来（门禁不该建在不稳的指标上）
    sr = report["metrics"]["success_rate"]
    if sr.get("n") and sr["spread"] > 0:
        report.setdefault("warnings", []).append(
            f"成功率在 {sr['n']} 次运行间波动 {sr['spread']:.3f}：门禁阈值要考虑方差")
    return report


def save_report(report: dict[str, Any], path: str | Path) -> str:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("评估报告已写入 %s", p)
    return str(p)


def load_candidates_file(path: str | Path) -> list[dict[str, Any]]:
    """从 JSONL 载入候选（一行一个），坏行跳过并计数。"""
    p = Path(path)
    if not p.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            out.append(json.loads(line))
        except Exception:  # noqa: BLE001
            continue
    return out


__all__ = ["Candidate", "DEFAULT_GATE", "gate", "list_candidates", "load_candidates_file",
           "promote", "register", "reset", "save_report", "three_run_report"]
