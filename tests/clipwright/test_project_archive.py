"""P8: 项目归档 zip 导出测试。"""

from __future__ import annotations

import io
import json
import zipfile

from fastapi.testclient import TestClient

from clipwright.main import app

client = TestClient(app)


def _create_project_with_media(tmp_path) -> tuple[str, str]:
    """创建带时间线媒体引用的项目，返回 (project_id, media_path)。"""
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"fake-mp4")
    resp = client.post("/api/project", json={
        "name": "归档测试",
        "timeline": {
            "id": "", "width": 1920, "height": 1080, "fps": 30, "duration_sec": 5,
            "tracks": [{"id": "t1", "name": "V1", "kind": "video", "index": 0,
                        "locked": False, "muted": False, "clips": [
                            {"id": "c1", "kind": "video", "asset_id": str(media),
                             "track_id": "t1", "start_sec": 0, "duration_sec": 5,
                             "source_offset_sec": 0, "speed": 1, "volume": 1, "opacity": 1,
                             "keyframes": [], "metadata": {}}]}],
        },
    })
    assert resp.status_code == 200, resp.text
    return resp.json()["id"], str(media)


def test_archive_zip_contains_project_and_media(tmp_path) -> None:
    pid, media_path = _create_project_with_media(tmp_path)
    try:
        resp = client.get(f"/api/project/{pid}/archive")
        assert resp.status_code == 200
        assert resp.headers.get("content-type") == "application/zip"
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        names = zf.namelist()
        assert f"{pid}/project.json" in names
        # project.json 内容有效
        data = json.loads(zf.read(f"{pid}/project.json"))
        assert data["name"] == "归档测试"
        # tmp_path 不在媒体白名单内 → 媒体文件被安全跳过（防路径穿越），仅归档 JSON
        assert not any(n.startswith(f"{pid}/media/") for n in names)
    finally:
        client.delete(f"/api/project/{pid}")


def test_archive_unknown_project_404() -> None:
    resp = client.get("/api/project/proj_nonexistent/archive")
    assert resp.status_code == 404
