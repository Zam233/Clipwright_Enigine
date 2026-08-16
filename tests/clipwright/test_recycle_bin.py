"""A2: 项目回收站（软删除/恢复/永久删除 + list 过滤）测试。"""

from __future__ import annotations

import time

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient

from clipwright.config import settings
from clipwright.main import app

JWT_SECRET = "unit-test-trash-secret-0123456789abcdef"


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


class TestRecycleBin:
    def test_trash_restore_purge_flow(self, client: TestClient) -> None:
        headers = {"Authorization": f"Bearer {_token('user_t')}"}
        pid = client.post("/api/project", json={"name": "回收站测试"}, headers=headers).json()["id"]

        # 移入回收站 → 列表隐藏，trash=1 可见
        assert client.post(f"/api/project/{pid}/trash", headers=headers).status_code == 200
        normal = client.get("/api/project", headers=headers).json()
        assert all(p["id"] != pid for p in normal)
        trashed = client.get("/api/project?trash=1", headers=headers).json()
        assert any(p["id"] == pid and p["deleted_at"] for p in trashed)

        # 恢复 → 列表可见、回收站隐藏
        assert client.post(f"/api/project/{pid}/restore", headers=headers).status_code == 200
        assert any(p["id"] == pid for p in client.get("/api/project", headers=headers).json())
        assert all(p["id"] != pid for p in client.get("/api/project?trash=1", headers=headers).json())

        # 再入回收站 → 永久删除
        client.post(f"/api/project/{pid}/trash", headers=headers)
        assert client.delete(f"/api/project/{pid}/trash", headers=headers).status_code == 200
        assert all(p["id"] != pid for p in client.get("/api/project?trash=1", headers=headers).json())

    def test_trash_owner_isolation(self, client: TestClient) -> None:
        a = {"Authorization": f"Bearer {_token('user_ta')}"}
        b = {"Authorization": f"Bearer {_token('user_tb')}"}
        pid = client.post("/api/project", json={"name": "越权测试"}, headers=a).json()["id"]
        assert client.post(f"/api/project/{pid}/trash", headers=b).status_code == 403
        assert client.post(f"/api/project/{pid}/restore", headers=b).status_code == 403
        assert client.delete(f"/api/project/{pid}/trash", headers=b).status_code == 403
        # owner 可正常操作（清理）
        assert client.post(f"/api/project/{pid}/trash", headers=a).status_code == 200
        assert client.delete(f"/api/project/{pid}/trash", headers=a).status_code == 200

    def test_trash_missing_project_404(self, client: TestClient) -> None:
        headers = {"Authorization": f"Bearer {_token('user_t')}"}
        assert client.post("/api/project/proj_nonexistent/trash", headers=headers).status_code == 404
        assert client.post("/api/project/proj_nonexistent/restore", headers=headers).status_code == 404
