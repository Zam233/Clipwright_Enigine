"""Export gate: QA must pass; user confirmation is optional when not required."""
from __future__ import annotations

from typing import Any

from clipwright.animation.qa_engine import evaluate


def can_export(mg_def: dict[str, Any], *, user_confirmed: bool = False,
               require_confirmation: bool = False) -> dict[str, Any]:
    qa = evaluate(mg_def)
    if not qa.ok:
        return {"allowed": False, "reason": "qa_failed", "retry_action": qa.retry_action,
                "report": qa.report}
    if require_confirmation and not user_confirmed:
        return {"allowed": False, "reason": "waiting_user_confirmation", "report": qa.report}
    return {"allowed": True, "reason": "ok", "report": qa.report}
