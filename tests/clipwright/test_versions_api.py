"""G1: 项目时间线版本 API 测试（list / snapshot / restore / clear + 越权）。"""

from __future__ import annotations

import time

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient

from clipwright.config import settings
from clipwright.main import app

JWT_SECRET = "unit-test-version-secret-0123456789abcdef"


def _token(user_id: str, role: str = "user") -> str:
    return pyjwt.encode(
        {"sub": user_id, "role": role, "type": "access", "iat": int(time.time())},
        JWT_SECRET,
        algorithm="HS256",
    )


@pytest.fixture()
def client():
    prev_mode = settings.account_verify_mode
    prev_secret = settings.account_jwt_secret
    settings.account_verify_mode = "jwt"
    settings.account_jwt_secret = JWT_SECRET
    try:
        yield TestClient(app)
    finally:
        settings.account_verify_mode = prev_mode
        settings.account_jwt_secret = prev_secret


def _make_project(client: TestClient, headers: dict, name: str = "版本测试") -> str:
    resp = client.post("/api/project", json={"name": name}, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


class TestVersionApi:
    def test_snapshot_list_restore_clear_flow(self, client: TestClient) -> None:
        headers = {"Authorization": f"Bearer {_token('user_v')}"}
        pid = _make_project(client, headers)

        # 初始无版本
        assert client.get(f"/api/project/{pid}/versions", headers=headers).json() == []

        # 无时间线时快照被拒
        no_tl = client.post(f"/api/project/{pid}/versions", json={"label": "x"}, headers=headers)
        assert no_tl.status_code == 400

        # 写入时间线 → 快照
        tl1 = {"id": pid, "width": 1920, "height": 1080, "fps": 30, "duration_sec": 5, "tracks": []}
        assert client.put(f"/api/project/{pid}", json={"timeline": tl1}, headers=headers).status_code == 200
        snap = client.post(f"/api/project/{pid}/versions", json={"label": "初版"}, headers=headers)
        assert snap.status_code == 200, snap.text
        assert snap.json()["count"] == 1

        # 再改时间线 → 第二版
        tl2 = {**tl1, "duration_sec": 9}
        client.put(f"/api/project/{pid}", json={"timeline": tl2}, headers=headers)
        client.post(f"/api/project/{pid}/versions", json={"label": "第二版"}, headers=headers)
        versions = client.get(f"/api/project/{pid}/versions", headers=headers).json()
        assert len(versions) == 2
        assert versions[-1]["label"] == "第二版"
        assert versions[-1]["is_current"] is True

        # 恢复版本 0 → 项目 timeline 回到 5s
        restore = client.post(f"/api/project/{pid}/versions/0/restore", headers=headers)
        assert restore.status_code == 200, restore.text
        assert restore.json()["timeline"]["duration_sec"] == 5
        assert client.get(f"/api/project/{pid}", headers=headers).json()["timeline"]["duration_sec"] == 5

        # 越界位置 404
        assert client.post(f"/api/project/{pid}/versions/99/restore", headers=headers).status_code == 404

        # 清空
        assert client.delete(f"/api/project/{pid}/versions", headers=headers).status_code == 200
        assert client.get(f"/api/project/{pid}/versions", headers=headers).json() == []

    def test_version_owner_isolation(self, client: TestClient) -> None:
        a = {"Authorization": f"Bearer {_token('user_va')}"}
        b = {"Authorization": f"Bearer {_token('user_vb')}"}
        pid = _make_project(client, a)
        tl = {"id": pid, "width": 1920, "height": 1080, "fps": 30, "duration_sec": 1, "tracks": []}
        client.put(f"/api/project/{pid}", json={"timeline": tl}, headers=a)
        assert client.post(f"/api/project/{pid}/versions", json={"label": "v"}, headers=a).status_code == 200
        # user_b 越权：list / snapshot / restore / clear 全部 403
        assert client.get(f"/api/project/{pid}/versions", headers=b).status_code == 403
        assert client.post(f"/api/project/{pid}/versions", json={"label": "x"}, headers=b).status_code == 403
        assert client.post(f"/api/project/{pid}/versions/0/restore", headers=b).status_code == 403
        assert client.delete(f"/api/project/{pid}/versions", headers=b).status_code == 403
