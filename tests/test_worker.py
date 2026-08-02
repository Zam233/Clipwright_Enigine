"""Tests for clipwright.worker.api — the remote render worker endpoints.

Covers:
1. Auth — worker-token enforcement, open mode, fallback to CLIPWRIGHT_API_TOKEN.
2. Asset upload path safety — ``../``-style filenames are sanitized to a
   hash-derived name strictly inside ``<work_dir>/assets`` (no traversal).
3. Dedup — re-uploading identical bytes returns ``stored: false``; HEAD probe.
4. Job lifecycle — empty-tracks timeline is accepted (202), reaches a terminal
   ``failed`` state WITHOUT invoking a real ffmpeg render, and a failed job's
   download returns 409.
5. Malformed job bodies — 400 for a timeline without ``tracks``, 422 for a
   non-object body.
6. Asset size limit — CLIPWRIGHT_WORKER_MAX_ASSET_MB=0 → 413.

The job tests never run a real render: ``RenderService.render`` is short-circuited
by monkeypatching ``clipwright.services.render.ffmpeg_available`` to report ffmpeg
unavailable, so it returns ``RenderResult(False)`` immediately and ``run_job``
marks the job ``failed``. Work dirs are per-test ``tmp_path`` so the real
``_cache/worker`` is never touched.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from clipwright.worker.api import app


@pytest.fixture
def client() -> TestClient:
    """A live TestClient; keeps the app portal alive for background tasks."""
    with TestClient(app) as c:
        yield c


def _ffmpeg_unavailable() -> tuple[bool, str]:
    """Stub for clipwright.services.render.ffmpeg_available (never run a render)."""
    return False, "test: no ffmpeg"


def _wait_terminal(client: TestClient, job_id: str, timeout: float = 10.0) -> dict:
    """Poll job status until a terminal state or raise on timeout."""
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        resp = client.get(f"/api/worker/jobs/{job_id}")
        assert resp.status_code == 200, resp.text
        last = resp.json()
        if last["status"] in ("completed", "failed"):
            return last
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not reach terminal state: {last}")


# ── Auth ──


def test_health_open_mode_without_token(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """No tokens configured → open development mode, requests allowed."""
    monkeypatch.delenv("CLIPWRIGHT_WORKER_TOKEN", raising=False)
    monkeypatch.delenv("CLIPWRIGHT_API_TOKEN", raising=False)
    resp = client.get("/api/worker/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_worker_token_auth(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """CLIPWRIGHT_WORKER_TOKEN set → 401 without/wrong bearer, 200 with correct."""
    monkeypatch.setenv("CLIPWRIGHT_WORKER_TOKEN", "test-secret")
    monkeypatch.delenv("CLIPWRIGHT_API_TOKEN", raising=False)

    assert client.get("/api/worker/health").status_code == 401
    assert client.get("/api/worker/health", headers={"Authorization": "Bearer wrong"}).status_code == 401
    ok = client.get("/api/worker/health", headers={"Authorization": "Bearer test-secret"})
    assert ok.status_code == 200
    assert ok.json() == {"status": "ok"}


def test_worker_token_fallback_to_api_token(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """CLIPWRIGHT_API_TOKEN fallback, and WORKER_TOKEN takes precedence over it."""
    monkeypatch.delenv("CLIPWRIGHT_WORKER_TOKEN", raising=False)
    monkeypatch.setenv("CLIPWRIGHT_API_TOKEN", "fallback-secret")

    assert client.get("/api/worker/health").status_code == 401
    ok = client.get("/api/worker/health", headers={"Authorization": "Bearer fallback-secret"})
    assert ok.status_code == 200

    monkeypatch.setenv("CLIPWRIGHT_WORKER_TOKEN", "primary-secret")
    assert client.get("/api/worker/health", headers={"Authorization": "Bearer fallback-secret"}).status_code == 401
    ok2 = client.get("/api/worker/health", headers={"Authorization": "Bearer primary-secret"})
    assert ok2.status_code == 200


# ── Asset upload: path safety, dedup, size limit ──


def test_asset_upload_path_safety(client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Traversal-style filenames are sanitized to a hash name inside assets/."""
    monkeypatch.setenv("CLIPWRIGHT_WORKER_WORK_DIR", str(tmp_path))
    assets_dir = tmp_path / "assets"
    payloads = [
        ("..\\..\\evil.mp4", b"payload-windows-traversal"),
        ("../../evil.mp4", b"payload-posix-traversal"),
    ]
    stored_names: list[str] = []
    for filename, payload in payloads:
        resp = client.post("/api/worker/assets", files={"file": (filename, payload)})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["stored"] is True
        name = f"{data['hash'][:16]}.mp4"
        stored = assets_dir / name
        assert stored.is_file()
        resolved = stored.resolve()
        assert resolved.is_relative_to(assets_dir.resolve())
        assert ".." not in resolved.parts
        stored_names.append(name)
    # only the two hash-derived files exist in assets/ — nothing escaped it
    assert len(stored_names) == len(set(stored_names))
    assert sorted(p.name for p in assets_dir.iterdir()) == sorted(stored_names)


