"""M6：通用 artifact 查询与原始文件读取。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from clipwright.services.artifact_query import (
    ArtifactNotFound,
    artifact_path,
    list_artifacts,
)
from clipwright.services.workspace import Workspace


@pytest.fixture
def project(tmp_path, monkeypatch):
    from clipwright import config

    monkeypatch.setattr(config.settings, "workspace_dir", str(tmp_path / "ws"), raising=False)
    root = Workspace("proj-1").dirs().root
    plugin_root = root / "plugins" / "demo"
    (plugin_root / "motion").mkdir(parents=True)
    (plugin_root / "motion" / "pilot.mp4").write_bytes(b"video")
    (plugin_root / "motion" / "qa.json").write_text("{}", encoding="utf-8")
    reg = root / ".engine" / "artifacts"
    reg.mkdir(parents=True)
    entries = [
        {
            "artifact_id": "art_1", "plugin_id": "demo", "task_id": "t1",
            "project_id": "proj-1", "path": "motion/pilot.mp4",
            "mime": "video/mp4", "size": 5, "sha256": "x",
            "role": "render", "status": "ready", "retention": "task",
            "created_at": 100.0,
        },
        {
            "artifact_id": "art_2", "plugin_id": "demo", "task_id": "t1",
            "project_id": "proj-1", "path": "motion/qa.json",
            "mime": "application/json", "size": 2, "sha256": "y",
            "role": "report", "status": "ready", "retention": "task",
            "created_at": 200.0,
        },
    ]
    (reg / "demo.json").write_text(
        json.dumps({"artifacts": entries}), encoding="utf-8",
    )
    return root


class TestList:
    def test_sorted_desc_and_roles(self, project):
        page = list_artifacts("proj-1")
        assert [i["artifact_id"] for i in page.items] == ["art_2", "art_1"]
        assert page.total_estimate == 2

    def test_filter_role_and_task(self, project):
        page = list_artifacts("proj-1", role="render")
        assert [i["artifact_id"] for i in page.items] == ["art_1"]
        assert list_artifacts("proj-1", task_id="nope").items == []

    def test_cursor_paging(self, project):
        first = list_artifacts("proj-1", limit=1)
        assert [i["artifact_id"] for i in first.items] == ["art_2"]
        assert first.next_cursor
        second = list_artifacts("proj-1", limit=1, cursor=first.next_cursor)
        assert [i["artifact_id"] for i in second.items] == ["art_1"]
        assert second.next_cursor == ""

    def test_missing_project_rejected(self):
        with pytest.raises(ValueError):
            list_artifacts("")


class TestRaw:
    def test_returns_file_and_mime(self, project):
        path, entry = artifact_path("proj-1", "demo", "art_1")
        assert path.read_bytes() == b"video"
        assert entry["mime"] == "video/mp4"

    def test_missing_raises(self, project):
        with pytest.raises(ArtifactNotFound):
            artifact_path("proj-1", "demo", "nope")

    def test_traversal_blocked(self, project):
        reg = project / ".engine" / "artifacts" / "demo.json"
        data = json.loads(reg.read_text(encoding="utf-8"))
        data["artifacts"][0]["path"] = "../../project.json"
        reg.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(PermissionError):
            artifact_path("proj-1", "demo", "art_1")


@pytest.mark.asyncio
async def test_api_endpoints(project):
    from clipwright.api.artifacts import artifact_raw, list_artifact_api

    payload = await list_artifact_api(
        project_id="proj-1", plugin_id="", task_id="", status="", role="",
        cursor="", limit=50,
    )
    assert payload["total_estimate"] == 2
    assert payload["items"][0]["artifact_id"] == "art_2"

    response = await artifact_raw("demo", "art_1", project_id="proj-1")
    assert response.media_type == "video/mp4"
    assert Path(response.path).read_bytes() == b"video"
