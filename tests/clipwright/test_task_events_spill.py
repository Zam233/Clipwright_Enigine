"""事件流溢出落盘与回放（PRD modern-agent-review M2）。

现场：每任务热层 2000 条，超限直接 `del` —— 「从头回放」在长任务里会凭空缺一段，
主 agent/前端看到的 transcript 不是完整的。
"""

from __future__ import annotations

import json

import pytest

from clipwright.services import task_events as te


@pytest.fixture(autouse=True)
def _isolated_spill(tmp_path, monkeypatch):
    monkeypatch.setattr(te, "_SPILL_DIR", tmp_path, raising=False)
    monkeypatch.setattr(te, "_MAX_EVENTS_PER_TASK", 5, raising=False)
    te.clear()
    yield
    te.clear()


def _emit_n(n: int, task: str = "task_spill") -> None:
    for i in range(n):
        te.emit(task, "narration", f"事件 {i}")


def test_hot_layer_is_bounded_but_spill_keeps_everything(tmp_path):
    _emit_n(12)
    hot = te.events("task_spill")
    assert len(hot) == 5                      # 热层有界
    assert hot[-1].seq == 12
    full = te.events("task_spill", include_spilled=True)
    assert len(full) == 12                    # 全量无损
    assert [e.seq for e in full] == list(range(1, 13))
    spill_file = tmp_path / "task_spill.jsonl"
    assert spill_file.exists()
    lines = [json.loads(x) for x in spill_file.read_text(encoding="utf-8").splitlines() if x]
    assert [row["seq"] for row in lines] == [1, 2, 3, 4, 5, 6, 7]


def test_events_since_reads_spilled_history():
    _emit_n(12)
    tail = te.events_since("task_spill", seq=8)
    assert [e.seq for e in tail] == [9, 10, 11, 12]
    everything = te.events_since("task_spill", seq=0)
    assert len(everything) == 12
    assert te.spilled_max_seq("task_spill") == 7


def test_spilled_events_keep_detail_and_type():
    for i in range(8):
        te.emit("task_spill", "degradation", f"降级 {i}",
                {"kind": "llm_json_invalid", "idx": i})
    spilled = te.events("task_spill", include_spilled=True)[:3]
    assert spilled[0].type == "degradation"
    assert spilled[0].detail["kind"] == "llm_json_invalid"


def test_clear_drops_spill(tmp_path):
    _emit_n(10)
    assert (tmp_path / "task_spill.jsonl").exists()
    te.clear("task_spill")
    assert not (tmp_path / "task_spill.jsonl").exists()
    assert te.events("task_spill", include_spilled=True) == []
