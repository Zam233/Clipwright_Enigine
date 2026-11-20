"""X02：维护窗口探针可执行化（离线 TestClient，不依赖真实 provider/数据）。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from clipwright.main import app
from scripts.x02_probe import run_probes


def test_x02_probe_list_all_pass():
    client = TestClient(app)
    results = run_probes(client)
    failed = [(name, detail) for name, ok, detail in results if not ok]
    assert not failed, f"探针失败：{failed}"
    assert len(results) >= 5
