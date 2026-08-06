"""预处理清理回归测试。

- B16: ``clipwright.services.material_preprocessor.preprocess_worker`` 是空壳
  （仅 30 秒空转循环，从未执行任务），应从该模块移除，main.py 生命周期也不应再
  spawn 它。真正的后台 worker 由 ``clipwright.api.preprocess._ensure_worker`` 在
  ``/api/preprocess/submit`` 调用时懒启动，必须继续可用。
- B17: ``transcribe`` 操作未实现（Whisper 为未来工作），应从
  ``SUPPORTED_OPERATIONS`` 与 ``/api/preprocess/operations`` 端点中移除。
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import clipwright.main
import clipwright.services.material_preprocessor as material_preprocessor
from clipwright.category.registry import CategoryRegistry
from clipwright.services import async_util


@pytest.fixture(autouse=True)
def _clear_category_registry() -> None:
    """进入 app lifespan 前清空插件注册表。

    避免与收集期的顶层注册（如 test_pipeline_diag.py）及本文件多次 lifespan
    启动产生 'Plugin already registered' 冲突（同 cea09f9 的处理模式）。
    """
    CategoryRegistry.clear()


def _client() -> TestClient:
    from clipwright.main import app

    return TestClient(app)


class TestB16RemoveStubWorker:
    """B16: 空壳 preprocess_worker 应从模块与 main.py 生命周期中移除。"""

    def test_stub_worker_not_in_module(self) -> None:
        assert not hasattr(material_preprocessor, "preprocess_worker")

    def test_main_no_longer_references_stub(self) -> None:
        src = Path(clipwright.main.__file__).read_text(encoding="utf-8")
        assert "preprocess_worker" not in src

    def test_stub_startup_log_removed(self) -> None:
        mp_src = Path(material_preprocessor.__file__).read_text(encoding="utf-8")
        main_src = Path(clipwright.main.__file__).read_text(encoding="utf-8")
        assert "素材预处理 worker 已启动" not in mp_src
        assert "素材预处理 worker 已启动" not in main_src

    def test_lifespan_does_not_spawn_preprocess_worker(self, monkeypatch) -> None:
        calls: list[str] = []

        def fake_spawn_background(coro, name: str | None = None):
            calls.append(name or "")
            return None

        monkeypatch.setattr(async_util, "spawn_background", fake_spawn_background)
        client = _client()
        with client:
            client.get("/api/preprocess/operations")
        assert "preprocess-worker" not in calls


class TestB17TranscribeRemoved:
    """B17: transcribe 未实现，应从 SUPPORTED_OPERATIONS / operations 端点移除。"""

    def test_operations_list_has_no_transcribe(self) -> None:
        resp = _client().get("/api/preprocess/operations")
        assert resp.status_code == 200
        body = resp.json()
        assert "transcribe" not in body["operations"]
        assert "transcribe" not in body["descriptions"]

    def test_submit_rejects_transcribe(self) -> None:
        resp = _client().post(
            "/api/preprocess/submit",
            json={"file_path": "nonexistent_preprocess_test.mp4", "operations": ["transcribe"]},
        )
        assert resp.status_code == 400
        assert "Unsupported operations" in resp.json()["detail"]

    def test_submit_still_accepts_real_operations(self) -> None:
        resp = _client().post(
            "/api/preprocess/submit",
            json={
                "file_path": "nonexistent_preprocess_test.mp4",
                "operations": ["metadata"],
            },
        )
        assert resp.status_code == 400
        assert "File not found" in resp.json()["detail"]


class TestB16RealWorkerStillStarts:
    """B16: 移除 stub 后，/api/preprocess/submit 必须仍能启动真正的后台 worker。"""

    def test_submit_starts_real_worker(self) -> None:
        root = Path(__file__).resolve().parent.parent.parent
        src = root / "PluginData" / "preprocess_real_worker_test.mp4"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(b"not a real video; metadata extraction will fail gracefully")
        client = _client()
        try:
            with client:
                resp = client.post(
                    "/api/preprocess/submit",
                    json={"file_path": str(src), "operations": ["metadata"]},
                )
                assert resp.status_code == 200, resp.text
                task_id = resp.json()["task_id"]
                final = "queued"
                for _ in range(20):
                    tr = client.get(f"/api/preprocess/task/{task_id}")
                    assert tr.status_code == 200, tr.text
                    final = tr.json()["status"]
                    if final in ("completed", "failed"):
                        break
                    time.sleep(0.1)
                assert final in ("completed", "failed"), (
                    f"real worker never picked up task, final={final}"
                )
        finally:
            src.unlink(missing_ok=True)
