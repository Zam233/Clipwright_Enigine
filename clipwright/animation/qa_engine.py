"""Engine-owned QA loop: evaluate -> choose retry action -> local repair."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from clipwright.animation.mg.layout import normalize_mg_layout
from clipwright.animation.qa import qa_report


@dataclass
class QAOutcome:
    ok: bool
    report: dict[str, Any]
    retry_action: str = ""
    max_attempts: int = 3

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "report": self.report, "retry_action": self.retry_action,
                "max_attempts": self.max_attempts}


def evaluate(mg_def: dict[str, Any]) -> QAOutcome:
    report = qa_report(mg_def)
    if report["ok"]:
        return QAOutcome(True, report)
    text = report.get("text_issues") or []
    motion = report.get("motion_issues") or []
    retry = "normalize_layout"
    if any("来源" in str(x) for x in text):
        retry = "research"
    elif any("无关键帧" in str(x) for x in motion):
        retry = "generate_mg"
    return QAOutcome(False, report, retry)


def repair_once(mg_def: dict[str, Any]) -> tuple[dict[str, Any], QAOutcome]:
    """One deterministic local repair; never silently claims success."""
    fixed, adjustments = normalize_mg_layout(copy.deepcopy(mg_def))
    report = qa_report(fixed)
    return fixed, QAOutcome(report["ok"], report, "" if report["ok"] else "generate_mg")
