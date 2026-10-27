"""P9: 素材治理 — 哈希去重 / used_count / 治理摘要 测试。"""

from __future__ import annotations

import pytest

from clipwright.services.asset_manager import AssetManager, _sha256_of
from clipwright.config import settings


@pytest.fixture()
def isolated_manager(tmp_path, monkeypatch):
    """每个测试独立的项目目录（monkeypatch project_dir）。"""
    monkeypatch.setattr(settings, "project_dir", tmp_path / "projects")
    monkeypatch.setattr(settings, "library_dir", tmp_path / "library")
    return AssetManager(project_id="proj_test")


@pytest.mark.asyncio
async def test_import_dedup_same_content(isolated_manager, tmp_path) -> None:
    """同内容文件二次导入 → 复用既有 asset（deduplicated=True + used_count 递增）。

    B9（2026-10-28 PRD）：复用不再用 error="deduplicated" 表达——那是幂等成功，
    不是错误；API 层据此返回 200 而非 400。
    """
    a = tmp_path / "a.mp4"
    b = tmp_path / "b.mp4"
    a.write_bytes(b"\x00" * 100)
    b.write_bytes(b"\x00" * 100)
    first = await isolated_manager.import_file(a)
    assert first.sha256 == _sha256_of(a)
    assert first.used_count == 1
    assert first.deduplicated is False
    second = await isolated_manager.import_file(b)
    assert second.asset_id == first.asset_id
    assert second.deduplicated is True
    assert second.status == "reused"
    assert second.error == ""
    assert second.used_count == 2
    # 库内条目仍在，且状态/文件名未被复用副本污染
    stored = isolated_manager.get(first.asset_id)
    assert stored is not None and stored.status == "ok"
    assert stored.used_count == 2


def test_upload_same_audio_twice_returns_200_and_reused(isolated_manager) -> None:
    """二次上传同内容音频 → HTTP 200 + deduplicated=True（回归用户报的 "deduplicated" 提示）。"""
    from fastapi.testclient import TestClient
    from clipwright.main import app
    from clipwright.api import asset as asset_api

    asset_api._managers.clear()
    asset_api._managers["proj_test"] = isolated_manager
    client = TestClient(app)
    data = b"ID3" + b"\x11" * 256
    first = client.post(
        "/api/asset/upload?project_id=proj_test",
        files={"file": ("voice.mp3", data, "audio/mpeg")},
    )
    assert first.status_code == 200, first.text
    body1 = first.json()
    assert body1["deduplicated"] is False
    second = client.post(
        "/api/asset/upload?project_id=proj_test",
        files={"file": ("voice-again.mp3", data, "audio/mpeg")},
    )
    assert second.status_code == 200, second.text
    body2 = second.json()
    assert body2["asset_id"] == body1["asset_id"]
    assert body2["file_path"] == body1["file_path"]
    assert body2["deduplicated"] is True
    assert body2["status"] == "reused"
    assert body2["used_count"] == body1["used_count"] + 1
    # 复用不改库内文件名（回传的仍是首次入库名）
    assert body2["filename"] == body1["filename"]
    asset_api._managers.clear()


@pytest.mark.asyncio
async def test_import_no_dedup_different_content(isolated_manager, tmp_path) -> None:
    """不同内容 → 独立素材。"""
    a = tmp_path / "a.mp4"
    b = tmp_path / "b.mp4"
    a.write_bytes(b"\x00" * 100)
    b.write_bytes(b"\x01" * 100)
    first = await isolated_manager.import_file(a)
    second = await isolated_manager.import_file(b)
    assert first.asset_id != second.asset_id


def test_sha256_chunked(tmp_path) -> None:
    import hashlib
    data = b"clipwright-hash-test" * 100
    p = tmp_path / "h.bin"
    p.write_bytes(data)
    expected = hashlib.sha256(data).hexdigest()
    assert _sha256_of(p) == expected


def test_governance_summary_returns_shape(isolated_manager) -> None:
    import asyncio
    asyncio.run(isolated_manager.import_file(_make_file("s.mp4", b"\x00" * 50)))
    from fastapi.testclient import TestClient
    from clipwright.main import app
    client = TestClient(app)
    resp = client.get("/api/asset/governance/summary?project_id=proj_test")
    assert resp.status_code == 200
    body = resp.json()
    assert "total" in body and "deduplicated" in body and "total_uses" in body


def _make_file(name: str, data: bytes):
    import tempfile
    f = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
    f.write(data)
    f.close()
    import pathlib
    return pathlib.Path(f.name)
