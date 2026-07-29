"""Tests for clipwright.api.voice — voice API endpoints."""

from __future__ import annotations

import io
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from clipwright.api.voice import router
from clipwright.services.voice import VoiceRecord, VoiceResult

# ── Test app ──

app = FastAPI()
app.include_router(router)

client = TestClient(app)

# ── Helpers ──

def _mock_service() -> MagicMock:
    svc = MagicMock()
    svc.clone = AsyncMock(return_value=VoiceResult(
        success=True,
        data={"id": "v1", "voice_id": "v1", "voice_name": "test", "provider": "qwen_tts"},
    ))
    svc.list_voices.return_value = [
        {"id": "v1", "voice_name": "test", "provider": "qwen_tts"},
    ]
    svc.delete_voice = MagicMock(return_value=True)
    svc.synthesize = AsyncMock(return_value=VoiceResult(
        success=True,
        data={"audio_path": "/tmp/test.mp3", "duration_sec": 2.5, "voice_id": "v1", "provider": "qwen_tts", "text": "hello"},
    ))
    svc.dub_script = AsyncMock(return_value=VoiceResult(
        success=True,
        data={
            "segments": [{"index": 0, "text": "甲"}, {"index": 1, "text": "乙"}],
            "total": 2,
            "total_duration_sec": 4.0,
        },
    ))
    return svc


# ── Tests ──


@patch("clipwright.api.voice.get_voice_service")
def test_clone_200(mock_get: MagicMock) -> None:
    mock_get.return_value = _mock_service()
    resp = client.post("/api/voice/clone", json={"voice_name": "test", "provider": "qwen_tts"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["voice_id"] == "v1"


@patch("clipwright.api.voice.get_voice_service")
def test_clone_service_error(mock_get: MagicMock) -> None:
    svc = _mock_service()
    svc.clone = AsyncMock(return_value=VoiceResult(success=False, error="bad audio"))
    mock_get.return_value = svc
    resp = client.post("/api/voice/clone", json={"voice_name": "test"})
    assert resp.status_code == 400


@patch("clipwright.api.voice.get_voice_service")
def test_list_200(mock_get: MagicMock) -> None:
    mock_get.return_value = _mock_service()
    resp = client.get("/api/voice/list")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 1


@patch("clipwright.api.voice.get_voice_service")
def test_delete_200(mock_get: MagicMock) -> None:
    mock_get.return_value = _mock_service()
    resp = client.delete("/api/voice/v1")
    assert resp.status_code == 200
    assert resp.json()["deleted"] == "v1"


@patch("clipwright.api.voice.get_voice_service")
def test_delete_404(mock_get: MagicMock) -> None:
    svc = _mock_service()
    svc.delete_voice = MagicMock(return_value=False)
    mock_get.return_value = svc
    resp = client.delete("/api/voice/nonexistent")
    assert resp.status_code == 404


@patch("clipwright.api.voice.get_voice_service")
def test_synthesize_200(mock_get: MagicMock) -> None:
    mock_get.return_value = _mock_service()
    resp = client.post("/api/voice/synthesize", json={"voice_id": "v1", "text": "hello"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["audio_path"] == "/tmp/test.mp3"
    assert data["duration_sec"] == 2.5


@patch("clipwright.api.voice.get_voice_service")
def test_synthesize_service_error(mock_get: MagicMock) -> None:
    svc = _mock_service()
    svc.synthesize = AsyncMock(return_value=VoiceResult(success=False, error="provider down"))
    mock_get.return_value = svc
    resp = client.post("/api/voice/synthesize", json={"voice_id": "v1", "text": "hello"})
    assert resp.status_code == 400


@patch("clipwright.api.voice.get_voice_service")
def test_dub_200(mock_get: MagicMock) -> None:
    mock_get.return_value = _mock_service()
    resp = client.post("/api/voice/dub", json={"voice_id": "v1", "text": "甲。乙。"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert len(data["segments"]) == 2


@patch("clipwright.api.voice.get_voice_service")
def test_dub_service_error(mock_get: MagicMock) -> None:
    svc = _mock_service()
    svc.dub_script = AsyncMock(return_value=VoiceResult(success=False, error="empty"))
    mock_get.return_value = svc
    resp = client.post("/api/voice/dub", json={"voice_id": "v1", "text": ""})
    assert resp.status_code == 400


def test_route_registered() -> None:
    """Verify voice routes are present in the FastAPI app via OpenAPI schema."""
    from clipwright.main import app as main_app
    schema = main_app.openapi()
    paths = list(schema.get("paths", {}).keys())
    assert any("/api/voice/clone" in p for p in paths)
    assert any("/api/voice/list" in p for p in paths)
    assert any("/api/voice/dub" in p for p in paths)
