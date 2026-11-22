"""Kernel Exec surface：默认隐藏，开启后只在主 Agent 面出现。"""

from __future__ import annotations

from clipwright.config import settings
from clipwright.tool.surface import EXEC_TOOLS, schemas


def test_exec_surface_disabled_by_default(monkeypatch) -> None:
    monkeypatch.setattr(settings, "kernel_exec_enabled", False, raising=False)
    names = {s["function"]["name"] for s in schemas()}
    assert not (names & set(EXEC_TOOLS))


def test_exec_surface_enabled(monkeypatch) -> None:
    monkeypatch.setattr(settings, "kernel_exec_enabled", True, raising=False)
    names = {s["function"]["name"] for s in schemas()}
    assert set(EXEC_TOOLS) <= names
