"""批6.5 归档往返测试：导出（重名消歧/重映射）→ 导入还原。"""

from __future__ import annotations

import io
import json
import shutil
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from clipwright.main import app

client = TestClient(app)

_MEDIA_ROOT = Path("PluginData/archives_test")


def _setup_media() -> list[Path]:
    """两个不同目录、同名文件的媒体（验证重名消歧）。"""
    if _MEDIA_ROOT.exists():
        shutil.rmtree(_MEDIA_ROOT)
    d1 = _MEDIA_ROOT / "d1"
    d2 = _MEDIA_ROOT / "d2"
    d1.mkdir(parents=True, exist_ok=True)
    d2.mkdir(parents=True, exist_ok=True)
    f1 = d1 / "clip.mp4"
    f2 = d2 / "clip.mp4"
    f1.write_bytes(b"v" * 512)
    f2.write_bytes(b"v" * 512)
    return [f1, f2]


def _make_timeline(paths: list[Path]) -> dict:
    clips = []
    for i, p in enumerate(paths):
        clips.append({"id": f"c{i}", "kind": "video", "asset_id": str(p),
                      "track_id": "t1", "start_sec": i * 3.0, "duration_sec": 3.0,
                      "source_offset_sec": 0, "speed": 1, "volume": 1, "opacity": 1,
                      "keyframes": [], "metadata": {}})
    return {"id": "", "width": 1920, "height": 1080, "fps": 30, "duration_sec": 6.0,
            "tracks": [{"id": "t1", "name": "V1", "kind": "video", "index": 0,
                        "locked": False, "muted": False, "clips": clips}]}


def test_archive_roundtrip_with_collision_and_import(tmp_path) -> None:
    media = _setup_media()
    resp = client.post("/api/project", json={
        "name": "归档往返",
        "timeline": _make_timeline(media),
    })
    assert resp.status_code == 200
    pid = resp.json()["id"]

    try:
        resp = client.get(f"/api/project/{pid}/archive")
        assert resp.status_code == 200
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        names = zf.namelist()

        # 重名消歧：两个 clip.mp4 → clip.mp4 + clip_2.mp4
        media_names = [n for n in names if "/media/" in n]
        assert len(media_names) == 2
        assert len({n.rsplit("/", 1)[-1] for n in media_names}) == 2  # 名字互不相同

        data = json.loads(zf.read(f"{pid}/project.json"))
        # 重映射表 + 时间线 asset_id 已替换为归档相对路径
        assert set(data["archive_media_map"].keys()) == {str(m) for m in media}
        tl_asset_ids = [c["asset_id"] for t in data["timeline"]["tracks"]
                        for c in t["clips"]]
        assert all(a.startswith("media/") for a in tl_asset_ids)
        assert data["archive_format"] == "clipwright-v1"

        # ── 导入还原（直接上传归档本体，不再嵌套打包）──
        resp2 = client.post("/api/project/import-archive",
                            files={"file": ("archive.zip", resp.content, "application/zip")})
        assert resp2.status_code == 200, resp2.text
        imported = resp2.json()
        assert imported["status"] == "imported"
        assert imported["media_count"] == 2

        # 导入的项目时间线指向真实存在的解包媒体
        loaded = client.get(f"/api/project/{imported['id']}").json()
        tl = loaded["timeline"]
        asset_ids = [c["asset_id"] for t in tl["tracks"] for c in t["clips"]]
        assert all(Path(a).exists() for a in asset_ids)
        assert all("archives" in a for a in asset_ids)
    finally:
        client.delete(f"/api/project/{pid}")
        shutil.rmtree(_MEDIA_ROOT, ignore_errors=True)


def test_import_rejects_missing_project_json(tmp_path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("other.txt", "x")
    resp = client.post("/api/project/import-archive",
                       files={"file": ("a.zip", buf.getvalue(), "application/zip")})
    assert resp.status_code == 400


def test_import_rejects_zip_slip(tmp_path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("p/project.json", json.dumps({"name": "x"}))
        zf.writestr("../evil.txt", "x")
    resp = client.post("/api/project/import-archive",
                       files={"file": ("a.zip", buf.getvalue(), "application/zip")})
    assert resp.status_code == 400