def test_asset_dedup_and_head_probe(client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Same bytes twice → stored true then false; HEAD 200 after upload, 404 unknown."""
    monkeypatch.setenv("CLIPWRIGHT_WORKER_WORK_DIR", str(tmp_path))
    payload = b"identical-bytes-for-dedup"

    r1 = client.post("/api/worker/assets", files={"file": ("clip.mp4", payload)})
    assert r1.status_code == 200
    j1 = r1.json()
    assert j1["stored"] is True
    asset_hash = j1["hash"]
    prefix = asset_hash[:16]

    r2 = client.post("/api/worker/assets", files={"file": ("clip.mp4", payload)})
    assert r2.status_code == 200
    j2 = r2.json()
    assert j2["stored"] is False
    assert j2["hash"] == asset_hash

    assert client.head(f"/api/worker/assets/{prefix}").status_code == 200
    get = client.get(f"/api/worker/assets/{prefix}")
    assert get.status_code == 200
    assert get.content == payload
    # well-formed but unknown hash → 404
    assert client.head("/api/worker/assets/" + "0" * 16).status_code == 404


def test_asset_size_limit(client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """CLIPWRIGHT_WORKER_MAX_ASSET_MB=0 → any non-empty upload is 413."""
    monkeypatch.setenv("CLIPWRIGHT_WORKER_WORK_DIR", str(tmp_path))
    monkeypatch.setenv("CLIPWRIGHT_WORKER_MAX_ASSET_MB", "0")
    resp = client.post("/api/worker/assets", files={"file": ("big.mp4", b"x" * 1024)})
    assert resp.status_code == 413


# ── Jobs ──


def test_job_lifecycle_empty_timeline_fails_fast(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Empty-tracks job: 202 → terminal failed (no ffmpeg), download 409, unknown 404."""
    monkeypatch.setenv("CLIPWRIGHT_WORKER_WORK_DIR", str(tmp_path))
    monkeypatch.setattr("clipwright.services.render.ffmpeg_available", _ffmpeg_unavailable)

    resp = client.post("/api/worker/jobs", json={"timeline": {"tracks": []}, "params": {}})
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]
    assert job_id.startswith("job_")

    job = _wait_terminal(client, job_id)
    assert job["status"] == "failed"
    assert job["error"]

    # download of a non-completed job → 409
    assert client.get(f"/api/worker/jobs/{job_id}/download").status_code == 409
    # unknown jobs → 404 on both endpoints
    assert client.get("/api/worker/jobs/job_nonexistent").status_code == 404
    assert client.get("/api/worker/jobs/job_nonexistent/download").status_code == 404


def test_job_malformed_bodies(client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Missing tracks → 400; non-object body → 422."""
    monkeypatch.setenv("CLIPWRIGHT_WORKER_WORK_DIR", str(tmp_path))
    resp = client.post("/api/worker/jobs", json={"timeline": {"foo": 1}})
    assert resp.status_code == 400
    resp2 = client.post("/api/worker/jobs", json=[])
    assert resp2.status_code == 422
