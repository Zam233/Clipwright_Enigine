"""M2：插件 scoped workspace + artifact registry。

覆盖：
- 只允许插件自己 project 命名空间内的相对路径（绝对/../盘符/跨项目拒绝）；
- 写入原子化并返回 revision，expected_revision 冲突可测；
- engine 项目根快照不可被插件覆盖；
- artifact 由宿主计算 size/sha256/mime，路径越界拒绝。
"""
from __future__ import annotations

import pytest

from clipwright.services.plugin_workspace import (
    PluginArtifactRegistry,
    PluginScope,
    PluginWorkspace,
    WorkspaceRevisionConflict,
    WorkspaceScopeError,
)


@pytest.fixture
def ws(tmp_path, monkeypatch):
    from clipwright import config

    monkeypatch.setattr(config.settings, "workspace_dir", str(tmp_path / "ws"), raising=False)
    scope = PluginScope(plugin_id="demo", project_id="proj-1", task_id="t1")
    return PluginWorkspace(scope)


class TestWorkspacePaths:
    def test_write_read_roundtrip(self, ws):
        entry = ws.write_atomic("motion/a.txt", "hello")
        assert entry["path"] == "motion/a.txt"
        got = ws.read("motion/a.txt")
        assert got["data"] == "hello"
        assert got["sha256"] == entry["sha256"]

    def test_revision_conflict(self, ws):
        first = ws.write_atomic("motion/a.txt", "v1")
        ws.write_atomic("motion/a.txt", "v2", expected_revision=first["revision"])
        with pytest.raises(WorkspaceRevisionConflict):
            ws.write_atomic("motion/a.txt", "v3", expected_revision=first["revision"])

    def test_traversal_rejected(self, ws):
        for bad in ("../project.json", "motion/../../x", "/etc/passwd", "C:/Windows/x"):
            with pytest.raises(WorkspaceScopeError):
                ws.write_atomic(bad, "x")

    def test_project_root_snapshot_untouched(self, ws):
        """engine 的 project.json/timeline.json 在上一级，插件写入必须够不到。"""
        with pytest.raises(WorkspaceScopeError):
            ws.write_atomic("../project.json", "{}")
        assert not (ws.project_root / "project.json").exists()

    def test_scope_plugin_mismatch(self):
        with pytest.raises(WorkspaceScopeError):
            PluginScope.from_payload(
                {"plugin_id": "other", "project_id": "p"}, expected_plugin="demo",
            )

    def test_scope_requires_project(self):
        with pytest.raises(WorkspaceScopeError):
            PluginScope.from_payload({}, expected_plugin="demo")


class TestArtifacts:
    def test_register_computes_metadata(self, ws):
        ws.write_atomic("motion/report.json", '{"ok": true}')
        reg = PluginArtifactRegistry(ws)
        entry = reg.register("motion/report.json", role="report")
        assert entry["mime"] == "application/json"
        assert entry["size"] == len('{"ok": true}')
        assert len(entry["sha256"]) == 64
        assert reg.get(entry["artifact_id"])["path"] == "motion/report.json"
        assert reg.raw_path(entry["artifact_id"]).is_file()

    def test_register_rejects_outside_path(self, ws):
        reg = PluginArtifactRegistry(ws)
        with pytest.raises(WorkspaceScopeError):
            reg.register("../project.json")

    def test_list_filters(self, ws):
        ws.write_atomic("motion/a.png", b"\x89PNG")
        ws.write_atomic("motion/b.json", "{}")
        reg = PluginArtifactRegistry(ws)
        reg.register("motion/a.png", role="image")
        reg.register("motion/b.json", role="report")
        assert len(reg.list()) == 2
        assert len(reg.list(role="image")) == 1
