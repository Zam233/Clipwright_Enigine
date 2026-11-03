"""V19：类型切换记录单测（PRD F13）。"""

from __future__ import annotations

import json

import pytest

from clipwright.category.base import BaseCategoryPlugin
from clipwright.category.registry import CategoryRegistry
from clipwright.services.type_switch import (
    TypeSwitchError,
    append_switch,
    plan_switch,
    switch_note,
)


class _P(BaseCategoryPlugin):
    def __init__(self, pid: str) -> None:
        self.plugin_id = pid
        self.display_name = pid

    def translate_persona(self, params):  # type: ignore[no-untyped-def]
        return {}

    def get_shot_params(self, translated):  # type: ignore[no-untyped-def]
        return {}


@pytest.fixture(autouse=True)
def _isolate():
    CategoryRegistry.clear()
    CategoryRegistry.register(_P("lyric_mv"))
    CategoryRegistry.register(_P("knowledge_longform"))
    yield
    CategoryRegistry.clear()


class TestPlanSwitch:
    def test_records_both_types(self) -> None:
        record = plan_switch("lyric_mv", "knowledge_longform", reason="配置更像知识区")
        assert record.from_type == "lyric_mv"
        assert record.to_type == "knowledge_longform"
        assert record.as_dict()["event"] == "type_switch"

    def test_unknown_type_rejected(self) -> None:
        with pytest.raises(TypeSwitchError) as exc:
            plan_switch("lyric_mv", "nonexistent")
        assert "nonexistent" in str(exc.value)

    def test_same_type_rejected(self) -> None:
        with pytest.raises(TypeSwitchError):
            plan_switch("lyric_mv", "lyric_mv")

    def test_blank_rejected(self) -> None:
        with pytest.raises(TypeSwitchError):
            plan_switch("", "lyric_mv")

    def test_require_known_false_allows_unknown(self) -> None:
        record = plan_switch("custom_a", "custom_b", require_known=False)
        assert record.to_type == "custom_b"

    def test_note_contains_reason(self) -> None:
        record = plan_switch("lyric_mv", "knowledge_longform", reason="更像知识区")
        note = switch_note(record)
        assert "lyric_mv → knowledge_longform" in note
        assert "更像知识区" in note


class TestAppend:
    def test_appends_jsonl(self, tmp_path) -> None:
        path = tmp_path / "switches.jsonl"
        record = plan_switch("lyric_mv", "knowledge_longform", reason="x")
        append_switch(path, record)
        append_switch(path, record)
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        payload = json.loads(lines[0])
        assert payload["from_type"] == "lyric_mv"
        assert payload["to_type"] == "knowledge_longform"

    def test_creates_parent_dir(self, tmp_path) -> None:
        path = tmp_path / "deep" / "dir" / "switches.jsonl"
        append_switch(path, plan_switch("lyric_mv", "knowledge_longform"))
        assert path.exists()
