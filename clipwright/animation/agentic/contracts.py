"""First-class failure/observation contract for animation agentic loop."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Failure:
    code: str
    category: str
    severity: str = "blocking"  # blocking|warning
    scope: str = "tool"         # tool|element|field|document|artifact
    location: dict[str, Any] = field(default_factory=dict)
    expected: Any = None
    observed: Any = None
    fix_hint: str = ""
    retryable: bool = True
    evidence_ref: str = ""

    def fingerprint(self) -> str:
        return f"{self.code}|{self.scope}|{self.location}|{self.observed}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Observation:
    tool: str
    status: str  # ok|failed|partial|blocked
    summary: str = ""
    artifact_ref: str = ""
    failures: tuple[Failure, ...] = ()
    next_actions: tuple[str, ...] = ()
    raw_ref: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "ok" and not any(f.severity == "blocking" for f in self.failures)

    @property
    def blocking(self) -> list[Failure]:
        return [f for f in self.failures if f.severity == "blocking"]

    def to_dict(self, include_data: bool = True) -> dict[str, Any]:
        d = {
            "tool": self.tool, "status": self.status, "summary": self.summary,
            "artifact_ref": self.artifact_ref,
            "failures": [f.to_dict() for f in self.failures],
            "next_actions": list(self.next_actions), "raw_ref": self.raw_ref,
        }
        if include_data:
            d["data"] = self.data
        return d
