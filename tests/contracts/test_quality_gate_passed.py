"""R9：质量门读取 passed，而不是只看 stage status。"""

from __future__ import annotations

from types import SimpleNamespace

from clipwright.services import agent_kernel


def _run(status="ok", passed=True):
    return SimpleNamespace(stage="quality", status=status, outputs={"passed": passed})


def test_quality_state_reads_passed(monkeypatch) -> None:
    monkeypatch.setattr("clipwright.services.run_store.list_task",
                        lambda _tid: [_run(passed=False)], raising=False)
    assert agent_kernel._quality_state("t") == "failed"  # noqa: SLF001
    monkeypatch.setattr("clipwright.services.run_store.list_task",
                        lambda _tid: [_run(passed=True)], raising=False)
    assert agent_kernel._quality_state("t") == "passed"  # noqa: SLF001
    monkeypatch.setattr("clipwright.services.run_store.list_task",
                        lambda _tid: [], raising=False)
    assert agent_kernel._quality_state("t") == "missing"  # noqa: SLF001
