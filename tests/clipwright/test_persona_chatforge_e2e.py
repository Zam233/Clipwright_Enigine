"""Persona ChatForge API E2E：创建会话→上传→PATCH→commit→读 Persona。"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from clipwright.config import settings
from clipwright.persona.repository import PersonaRepository
from clipwright.services.chat_forge import ChatForge


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from clipwright.api import chat_forge as chat_api
    from clipwright.api import persona as persona_api

    monkeypatch.setattr(ChatForge, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(ChatForge, "ATTACHMENTS_DIR", tmp_path / "attachments")
    monkeypatch.setattr(settings, "persona_dir", tmp_path / "personas")
    monkeypatch.setattr(chat_api, "_forge", ChatForge())
    monkeypatch.setattr(persona_api, "_repo", PersonaRepository(tmp_path / "personas"))
    from clipwright.main import app
    return TestClient(app)


def test_full_chatforge_flow(client: TestClient) -> None:
    sid = client.post("/api/persona/forge/chat/start", json={}).json()["session_id"]

    up = client.post(
        f"/api/persona/forge/chat/sessions/{sid}/attachments",
        files={"file": ("a.txt", b"hello", "text/plain")},
    )
    assert up.status_code == 200

    patch = client.patch(
        f"/api/persona/forge/chat/sessions/{sid}/draft",
        json={"parameter_patch": {"identity": {"tone": "sharp"}}},
    )
    assert patch.status_code == 200

    detail = client.get(f"/api/persona/forge/chat/sessions/{sid}")
    assert detail.status_code == 200
    assert detail.json()["persona_draft"]["identity"]["tone"] == "sharp"
    # commit 不依赖 LLM；prompt 由 user 消息生成
    commit = client.post("/api/persona/forge/chat/commit", json={
        "session_id": sid, "persona_id": "chat_e2e", "persona_name": "E2E",
    })
    assert commit.status_code == 200, commit.text
    got = client.get("/api/persona/chat_e2e")
    assert got.status_code == 200
