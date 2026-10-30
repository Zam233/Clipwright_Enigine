"""前端 run 详情读全量工具轨迹的接口（PRD modern-agent-review M3）。

`AgentResult.tool_calls` 只带 12 条采样，完整 transcript 落盘在
`<workspace>/reports/tool_calls/<stage>-<n>.json`。这里验证读回接口：
分页、internal/failed 计数、available 列表、缺文件时不炸。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import pytest

from clipwright.services.workspace import Workspace


class _FakeRequest:
    """最小 Request 替身：单机模式（user_id 为 None）下 owner 校验放行。"""

    class _State:
        user_id = None
        user_role = None

    state = _State()
    headers: dict[str, str] = {}

    def __getitem__(self, key):  # pragma: no cover - 守卫用
        raise KeyError(key)


@pytest.fixture()
def workspace_dir(monkeypatch, tmp_path):
    from clipwright.config import settings

    monkeypatch.setattr(settings, "workspace_dir", str(tmp_path), raising=False)
    return tmp_path


def _root(task_id: str) -> Path:
    return Workspace(task_id).dirs().root


def _write_transcript(task_id: str, stage: str, attempt: int, calls: list[dict]) -> Path:
    base = _root(task_id) / "reports" / "tool_calls"
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{stage}-{attempt}.json"
    path.write_text(
        json.dumps(
            {"stage": stage, "attempt": attempt, "count": len(calls), "calls": calls},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _call(task_id: str, **kwargs):
    from clipwright.api.task import task_tool_calls

    kwargs.setdefault("stage", "")
    kwargs.setdefault("attempt", 0)
    kwargs.setdefault("limit", 2000)
    kwargs.setdefault("offset", 0)
    return asyncio.run(task_tool_calls(task_id, _FakeRequest(), **kwargs))


def test_returns_latest_attempt_with_full_transcript(workspace_dir):
    task_id = "task_tc0001"
    _write_transcript(
        task_id,
        "edit",
        1,
        [
            {"tool": "video_trim", "status": "error"},
            {"tool": "video_concat", "status": "success"},
        ],
    )
    _write_transcript(
        task_id,
        "edit",
        2,
        [
            {"tool": "video_trim", "status": "success"},
            {"tool": "mg_generate:x", "status": "ok", "source": "internal"},
            {"tool": "video_concat", "status": "error"},
        ],
    )

    body = _call(task_id, stage="edit")

    assert body["task_id"] == task_id
    assert body["stage"] == "edit"
    assert body["attempt"] == 2
    assert body["total"] == 3 and body["count"] == 3
    assert body["internal"] == 1
    assert body["failed"] == 1
    assert [c["tool"] for c in body["items"]] == ["video_trim", "mg_generate:x", "video_concat"]
    assert "edit-1" in body["available"] and "edit-2" in body["available"]
    assert body["path"].endswith("edit-2.json")


def test_can_pick_historical_attempt(workspace_dir):
    task_id = "task_tc0002"
    _write_transcript(task_id, "edit", 1, [{"tool": "a", "status": "success"}])
    _write_transcript(task_id, "edit", 2, [{"tool": "b", "status": "success"}])

    body = _call(task_id, stage="edit", attempt=1)

    assert body["attempt"] == 1
    assert body["items"][0]["tool"] == "a"


def test_paginates(workspace_dir):
    task_id = "task_tc0003"
    _write_transcript(
        task_id, "animation", 1, [{"tool": f"t{i}", "status": "success"} for i in range(25)]
    )

    page = _call(task_id, stage="animation", limit=10, offset=20)

    assert page["total"] == 25
    assert page["count"] == 5
    assert [c["tool"] for c in page["items"]] == [f"t{i}" for i in range(20, 25)]
    assert page["offset"] == 20 and page["limit"] == 10


def test_empty_when_workspace_missing(workspace_dir):
    body = _call("task_nosuch", stage="edit")

    assert body["count"] == 0 and body["items"] == []
    assert body["total"] == 0 and body["available"] == []


def test_empty_when_stage_has_no_transcript(workspace_dir):
    task_id = "task_tc0004"
    _write_transcript(task_id, "edit", 1, [{"tool": "a", "status": "success"}])

    body = _call(task_id, stage="audio")

    assert body["count"] == 0
    # 仍要告诉前端磁盘上有哪些 stage 可看
    assert body["available"] == ["edit-1"]


def test_stage_blank_picks_most_recent_file(workspace_dir):
    task_id = "task_tc0005"
    p1 = _write_transcript(task_id, "edit", 1, [{"tool": "old", "status": "success"}])
    p2 = _write_transcript(task_id, "audio", 1, [{"tool": "new", "status": "success"}])

    now = time.time()
    os.utime(p1, (now - 100, now - 100))
    os.utime(p2, (now, now))

    body = _call(task_id)

    assert body["stage"] == "audio"
    assert body["items"][0]["tool"] == "new"


def test_rejects_path_traversal_in_stage(workspace_dir):
    """stage 只用于前缀匹配磁盘文件名，不接受路径穿越。"""
    task_id = "task_tc0006"
    _write_transcript(task_id, "edit", 1, [{"tool": "a", "status": "success"}])

    body = _call(task_id, stage="../../../etc")

    assert body["count"] == 0
    assert body["items"] == []

def test_reads_transcripts_from_project_workspace(workspace_dir, monkeypatch):
    """从项目 /proceed 起来的任务，轨迹落在**项目**工作区。

    阶段侧 `_workspace_root_of` 取 `request.extra.workspace_root`，只查任务工作区会
    读成"没有轨迹"（E2E 2026-10-30 实测）。
    """
    from clipwright.api import task as task_api
    from types import SimpleNamespace

    task_id = "task_proj_ws"
    project_id = "proj_ws_9f8e7d"
    proj_root = Workspace(project_id).dirs().root
    base = proj_root / "reports" / "tool_calls"
    base.mkdir(parents=True, exist_ok=True)
    (base / "edit-1.json").write_text(
        json.dumps({"stage": "edit", "attempt": 1, "count": 1,
                    "calls": [{"tool": "video_trim", "status": "success"}]}),
        encoding="utf-8")

    fake_task = SimpleNamespace(request=SimpleNamespace(
        extra={"proceed_project_id": project_id}))
    monkeypatch.setattr(task_api, "_kernel",
                        lambda: SimpleNamespace(get=lambda tid: fake_task), raising=False)

    body = _call(task_id, stage="edit")

    assert body["total"] == 1
    assert body["items"][0]["tool"] == "video_trim"
    parts = Path(body["path"]).parts
    assert parts[-3:] == ("reports", "tool_calls", "edit-1.json")


def test_reads_transcripts_from_extra_workspace_root(workspace_dir, monkeypatch, tmp_path):
    """`request.extra.workspace_root` 优先（与阶段侧完全一致）。"""
    from clipwright.api import task as task_api
    from types import SimpleNamespace

    root = tmp_path / "custom_ws"
    base = root / "reports" / "tool_calls"
    base.mkdir(parents=True, exist_ok=True)
    (base / "audio-1.json").write_text(
        json.dumps({"stage": "audio", "attempt": 1, "count": 1,
                    "calls": [{"tool": "tts", "status": "error"}]}),
        encoding="utf-8")
    fake_task = SimpleNamespace(request=SimpleNamespace(
        extra={"workspace_root": str(root)}))
    monkeypatch.setattr(task_api, "_kernel",
                        lambda: SimpleNamespace(get=lambda tid: fake_task), raising=False)

    body = _call("task_custom_ws", stage="audio")

    assert body["total"] == 1 and body["failed"] == 1
