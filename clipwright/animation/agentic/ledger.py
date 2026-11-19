"""Append-only failure ledger with repeat detection."""
from __future__ import annotations

from dataclasses import dataclass, field

from .contracts import Failure, Observation


@dataclass
class FailureLedger:
    observations: list[Observation] = field(default_factory=list)
    _seen: dict[str, str] = field(default_factory=dict)

    def add(self, obs: Observation) -> None:
        self.observations.append(obs)

    def unresolved(self) -> list[Failure]:
        done: set[str] = set()
        out: list[Failure] = []
        for obs in self.observations:
            for f in obs.failures:
                if f.severity == "blocking" and f.fingerprint() not in done:
                    done.add(f.fingerprint())
                    out.append(f)
        return out

    def repeated(self, failure: Failure) -> bool:
        fp = failure.fingerprint()
        if fp in self._seen:
            return True
        self._seen[fp] = failure.code
        return False

    def can_final(self) -> bool:
        return not self.unresolved()
