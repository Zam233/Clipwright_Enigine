"""Tests for ProjectManager — CRUD, id validation, metadata operations."""

from __future__ import annotations

import pytest
from pathlib import Path

# Patch settings before import so ProjectManager uses tmp_path
import clipwright.config as _cfg


@pytest.fixture(autouse=True)
def _patch_project_dir(tmp_path, monkeypatch):
    """Point settings.project_dir to tmp_path for all tests."""
    monkeypatch.setattr(_cfg.settings, "project_dir", tmp_path)


from clipwright.services.project_manager import ProjectManager, _safe_id


@pytest.fixture
def pm(tmp_path):
    return ProjectManager(projects_dir=tmp_path)


# ── _safe_id ──

def test_safe_id_valid():
    assert _safe_id("proj_abc123") == "proj_abc123"
    assert _safe_id("my-project_1") == "my-project_1"
    # Max length 64
    assert _safe_id("a" * 64) == "a" * 64


def test_safe_id_rejects():
    with pytest.raises(ValueError):
        _safe_id("../etc/passwd")
    with pytest.raises(ValueError):
        _safe_id("")  # empty
    with pytest.raises(ValueError):
        _safe_id("has space")
    with pytest.raises(ValueError):
        _safe_id("a" * 65)  # too long (max 64)
    with pytest.raises(ValueError):
        _safe_id("-no-start")  # must start with alnum


# ── create ──

def test_create_returns_dict_with_id(pm):
    result = pm.create(name="Test")
    assert result["id"].startswith("proj_")
    assert result["name"] == "Test"
    assert result["timeline"] is None
    assert result["folder"] == ""
    assert result["tags"] == []
    assert "created_at" in result
    assert "updated_at" in result


def test_create_default_name(pm):
    result = pm.create()
    assert result["name"].startswith("Project ")


def test_create_persists_to_disk(pm, tmp_path):
    result = pm.create(name="Disk Test")
    project_file = tmp_path / result["id"] / "project.json"
    assert project_file.exists()


# ── save + load round-trip ──

def test_save_and_load(pm):
    created = pm.create(name="Round Trip")
    pid = created["id"]
    # Update timeline via save
    updated = pm.save(pid, {"timeline": {"tracks": []}})
    assert updated["timeline"] == {"tracks": []}
    assert updated["id"] == pid  # id preserved
    # Load
    loaded = pm.load(pid)
    assert loaded is not None
    assert loaded["timeline"] == {"tracks": []}
    assert loaded["name"] == "Round Trip"


def test_save_nonexistent_raises(pm):
    with pytest.raises(FileNotFoundError):
        pm.save("proj_nonexistent", {"name": "nope"})


def test_save_preserves_immutable_id(pm):
    created = pm.create()
    pid = created["id"]
    result = pm.save(pid, {"id": "proj_hacked"})
    assert result["id"] == pid  # id not overwritten


# ── load ──

def test_load_nonexistent_returns_none(pm):
    assert pm.load("proj_nothing") is None


# ── delete ──

def test_delete_removes_directory(pm, tmp_path):
    created = pm.create()
    pid = created["id"]
    assert (tmp_path / pid).exists()
    assert pm.delete(pid) is True
    assert not (tmp_path / pid).exists()


def test_delete_nonexistent_returns_false(pm):
    assert pm.delete("proj_nothing") is False


# ── list_projects ──

def test_list_projects(pm):
    pm.create(name="A")
    pm.create(name="B")
    projects = pm.list_projects()
    assert len(projects) == 2


def test_list_projects_empty(pm):
    assert pm.list_projects() == []


def test_list_projects_filter_folder(pm):
    pm.create(name="In folder", folder="work")
    pm.create(name="No folder", folder="")
    result = pm.list_projects(folder="work")
    assert len(result) == 1
    assert result[0]["name"] == "In folder"


def test_list_projects_filter_tag(pm):
    pm.create(name="Tagged", tags=["important"])
    pm.create(name="Not tagged", tags=[])
    result = pm.list_projects(tag="important")
    assert len(result) == 1
    assert result[0]["name"] == "Tagged"


# ── rename ──

def test_rename(pm):
    created = pm.create(name="Old Name")
    result = pm.rename(created["id"], "New Name")
    assert result["name"] == "New Name"
    # Verify persisted
    loaded = pm.load(created["id"])
    assert loaded["name"] == "New Name"


def test_rename_nonexistent_raises(pm):
    with pytest.raises(FileNotFoundError):
        pm.rename("proj_nothing", "X")


# ── set_folder ──

def test_set_folder(pm):
    created = pm.create()
    result = pm.set_folder(created["id"], "archived")
    assert result["folder"] == "archived"


# ── add_tag / remove_tag ──

def test_add_tag(pm):
    created = pm.create()
    result = pm.add_tag(created["id"], "v1")
    assert "v1" in result["tags"]


def test_add_tag_no_dupes(pm):
    created = pm.create()
    pm.add_tag(created["id"], "v1")
    result = pm.add_tag(created["id"], "v1")
    assert result["tags"].count("v1") == 1


def test_remove_tag(pm):
    created = pm.create()
    pm.add_tag(created["id"], "v1")
    result = pm.remove_tag(created["id"], "v1")
    assert "v1" not in result["tags"]


def test_remove_nonexistent_tag_noop(pm):
    created = pm.create()
    result = pm.remove_tag(created["id"], "nonexistent")
    assert result["tags"] == []


# ── set_thumbnail ──

def test_set_thumbnail(pm):
    created = pm.create()
    result = pm.set_thumbnail(created["id"], "/path/to/thumb.jpg")
    assert result["thumbnail"] == "/path/to/thumb.jpg"
