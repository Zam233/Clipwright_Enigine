"""P3-3B: jwt 模式鉴权与 owner 数据隔离测试。

fixture 在运行时切换 settings.account_verify_mode（中间件动态读取），
避免模块级环境变量污染同进程内的其他测试（settings 为进程级单例）。
"""

from __future__ import annotations

import time

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient

from clipwright.config import settings
from clipwright.main import app

JWT_SECRET = "unit-test-shared-secret-0123456789abcdef"


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


class TestJwtAuth:
    def test_no_token_401(self, client: TestClient) -> None:
        assert client.get("/api/project").status_code == 401

    def test_jwt_allows_api(self, client: TestClient) -> None:
        headers = {"Authorization": f"Bearer {_token('user_a')}"}
        assert client.get("/api/project", headers=headers).status_code == 200

    def test_invalid_jwt_401(self, client: TestClient) -> None:
        headers = {"Authorization": "Bearer not-a-jwt"}
        assert client.get("/api/project", headers=headers).status_code == 401

    def test_wrong_secret_jwt_401(self, client: TestClient) -> None:
        forged = pyjwt.encode(
            {"sub": "x", "role": "user", "type": "access"}, "other-secret", algorithm="HS256"
        )
        headers = {"Authorization": f"Bearer {forged}"}
        assert client.get("/api/project", headers=headers).status_code == 401


class TestOwnerIsolation:
    def test_project_owner_isolation(self, client: TestClient) -> None:
        # user_a 创建项目
        a = {"Authorization": f"Bearer {_token('user_a')}"}
        b = {"Authorization": f"Bearer {_token('user_b')}"}
        create = client.post("/api/project", json={"name": "A 的项目"}, headers=a)
        assert create.status_code == 200, create.text
        pid = create.json()["id"]

        # user_b 列表看不到；直接读取 403
        listing = client.get("/api/project", headers=b).json()
        assert all(p["id"] != pid for p in listing)
        assert client.get(f"/api/project/{pid}", headers=b).status_code == 403

        # 所有者可读可删
        assert client.get(f"/api/project/{pid}", headers=a).status_code == 200
        assert client.delete(f"/api/project/{pid}", headers=a).status_code == 200

    def test_persona_owner_edit_guard(self, client: TestClient) -> None:
        a = {"Authorization": f"Bearer {_token('user_a')}"}
        b = {"Authorization": f"Bearer {_token('user_b')}"}

        manifest = {
            "persona_id": "authz_test_persona",
            "persona_name": "所有权测试",
            "parameter": {
                "persona_id": "authz_test_persona",
                "identity": {"tone": "neutral"},
            },
        }
        create = client.post("/api/persona/create", json=manifest, headers=a)
        assert create.status_code == 200, create.text

        # 他人读取允许（persona 公开可读）、更新/删除 403
        assert client.get("/api/persona/authz_test_persona", headers=b).status_code == 200
        assert client.put("/api/persona/authz_test_persona", json=create.json(), headers=b).status_code == 403
        assert client.delete("/api/persona/authz_test_persona", headers=b).status_code == 403

        # 所有者可删（清理）
        assert client.delete("/api/persona/authz_test_persona", headers=a).status_code == 200

    def test_admin_bypasses_ownership(self, client: TestClient) -> None:
        a = {"Authorization": f"Bearer {_token('user_a')}"}
        admin = {"Authorization": f"Bearer {_token('admin_1', role='admin')}"}
        create = client.post("/api/project", json={"name": "管理可见"}, headers=a)
        pid = create.json()["id"]
        assert client.get(f"/api/project/{pid}", headers=admin).status_code == 200
        assert client.delete(f"/api/project/{pid}", headers=admin).status_code == 200
