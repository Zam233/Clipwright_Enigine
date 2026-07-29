"""安全测试 — 路径遍历防护与 ID 校验。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from clipwright.security import SecurityViolation, is_safe_id, is_within, validate_id


class TestSafeId:
    def test_valid_ids(self) -> None:
        assert is_safe_id("persona_1")
        assert is_safe_id("chat_aba14848f1dd")
        assert is_safe_id("a.b-c_1")

    def test_invalid_ids(self) -> None:
        assert not is_safe_id("")
        assert not is_safe_id("../etc")
        assert not is_safe_id("a/b")
        assert not is_safe_id("a\\b")
        assert not is_safe_id(".hidden")
        assert not is_safe_id("-lead")

    def test_validate_raises(self) -> None:
        with pytest.raises(SecurityViolation):
            validate_id("../../x", "persona_id")


class TestIsWithin:
    def test_within(self, tmp_path: Path) -> None:
        child = tmp_path / "a" / "b.txt"
        child.parent.mkdir(parents=True)
        child.write_text("x")
        assert is_within(tmp_path, child)

    def test_escape(self, tmp_path: Path) -> None:
        assert not is_within(tmp_path / "sub", tmp_path)


class TestServeVideoGuard:
    @pytest.fixture
    def client(self) -> TestClient:
        from clipwright.main import app
        return TestClient(app)

    def test_rejects_outside_whitelist(self, client: TestClient, tmp_path: Path) -> None:
        secret = tmp_path / "secret.txt"
        secret.write_text("top secret")
        resp = client.get("/api/render/video", params={"path": str(secret)})
        assert resp.status_code in (400, 403, 404)

    def test_rejects_traversal(self, client: TestClient) -> None:
        resp = client.get("/api/render/video", params={"path": "renders/../../.env"})
        assert resp.status_code in (400, 403, 404)
        assert resp.status_code != 200


class TestVideoEditorGuard:
    @pytest.fixture
    def client(self) -> TestClient:
        from clipwright.main import app
        return TestClient(app)

    def test_rejects_traversal_project_id(self, client: TestClient) -> None:
        # %2F 遍历会被 Starlette 路径规范化拦截 (404)，反斜杠遍历被 ID 校验拦截 (400)
        resp = client.get("/api/video-editor/projects/..%2F..%2Fsecret")
        assert resp.status_code in (400, 404)
        resp2 = client.get("/api/video-editor/projects/..\\secret")
        assert resp2.status_code == 400

    def test_missing_project_404(self, client: TestClient) -> None:
        resp = client.get("/api/video-editor/projects/nonexistent_proj_123")
        assert resp.status_code == 404


class TestTemplateAndTypeMakerGuard:
    @pytest.fixture
    def client(self) -> TestClient:
        from clipwright.main import app
        return TestClient(app)

    def test_template_traversal_blocked(self, client: TestClient) -> None:
        resp = client.get("/api/template/..%5C..%5Csecret")
        assert resp.status_code == 400

    def test_type_maker_create_traversal_blocked(self, client: TestClient) -> None:
        resp = client.post(
            "/api/type-maker/create",
            json={"id": "../../evil_type", "name": "x"},
        )
        assert resp.status_code == 400

    def test_learning_job_traversal_blocked(self, client: TestClient) -> None:
        resp = client.post(
            "/api/learning/jobs/create",
            json={"name": "x", "dataset_id": "../../etc/passwd"},
        )
        assert resp.status_code == 400


class TestWebhookSsrf:
    @pytest.fixture
    def client(self) -> TestClient:
        from clipwright.main import app
        return TestClient(app)

    def test_register_loopback_blocked(self, client: TestClient) -> None:
        resp = client.post(
            "/api/webhook/register",
            json={"url": "http://127.0.0.1:9999/hook", "events": ["pipeline.completed"]},
        )
        assert resp.status_code == 400

    def test_register_metadata_blocked(self, client: TestClient) -> None:
        resp = client.post(
            "/api/webhook/register",
            json={"url": "http://169.254.169.254/latest/meta-data/", "events": ["pipeline.completed"]},
        )
        assert resp.status_code == 400

    def test_register_cgnat_blocked(self, client: TestClient) -> None:
        resp = client.post(
            "/api/webhook/register",
            json={"url": "http://100.64.0.1/hook", "events": ["pipeline.completed"]},
        )
        assert resp.status_code == 400


class TestProxyGuard:
    async def test_rejects_outside_whitelist(self, tmp_path: Path) -> None:
        from clipwright.services.proxy import ProxyGenerator
        src = tmp_path / "video.mp4"
        src.write_bytes(b"fake")
        result = await ProxyGenerator.generate(str(src))
        assert "error" in result

    async def test_rejects_output_dir_outside_whitelist(self, tmp_path: Path) -> None:
        from clipwright.services.proxy import ProxyGenerator
        result = await ProxyGenerator.generate("library/nonexistent.mp4", output_dir=str(tmp_path))
        assert "error" in result


class TestPersonaRepositoryGuard:
    def test_delete_traversal_blocked(self, tmp_path: Path) -> None:
        from clipwright.persona.repository import PersonaRepository
        repo = PersonaRepository(tmp_path / "personas")
        victim = tmp_path / "victim"
        victim.mkdir()
        with pytest.raises(SecurityViolation):
            repo.delete("../victim")
        assert victim.exists()

    def test_save_manifest_traversal_blocked(self, tmp_path: Path) -> None:
        from clipwright.persona.repository import PersonaRepository
        from clipwright.schema.persona import ParameterLayer, PersonaManifest
        repo = PersonaRepository(tmp_path / "personas")
        manifest = PersonaManifest(
            persona_id="../escape",
            parameter=ParameterLayer(persona_id="../escape"),
        )
        with pytest.raises(SecurityViolation):
            repo.save_manifest(manifest)
        assert not (tmp_path / "escape").exists()
