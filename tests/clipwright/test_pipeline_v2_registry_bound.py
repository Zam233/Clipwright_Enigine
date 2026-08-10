"""B9: 运行注册表有界 — record_run_start 后裁剪至最近 200 条。"""

from __future__ import annotations

from clipwright.services.pipeline_v2 import (
    clear_run_records,
    get_run_records,
    record_run_complete,
    record_run_start,
    _run_registry,
)


def test_registry_bounded_after_many_starts() -> None:
    clear_run_records()
    ids = [f"pl_bound_{i}" for i in range(250)]
    for pid in ids:
        record_run_start(pid, f"topic-{pid}")
    assert len(_run_registry) == 200
    snapshot_ids = [r["id"] for r in get_run_records(limit=500)]
    # 最旧 50 条被裁剪
    assert "pl_bound_0" not in snapshot_ids
    assert "pl_bound_249" in snapshot_ids


def test_record_run_complete_updates_bounded_run() -> None:
    clear_run_records()
    record_run_start("pl_complete_1", "t1")
    # 用空 steps 调用，应正常更新该 run 且不抛异常
    record_run_complete("pl_complete_1", "completed", [])
    records = get_run_records(limit=500)
    match = [r for r in records if r["id"] == "pl_complete_1"]
    assert len(match) == 1
    assert match[0]["status"] == "completed"


def test_under_cap_not_trimmed() -> None:
    clear_run_records()
    for i in range(5):
        record_run_start(f"pl_small_{i}", "t")
    assert len(_run_registry) == 5
