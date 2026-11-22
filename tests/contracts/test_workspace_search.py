"""工作区查询工具：glob / tree / read_file_range。"""

from __future__ import annotations

import asyncio

import pytest

from clipwright.tool.workspace_search import (
    GlobWorkspaceTool,
    ReadFileRangeTool,
    WorkspaceTreeTool,
)


@pytest.fixture
def ws(tmp_path, monkeypatch):
    import clipwright.security as security

    monkeypatch.setattr(security, "allowed_media_roots", lambda: [tmp_path])
    (tmp_path / "reports").mkdir()
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "reports" / "a.md").write_text("0123456789" * 20, encoding="utf-8")
    (tmp_path / "artifacts" / "art_1.json").write_text("{}", encoding="utf-8")
    return tmp_path


def test_glob_workspace(ws) -> None:
    out = asyncio.run(GlobWorkspaceTool().execute(
        pattern="**/*.json", workspace_root=str(ws),
    ))
    assert str(out.status) == "success"
    assert out.output["count"] == 1
    assert out.output["items"][0]["path"] == "artifacts/art_1.json"


def test_workspace_tree(ws) -> None:
    out = asyncio.run(WorkspaceTreeTool().execute(
        depth=2, workspace_root=str(ws),
    ))
    assert str(out.status) == "success"
    paths = {x["path"] for x in out.output["items"]}
    assert {"reports", "artifacts", "reports/a.md"} <= paths


def test_read_file_range(ws) -> None:
    out = asyncio.run(ReadFileRangeTool().execute(
        path="reports/a.md", offset=5, length=10, workspace_root=str(ws),
    ))
    assert str(out.status) == "success"
    assert out.output["content"] == "5678901234"
    assert out.output["offset"] == 5
    assert out.output["truncated"] is True
    assert out.output["next_offset"] == 15


def test_workspace_tools_reject_outside_path(ws) -> None:
    out = asyncio.run(ReadFileRangeTool().execute(
        path="../outside.txt", workspace_root=str(ws),
    ))
    assert str(out.status) == "error"
    assert "允许" in out.error
