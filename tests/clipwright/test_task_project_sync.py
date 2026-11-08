# -*- coding: utf-8 -*-
"""成片时间线回写发起项目（E2E 2026-10-29）。

背景缺陷：proceed 带了 `project_id`，但 Task 路径只把它当工作区 id 用——任务跑完
黑板里有 final_timeline，项目里仍是 0 轨，前端导出页按钮恒禁用。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from clipwright.services import task_blackboard as bb
from clipwright.services import task_project_sync as sync


class _FakeManager:
    def __init__(self, existing: set[str] | None = None) -> None:
        self.saves: list[tuple[str, dict]] = []
        self.existing = existing or {"proj_1"}

    def save(self, project_id: str, data: dict) -> dict:
        if project_id not in self.existing:
            raise FileNotFoundError(project_id)
        self.saves.append((project_id, data))
        return {"id": project_id, **data}


@pytest.fixture(autouse=True)
def _clean_board():
    bb.reset("task_sync_test")
    yield
    bb.reset("task_sync_test")


def _task(extra: dict) -> SimpleNamespace:
    return SimpleNamespace(request=SimpleNamespace(extra=extra))


def test_resolve_timeline_accepts_plain_dict() -> None:
    tl = {"tracks": [{"id": "t1", "clips": []}], "duration": 12.0}
    assert sync.resolve_timeline(tl) is tl


def test_resolve_timeline_follows_spill_pointer(tmp_path: Path) -> None:
    payload = {"tracks": [{"id": "v1", "clips": [{"id": "c1"}]}], "duration": 7.0}
    f = tmp_path / "timeline.json"
    f.write_text(json.dumps(payload), encoding="utf-8")
    got = sync.resolve_timeline({"path": str(f), "sha256": "x", "bytes": 10})
    assert got == payload


def test_resolve_timeline_rejects_garbage(tmp_path: Path) -> None:
    assert sync.resolve_timeline(None) is None
    assert sync.resolve_timeline({"tracks": "not-a-list"}) is None
    assert sync.resolve_timeline({"path": str(tmp_path / "missing.json")}) is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert sync.resolve_timeline({"path": str(bad)}) is None


def test_project_id_of_prefers_proceed_project_id() -> None:
    assert sync.project_id_of(_task({"proceed_project_id": "proj_a", "project_id": "proj_b"})) == "proj_a"
    assert sync.project_id_of(_task({"project_id": "proj_b"})) == "proj_b"
    assert sync.project_id_of(_task({})) == ""


def test_sync_writes_timeline_from_board(monkeypatch) -> None:
    task_id = "task_sync_test"
    timeline = {"tracks": [{"id": "t1", "clips": []}], "duration": 3.0}
    bb.put(task_id, "final_timeline", timeline, by="audio")
    fake = _FakeManager()
    monkeypatch.setattr("clipwright.api.project._manager", fake, raising=False)

    pid = sync.sync_timeline_to_project(task_id, _task({"proceed_project_id": "proj_1"}))

    assert pid == "proj_1"
    assert len(fake.saves) == 1
    assert fake.saves[0][0] == "proj_1"
    saved = fake.saves[0][1]["timeline"]
    # B5 后黑板写入会附 provenance metadata（agent_sig/last_writer/source/written_at），
    # 这里比较业务字段本身，忽略写入元数据。
    assert saved["tracks"] == timeline["tracks"]
    assert saved.get("duration") == timeline.get("duration")


def test_sync_skips_when_no_timeline(monkeypatch) -> None:
    fake = _FakeManager()
    monkeypatch.setattr("clipwright.api.project._manager", fake, raising=False)
    pid = sync.sync_timeline_to_project("task_sync_test", _task({"proceed_project_id": "proj_1"}))
    assert pid == ""
    assert fake.saves == []


def test_sync_skips_without_project(monkeypatch) -> None:
    task_id = "task_sync_test"
    bb.put(task_id, "final_timeline", {"tracks": []}, by="audio")
    fake = _FakeManager()
    monkeypatch.setattr("clipwright.api.project._manager", fake, raising=False)
    assert sync.sync_timeline_to_project(task_id, _task({})) == ""
    assert fake.saves == []


def test_sync_survives_missing_project(monkeypatch) -> None:
    task_id = "task_sync_test"
    bb.put(task_id, "final_timeline", {"tracks": [{"id": "t"}]}, by="audio")
    fake = _FakeManager(existing=set())
    monkeypatch.setattr("clipwright.api.project._manager", fake, raising=False)
    # 项目已被删掉也不能抛——任务已经成功，回写失败只记日志
    assert sync.sync_timeline_to_project(task_id, _task({"proceed_project_id": "proj_gone"})) == ""
