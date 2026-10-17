"""api/project.py 路由层单测 — tmp 项目目录 + owner 隔离语义。

覆盖：创建归属写入、jwt 模式下列表按 owner 过滤（无主数据隐藏）、
跨用户读/改/删 403、非法 id 400、不存在 404。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from clipwright.api import project as project_api
from clipwright.services.project_manager import ProjectManager


@pytest.fixture
def env(tmp_path, monkeypatch):
    """隔离的项目目录 + 可切换的模拟登录用户。"""
    app = FastAPI()
    app.include_router(project_api.router)
    mgr = ProjectManager(projects_dir=tmp_path / "projects")
    monkeypatch.setattr(project_api, "_manager", mgr)
    # 后台缩略图线程在测试里替换为空操作（避免真实 ffmpeg 调用）
    monkeypatch.setattr(project_api, "_bg_thumbnail", lambda pid: None)

    holder: dict[str, str | None] = {"uid": None}

    def fake_uid(request):
        return holder["uid"]

    import clipwright.authz as authz

    monkeypatch.setattr(project_api, "current_user_id", fake_uid)
    monkeypatch.setattr(authz, "current_user_id", fake_uid)

    client = TestClient(app)
    client.uid = holder  # type: ignore[attr-defined]
    return client


def _as(client, uid: str | None) -> None:
    client.uid["uid"] = uid  # type: ignore[attr-defined]


class TestCreateAndOwnership:
    def test_create_without_uid_has_no_owner(self, env) -> None:
        _as(env, None)
        resp = env.post("/api/project", json={"name": "p1"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"].startswith("proj_")
        assert not data.get("owner_id")
        # off/token 模式列出全部
        names = [p["name"] for p in env.get("/api/project").json()]
        assert "p1" in names

    def test_create_with_uid_writes_owner(self, env) -> None:
        _as(env, "u1")
        data = env.post("/api/project", json={"name": "mine"}).json()
        assert data["owner_id"] == "u1"
        got = env.get(f"/api/project/{data['id']}").json()
        assert got["owner_id"] == "u1"

    def test_list_filters_by_owner_and_hides_ownerless(self, env) -> None:
        _as(env, "u1")
        mine = env.post("/api/project", json={"name": "a"}).json()["id"]
        _as(env, "u2")
        theirs = env.post("/api/project", json={"name": "b"}).json()["id"]
        _as(env, None)
        orphan = env.post("/api/project", json={"name": "c"}).json()["id"]

        _as(env, "u1")
        ids = {p["id"] for p in env.get("/api/project").json()}
        assert ids == {mine}  # 只见自己的；u2 的与无主的都隐藏

        _as(env, None)
        ids_all = {p["id"] for p in env.get("/api/project").json()}
        assert ids_all == {mine, theirs, orphan}


class TestOwnerGuards:
    def test_get_other_owner_project_403(self, env) -> None:
        _as(env, "u1")
        pid = env.post("/api/project", json={"name": "a"}).json()["id"]
        _as(env, "u2")
        assert env.get(f"/api/project/{pid}").status_code == 403

    def test_get_ownerless_project_403_in_jwt_mode(self, env) -> None:
        _as(env, None)
        pid = env.post("/api/project", json={"name": "legacy"}).json()["id"]
        _as(env, "u1")
        assert env.get(f"/api/project/{pid}").status_code == 403

    def test_update_other_owner_403(self, env) -> None:
        _as(env, "u1")
        pid = env.post("/api/project", json={"name": "a"}).json()["id"]
        _as(env, "u2")
        resp = env.put(f"/api/project/{pid}", json={"name": "hacked"})
        assert resp.status_code == 403
        _as(env, "u1")
        assert env.put(f"/api/project/{pid}", json={"name": "renamed"}).status_code == 200

    def test_delete_other_owner_403(self, env) -> None:
        _as(env, "u1")
        pid = env.post("/api/project", json={"name": "a"}).json()["id"]
        _as(env, "u2")
        assert env.delete(f"/api/project/{pid}").status_code == 403
        _as(env, "u1")
        assert env.delete(f"/api/project/{pid}").status_code == 200

    def test_missing_project_404(self, env) -> None:
        _as(env, None)
        assert env.get("/api/project/proj_does_not_exist").status_code == 404

    def test_invalid_project_id_400(self, env) -> None:
        _as(env, None)
        assert env.get("/api/project/../etc").status_code in (400, 404)
        assert env.get("/api/project/bad id!").status_code == 400


class TestTimelineShapeGuard:
    """P0-01：残缺 timeline（元数据桩）不得覆盖磁盘上的完整时间线。"""

    def _create_with_timeline(self, env) -> str:
        _as(env, None)
        timeline = {
            "id": "tl_1",
            "width": 1920,
            "height": 1080,
            "fps": 30,
            "duration_sec": 10,
            "tracks": [
                {"id": "t1", "kind": "video", "clips": [{"id": "c1", "kind": "video", "start_sec": 0, "duration_sec": 5}]}
            ],
        }
        return env.post("/api/project", json={"name": "big", "timeline": timeline}).json()["id"]

    def test_metadata_stub_timeline_does_not_overwrite(self, env) -> None:
        pid = self._create_with_timeline(env)
        stub = {"id": "tl_1", "duration_sec": 10, "track_count": 1, "clip_count": 1, "last_edit_at": "x"}
        resp = env.put(f"/api/project/{pid}", json={"timeline": stub})
        assert resp.status_code == 200
        got = env.get(f"/api/project/{pid}").json()
        # 完整 tracks 保留，桩未落盘
        assert got["timeline"]["tracks"][0]["clips"][0]["id"] == "c1"
        assert "track_count" not in got["timeline"]

    def test_non_dict_timeline_ignored(self, env) -> None:
        pid = self._create_with_timeline(env)
        resp = env.put(f"/api/project/{pid}", json={"timeline": "junk"})
        assert resp.status_code == 200
        got = env.get(f"/api/project/{pid}").json()
        assert isinstance(got["timeline"].get("tracks"), list)

    def test_valid_timeline_still_overwrites(self, env) -> None:
        pid = self._create_with_timeline(env)
        new_tl = {"id": "tl_1", "tracks": [{"id": "t2", "kind": "video", "clips": []}]}
        resp = env.put(f"/api/project/{pid}", json={"timeline": new_tl})
        assert resp.status_code == 200
        got = env.get(f"/api/project/{pid}").json()
        assert got["timeline"]["tracks"][0]["id"] == "t2"

    def test_absent_timeline_key_does_not_touch(self, env) -> None:
        pid = self._create_with_timeline(env)
        resp = env.put(f"/api/project/{pid}", json={"name": "renamed-only"})
        assert resp.status_code == 200
        got = env.get(f"/api/project/{pid}").json()
        assert got["timeline"]["tracks"][0]["clips"][0]["id"] == "c1"
        assert got["name"] == "renamed-only"

    def test_empty_tracks_list_is_valid_shape(self, env) -> None:
        """tracks=[] 是合法形状（用户清空时间线），必须允许覆盖。"""
        pid = self._create_with_timeline(env)
        resp = env.put(f"/api/project/{pid}", json={"timeline": {"id": "tl_1", "tracks": []}})
        assert resp.status_code == 200
        got = env.get(f"/api/project/{pid}").json()
        assert got["timeline"]["tracks"] == []
