"""G1: 项目时间线版本 API 测试（list / snapshot / restore / clear）。

2026-11-08 剥离账号系统：jwt 鉴权与 owner 隔离测试已随账号体系移除。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from clipwright.main import app


@pytest.fixture()
def client():
    return TestClient(app)


def _make_project(client: TestClient, name: str = "版本测试") -> str:
    resp = client.post("/api/project", json={"name": name})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


class TestVersionApi:
    def test_snapshot_list_restore_clear_flow(self, client: TestClient) -> None:
        pid = _make_project(client)

        # 初始无版本
        assert client.get(f"/api/project/{pid}/versions").json() == []

        # 无时间线时快照被拒
        no_tl = client.post(f"/api/project/{pid}/versions", json={"label": "x"})
        assert no_tl.status_code == 400

        # 写入时间线 → 快照
        tl1 = {"id": pid, "width": 1920, "height": 1080, "fps": 30, "duration_sec": 5, "tracks": []}
        assert client.put(f"/api/project/{pid}", json={"timeline": tl1}).status_code == 200
        snap = client.post(f"/api/project/{pid}/versions", json={"label": "初版"})
        assert snap.status_code == 200, snap.text
        assert snap.json()["count"] == 1

        # 再改时间线 → 第二版
        tl2 = {**tl1, "duration_sec": 9}
        client.put(f"/api/project/{pid}", json={"timeline": tl2})
        client.post(f"/api/project/{pid}/versions", json={"label": "第二版"})
        versions = client.get(f"/api/project/{pid}/versions").json()
        assert len(versions) == 2
        assert versions[-1]["label"] == "第二版"
        assert versions[-1]["is_current"] is True

        # 恢复版本 0 → 项目 timeline 回到 5s
        restore = client.post(f"/api/project/{pid}/versions/0/restore")
        assert restore.status_code == 200, restore.text
        assert restore.json()["timeline"]["duration_sec"] == 5
        assert client.get(f"/api/project/{pid}").json()["timeline"]["duration_sec"] == 5

        # 越界位置 404
        assert client.post(f"/api/project/{pid}/versions/99/restore").status_code == 404

        # 清空
        assert client.delete(f"/api/project/{pid}/versions").status_code == 200
        assert client.get(f"/api/project/{pid}/versions").json() == []
