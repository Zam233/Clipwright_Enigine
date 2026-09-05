"""M6: MG 生成记录持久化与 generation_id 预览接线测试。"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient


class TestMgStorageGenerations:
    def test_save_load_roundtrip(self, tmp_path) -> None:
        from clipwright.animation.mg.storage import MGStorage
        st = MGStorage(base_dir=tmp_path)
        saved = st.save_generation({"animation_id": "m1", "elements": [{"t": "text"}]})
        gid = saved["generation_id"]
        assert gid.startswith("gen_")
        record = st.load_generation(gid)
        assert record is not None
        assert record["mg_def"]["animation_id"] == "m1"
        assert record["created_at"]

    def test_list_generations_summary(self, tmp_path) -> None:
        from clipwright.animation.mg.storage import MGStorage
        st = MGStorage(base_dir=tmp_path)
        st.save_generation({"animation_id": "a", "elements": [1, 2], "duration_sec": 3})
        st.save_generation({"animation_id": "b", "elements": [1]})
        items = st.list_generations(limit=10)
        assert len(items) == 2
        assert {i["animation_id"] for i in items} == {"a", "b"}
        assert items[0]["created_at"] >= items[-1]["created_at"]  # 新→旧

    def test_load_missing_returns_none(self, tmp_path) -> None:
        from clipwright.animation.mg.storage import MGStorage
        st = MGStorage(base_dir=tmp_path)
        assert st.load_generation("gen_missing") is None


class TestMgGenerationApi:
    def test_generation_endpoints(self, monkeypatch) -> None:
        from clipwright.main import app
        from clipwright.animation.mg import storage as mg_storage

        gid = "gen_20260906_000000_ab12cd34"

        def fake_load(self, g):
            if g == gid:
                return {"generation_id": gid, "mg_def": {"elements": [1]}, "created_at": "t"}
            return None

        monkeypatch.setattr(mg_storage.MGStorage, "load_generation", fake_load)
        monkeypatch.setattr(
            mg_storage.MGStorage, "list_generations",
            lambda self, limit=50: [{"generation_id": gid, "created_at": "t",
                                     "animation_id": "x", "element_count": 1,
                                     "duration_sec": 2.0}][:limit],
        )

        client = TestClient(app)
        # 列表 → 摘要数组
        lst = client.get("/api/animation/mg/generations?limit=5")
        assert lst.status_code == 200
        assert lst.json()[0]["generation_id"] == gid
        # 命中记录 → 全文
        ok = client.get(f"/api/animation/mg/generations/{gid}")
        assert ok.status_code == 200
        assert ok.json()["mg_def"]["elements"] == [1]
        # 不存在 → 404；非法 id → 400
        assert client.get("/api/animation/mg/generations/gen_missing_00000000").status_code == 404
        assert client.get("/api/animation/mg/generations/..%5Cevil").status_code in (400, 404)
        assert client.get("/api/animation/mg/generations/bad id!").status_code == 400


class TestGeneratorPersistence:
    def test_build_success_persists_generation(self, monkeypatch) -> None:
        """_build_success 成功路径调用 MGStorage.save_generation（M6 接线）。"""
        saved: list[tuple[dict, str]] = []

        class FakeStorage:
            def __init__(self, base_dir=None):
                pass
            def save_generation(self, mg_def, generation_id=""):
                saved.append((mg_def, generation_id))
                return {"generation_id": generation_id, "path": "x"}

        monkeypatch.setattr("clipwright.animation.mg.storage.MGStorage", FakeStorage)

        from clipwright.animation.mg.generator import MGGenerator
        gen = MGGenerator.__new__(MGGenerator)  # 跳过重型 __init__
        monkeypatch.setattr(gen, "_trace_event", lambda *a, **k: None, raising=False)

        async def fake_html(*a, **k):
            return "<html>ok</html>"
        monkeypatch.setattr(gen, "_render_html_no_residuals", fake_html, raising=False)

        mg_def = {"animation_id": "x", "elements": [], "duration_sec": 2.0,
                  "canvas": {"width": 1920, "height": 1080}}
        result = asyncio.run(gen._build_success(dict(mg_def), "llm"))

        assert result["success"] is True
        assert result["generation_id"].startswith("gen_")
        assert len(saved) == 1 and saved[0][1] == result["generation_id"]
