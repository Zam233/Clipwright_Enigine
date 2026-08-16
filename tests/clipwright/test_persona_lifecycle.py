"""P10: Persona 复制 / 派生 / 导出 / 导入 端点测试。

覆盖 duplicate / derive / export / import 四个端点的成功路径、
冲突后缀去重与缺参错误路径。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from clipwright.persona.repository import PersonaRepository
from clipwright.schema.persona import PersonaManifest


def _seed_repo(tmp_path: Path) -> PersonaRepository:
    repo = PersonaRepository(tmp_path)
    repo.save_manifest(
        PersonaManifest(
            persona_id="per_base",
            persona_name="基础人格",
            parameter={"persona_id": "per_base", "identity": {"tone": "neutral"}},
            prompt="基础 Prompt",
        )
    )
    return repo


class TestPersonaLifecycleAPI:
    @staticmethod
    def _make_client(tmp_path: Path, monkeypatch: MonkeyPatch) -> TestClient:
        repo = _seed_repo(tmp_path)
        monkeypatch.setattr("clipwright.api.persona._repo", repo)

        app = FastAPI()
        from clipwright.api.persona import router

        app.include_router(router)
        return TestClient(app)

    def test_duplicate_creates_new_id(self, tmp_path, monkeypatch: MonkeyPatch) -> None:
        client = self._make_client(tmp_path, monkeypatch)
        resp = client.post("/api/persona/per_base/duplicate")
        assert resp.status_code == 200
        data = resp.json()
        assert data["persona_id"].startswith("per_base_copy")
        assert "副本" in data["persona_name"]
        assert data["prompt"] == "基础 Prompt"
        # 原人格未被改动
        orig = client.get("/api/persona/per_base/export")
        assert orig.status_code == 200
        assert orig.json()["persona"]["persona_id"] == "per_base"

    def test_duplicate_conflict_appends_suffix(self, tmp_path, monkeypatch: MonkeyPatch) -> None:
        client = self._make_client(tmp_path, monkeypatch)
        client.post("/api/persona/per_base/duplicate")
        resp = client.post("/api/persona/per_base/duplicate")
        assert resp.status_code == 200
        assert resp.json()["persona_id"].startswith("per_base_copy_")

    def test_derive_with_adjustments(self, tmp_path, monkeypatch: MonkeyPatch) -> None:
        client = self._make_client(tmp_path, monkeypatch)
        resp = client.post("/api/persona/derive", json={
            "base_persona_id": "per_base",
            "adjustments": "更口语化、节奏更快",
            "new_persona_name": "派生人格",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["persona_id"].startswith("per_base_derived")
        assert data["persona_name"] == "派生人格"

    def test_derive_missing_base_404(self, tmp_path, monkeypatch: MonkeyPatch) -> None:
        client = self._make_client(tmp_path, monkeypatch)
        resp = client.post("/api/persona/derive", json={"base_persona_id": "nope"})
        assert resp.status_code == 404

    def test_export_roundtrip_import(self, tmp_path, monkeypatch: MonkeyPatch) -> None:
        client = self._make_client(tmp_path, monkeypatch)
        exp = client.get("/api/persona/per_base/export")
        assert exp.status_code == 200
        payload = exp.json()
        assert payload["persona"]["persona_id"] == "per_base"

        imp = client.post("/api/persona/import", json={
            "persona": payload["persona"],
            "new_persona_id": "per_imported",
            "new_persona_name": "导入人格",
        })
        assert imp.status_code == 200
        data = imp.json()
        assert data["persona_id"] == "per_imported"
        assert data["persona_name"] == "导入人格"
        assert data["prompt"] == "基础 Prompt"

    def test_import_conflict_appends_suffix(self, tmp_path, monkeypatch: MonkeyPatch) -> None:
        client = self._make_client(tmp_path, monkeypatch)
        exp = client.get("/api/persona/per_base/export").json()["persona"]
        # 目标 id 已被占用 → 自动追加后缀
        imp = client.post("/api/persona/import", json={"persona": exp, "new_persona_id": "per_base"})
        assert imp.status_code == 200
        assert imp.json()["persona_id"].startswith("per_base_")

    def test_import_invalid_json_400(self, tmp_path, monkeypatch: MonkeyPatch) -> None:
        client = self._make_client(tmp_path, monkeypatch)
        resp = client.post("/api/persona/import", json={"persona": {"bad": "shape"}})
        assert resp.status_code == 400

    def test_routes_registered_in_main_app(self) -> None:
        from clipwright.main import app as main_app

        schema = main_app.openapi()
        paths = list(schema.get("paths", {}).keys())
        assert any(p.endswith("/duplicate") for p in paths)
        assert any(p.endswith("/derive") for p in paths)
        assert any(p.endswith("/export") for p in paths)
        assert any(p.endswith("/import") for p in paths)


class TestPersonaLearner:
    """B16: 学习器接线 — learn/stats 端点。"""

    def _make_client(self, tmp_path: Path, monkeypatch: MonkeyPatch) -> TestClient:
        repo = _seed_repo(tmp_path)
        monkeypatch.setattr("clipwright.api.persona._repo", repo)
        monkeypatch.setattr(
            "clipwright.services.persona_learner.LEARNER_DATA_DIR",
            tmp_path / "learning",
        )
        from clipwright.services.persona_learner import _learners
        _learners.clear()

        app = FastAPI()
        from clipwright.api.persona import router

        app.include_router(router)
        return TestClient(app)

    def test_learn_records_edit(self, tmp_path, monkeypatch: MonkeyPatch) -> None:
        client = self._make_client(tmp_path, monkeypatch)
        resp = client.post("/api/persona/per_base/learn", json={
            "action": "apply_transition",
            "params": {"transition_type": "dissolve"},
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["edit_count"] >= 1

        stats = client.get("/api/persona/per_base/learn/stats")
        assert stats.status_code == 200
        assert stats.json()["edit_count"] >= 1
        # 转场权重被学习
        prefs = stats.json()["preferences"]
        assert prefs["transition_weights"]["dissolve"] > 0

    def test_learn_missing_persona_404(self, tmp_path, monkeypatch: MonkeyPatch) -> None:
        client = self._make_client(tmp_path, monkeypatch)
        resp = client.post("/api/persona/nope/learn", json={"action": "x", "params": {}})
        assert resp.status_code == 404
