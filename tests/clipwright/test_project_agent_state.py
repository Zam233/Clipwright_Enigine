"""B20: create_project 透传 agent_state（与 update 一致）。"""

from __future__ import annotations

from clipwright.services.project_manager import ProjectManager


def test_create_persists_agent_state(tmp_path) -> None:
    pm = ProjectManager(projects_dir=tmp_path / "projects")
    agent_state = {"requirementsStatus": "brief_ready", "requirementsMessages": [{"role": "user", "content": "hi"}]}
    data = pm.create(name="p", agent_state=agent_state)

    assert data.get("agent_state") == agent_state
    loaded = pm.load(data["id"])
    assert loaded is not None
    assert loaded.get("agent_state") == agent_state


def test_create_without_agent_state_ok(tmp_path) -> None:
    pm = ProjectManager(projects_dir=tmp_path / "projects2")
    data = pm.create(name="p2")
    assert data.get("agent_state") is None
    loaded = pm.load(data["id"])
    assert loaded is not None
    assert loaded.get("agent_state") is None
