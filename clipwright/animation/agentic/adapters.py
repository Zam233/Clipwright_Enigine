"""Adapters: legacy tool results -> first-class Observation/Failure."""
from __future__ import annotations

import re
from typing import Any

from .contracts import Failure, Observation


def _code_for(text: str) -> tuple[str, str]:
    t = text.lower()
    if "font" in t and ("font_face" in t or "未注册" in t):
        return "MG_FONT_UNREGISTERED", "typography"
    if "多行" in t or "y/bbox" in t:
        return "MG_LAYOUT_MULTILINE_OVERLAP", "layout"
    if "lint" in t:
        return "MG_HTML_LINT", "lint"
    if "expecting" in t or "json" in t and "parse" in t:
        return "MG_JSON_PARSE", "schema"
    if "shape" in t and "missing" in t:
        return "MG_SHAPE_MISSING", "schema"
    if "easing" in t:
        return "MG_EASING_UNSUPPORTED", "schema"
    return "MG_TOOL_FAILED", "tool"


def failures_from_result(result: dict[str, Any]) -> list[Failure]:
    out: list[Failure] = []
    errors = list(result.get("qa_errors") or [])
    if result.get("error"):
        errors.append(str(result["error"]))
    for text in errors:
        code, category = _code_for(str(text))
        loc: dict[str, Any] = {}
        m = re.search(r"elements\[(\d+)\]", str(text))
        if m:
            loc["element_index"] = int(m.group(1))
        out.append(Failure(code, category, scope="element" if loc else "tool",
            location=loc, observed=str(text), fix_hint=str(text), retryable=True))
    return out


def observation_from_result(tool: str, result: Any) -> Observation:
    if not isinstance(result, dict):
        return Observation(tool=tool, status="ok" if result is not None else "failed",
            summary=str(result)[:300], data={"result": result})
    failures = tuple(failures_from_result(result))
    ok = bool(result.get("success")) and not any(f.severity == "blocking" for f in failures)
    return Observation(
        tool=tool,
        status="ok" if ok else "failed",
        summary=str(result.get("method") or result.get("error") or "done")[:300],
        artifact_ref=str(result.get("path") or result.get("preview", {}).get("path") or ""),
        failures=failures,
        data=result,
    )
