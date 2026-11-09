"""M3：只读素材视图（list/read）与越权拒绝。"""
from __future__ import annotations

import base64

import pytest

from clipwright.services.plugin_materials import (
    MaterialError,
    list_materials,
    read_material,
)
from clipwright.services.workspace import Workspace


@pytest.fixture
def project(tmp_path, monkeypatch):
    from clipwright import config

    monkeypatch.setattr(config.settings, "workspace_dir", str(tmp_path / "ws"), raising=False)
    root = Workspace("proj-1").dirs().materials
    root.mkdir(parents=True, exist_ok=True)
    (root / "cover.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 32)
    (root / "sub").mkdir()
    (root / "sub" / "note.txt").write_text("hello", encoding="utf-8")
    return root


def test_list_returns_refs_and_hashes(project):
    items = list_materials("proj-1")
    refs = {i["ref"] for i in items}
    assert "materials/cover.png" in refs
    assert "materials/sub/note.txt" in refs
    assert all(len(i["sha256"]) == 64 for i in items)


def test_read_returns_base64(project):
    item = read_material("proj-1", "materials/sub/note.txt")
    assert base64.b64decode(item["data_b64"]) == b"hello"
    assert item["mime"] == "text/plain"


def test_read_rejects_traversal(project):
    with pytest.raises(MaterialError):
        read_material("proj-1", "../project.json")
    with pytest.raises(MaterialError):
        read_material("proj-1", "C:/Windows/win.ini")


def test_read_respects_size_limit(project):
    with pytest.raises(ValueError):
        read_material("proj-1", "materials/cover.png", max_bytes=4)
