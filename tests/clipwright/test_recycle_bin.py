"""A2: 项目回收站（软删除/恢复/永久删除 + list 过滤）测试。

2026-11-08 剥离账号系统：jwt 鉴权与 owner 隔离测试已随账号体系移除。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from clipwright.main import app


@pytest.fixture()
def client():
    return TestClient(app)


class TestRecycleBin:
    def test_trash_restore_purge_flow(self, client: TestClient) -> None:
        pid = client.post("/api/project", json={"name": "回收站测试"}).json()["id"]

        # 移入回收站 → 列表隐藏，trash=1 可见
        assert client.post(f"/api/project/{pid}/trash").status_code == 200
        normal = client.get("/api/project").json()
        assert all(p["id"] != pid for p in normal)
        trashed = client.get("/api/project?trash=1").json()
        assert any(p["id"] == pid and p["deleted_at"] for p in trashed)

        # 恢复 → 列表可见、回收站隐藏
        assert client.post(f"/api/project/{pid}/restore").status_code == 200
        assert any(p["id"] == pid for p in client.get("/api/project").json())
        assert all(p["id"] != pid for p in client.get("/api/project?trash=1").json())

        # 再入回收站 → 永久删除
        client.post(f"/api/project/{pid}/trash")
        assert client.delete(f"/api/project/{pid}/trash").status_code == 200
        assert all(p["id"] != pid for p in client.get("/api/project?trash=1").json())

    def test_trash_missing_project_404(self, client: TestClient) -> None:
        assert client.post("/api/project/proj_nonexistent/trash").status_code == 404
        assert client.post("/api/project/proj_nonexistent/restore").status_code == 404
