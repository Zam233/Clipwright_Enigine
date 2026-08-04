"""Persona vision_prompt 端到端测试。

覆盖：schema 序列化 / loader / repository / API / ChatForge 提交，
以及关键的 no-clobber 守卫（绝不覆盖已存在的 vision_prompt.md）。
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from clipwright.persona.loader import load_persona_manifest
from clipwright.persona.repository import PersonaRepository
from clipwright.schema.persona import PersonaManifest
from clipwright.services.chat_forge import ChatForge, ChatForgeSession


# ── Helpers ──


def _write_persona_dir(pdir, persona_id: str = "t1") -> None:
    """在 tmp 目录写入最小的 persona.yaml。"""
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "persona.yaml").write_text(
        f"persona_id: {persona_id}\npersona_name: test\n",
        encoding="utf-8",
    )


def _repo_at(tmp_path) -> PersonaRepository:
    return PersonaRepository(tmp_path)


# ── Schema ──


class TestPersonaManifestVisionPrompt:
    def test_roundtrip_serialization_contains_vision_prompt(self) -> None:
        manifest = PersonaManifest(persona_id="vp1", vision_prompt="深色背景，科技感配色")
        data = manifest.model_dump(mode="json")
        assert data["vision_prompt"] == "深色背景，科技感配色"

    def test_default_is_none(self) -> None:
        manifest = PersonaManifest(persona_id="vp2")
        assert manifest.vision_prompt is None


# ── Loader ──


class TestLoaderVisionPrompt:
    def test_load_persona_manifest_reads_vision_prompt(self, tmp_path) -> None:
        pdir = tmp_path / "vp_persona"
        _write_persona_dir(pdir, persona_id="vp_persona")
        text = "画面：深色科技风；配色：#0E101A；转场：硬切"
        (pdir / "vision_prompt.md").write_text(text, encoding="utf-8")

        manifest = load_persona_manifest(pdir)
        assert manifest.vision_prompt == text


# ── Repository ──


class TestRepositoryVisionPrompt:
    def test_save_manifest_writes_vision_prompt(self, tmp_path) -> None:
        repo = _repo_at(tmp_path)
        repo.save_manifest(PersonaManifest(persona_id="r1", vision_prompt="浅色极简"))

        vp_path = tmp_path / "r1" / "vision_prompt.md"
        assert vp_path.exists()
        assert vp_path.read_text(encoding="utf-8") == "浅色极简"

    def test_load_manifest_returns_vision_prompt(self, tmp_path) -> None:
        repo = _repo_at(tmp_path)
        pdir = tmp_path / "r2"
        _write_persona_dir(pdir, persona_id="r2")
        (pdir / "vision_prompt.md").write_text("内容A", encoding="utf-8")

        manifest = repo.load_manifest("r2")
        assert manifest.vision_prompt == "内容A"

    def test_save_vision_prompt_overwrites_explicit(self, tmp_path) -> None:
        """显式保存应允许覆盖（用户主动编辑）。"""
        repo = _repo_at(tmp_path)
        pdir = tmp_path / "r3"
        _write_persona_dir(pdir, persona_id="r3")

        repo.save_vision_prompt("r3", "第一版")
        assert (pdir / "vision_prompt.md").read_text(encoding="utf-8") == "第一版"

        repo.save_vision_prompt("r3", "第二版")
        assert (pdir / "vision_prompt.md").read_text(encoding="utf-8") == "第二版"

    def test_save_manifest_noclobber_existing_vision_prompt(self, tmp_path) -> None:
        """save_manifest 遇到已存在的 vision_prompt.md 不得覆盖。"""
        repo = _repo_at(tmp_path)
        pdir = tmp_path / "r4"
        _write_persona_dir(pdir, persona_id="r4")
        original = "既有用户手写内容"
        (pdir / "vision_prompt.md").write_text(original, encoding="utf-8")

        repo.save_manifest(PersonaManifest(persona_id="r4", vision_prompt="自动生成的另一版"))
        assert (pdir / "vision_prompt.md").read_text(encoding="utf-8") == original


# ── API ──


class TestVisionPromptAPI:
    @staticmethod
    def _make_client(tmp_path, monkeypatch: MonkeyPatch) -> TestClient:
        repo = PersonaRepository(tmp_path)
        _write_persona_dir(tmp_path / "vp_api", persona_id="vp_api")
        monkeypatch.setattr("clipwright.api.persona._repo", repo)

        app = FastAPI()
        from clipwright.api.persona import router
        app.include_router(router)
        return TestClient(app)

    def test_put_then_get_roundtrip(self, tmp_path, monkeypatch: MonkeyPatch) -> None:
        client = self._make_client(tmp_path, monkeypatch)
        text = "画面：胶片质感；配色：暖橙；转场：溶解"

        resp = client.put("/api/persona/vp_api/vision-prompt", json={"vision_prompt": text})
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        resp = client.get("/api/persona/vp_api/vision-prompt")
        assert resp.status_code == 200
        assert resp.json()["persona_id"] == "vp_api"
        assert resp.json()["vision_prompt"] == text

    def test_get_returns_empty_when_missing(self, tmp_path, monkeypatch: MonkeyPatch) -> None:
        client = self._make_client(tmp_path, monkeypatch)
        resp = client.get("/api/persona/vp_api/vision-prompt")
        assert resp.status_code == 200
        assert resp.json()["vision_prompt"] == ""

    def test_get_missing_persona_404(self, tmp_path, monkeypatch: MonkeyPatch) -> None:
        client = self._make_client(tmp_path, monkeypatch)
        resp = client.get("/api/persona/does_not_exist/vision-prompt")
        assert resp.status_code == 404

    def test_put_missing_persona_404(self, tmp_path, monkeypatch: MonkeyPatch) -> None:
        client = self._make_client(tmp_path, monkeypatch)
        resp = client.put("/api/persona/does_not_exist/vision-prompt", json={"vision_prompt": "x"})
        assert resp.status_code == 404

    def test_route_registered_in_main_app(self) -> None:
        from clipwright.main import app as main_app
        schema = main_app.openapi()
        paths = list(schema.get("paths", {}).keys())
        assert any("/api/persona/{persona_id}/vision-prompt" in p for p in paths)


# ── ChatForge commit ──


class TestChatForgeCommitVisionPrompt:
    @staticmethod
    def _make_session(session_id: str, messages: list[dict]) -> ChatForgeSession:
        session = ChatForgeSession(session_id=session_id)
        session.messages = messages
        return session

    def test_commit_generates_vision_prompt(
        self, tmp_path, monkeypatch: MonkeyPatch
    ) -> None:
        from clipwright.config import settings

        monkeypatch.setattr(settings, "persona_dir", tmp_path)
        forge = ChatForge()
        session = self._make_session("vp_commit", [
            {"role": "user", "content": "我喜欢深色科技风画面，配色用蓝紫渐变。"},
            {"role": "assistant", "content": "收到，正在记录你的画面偏好。"},
            {"role": "user", "content": "转场用硬切，字幕用打字机动画。"},
        ])
        forge._sessions["vp_commit"] = session

        import asyncio
        asyncio.run(forge.commit("vp_commit", persona_id="vp_commit"))

        vp_path = tmp_path / "vp_commit" / "vision_prompt.md"
        assert vp_path.exists()
        content = vp_path.read_text(encoding="utf-8")
        assert "# ChatForge 对话生成的视觉需求 Prompt" in content
        assert "深色科技风画面" in content
        assert "蓝紫渐变" in content
        assert "打字机动画" in content

    def test_commit_skips_non_visual_statements(
        self, tmp_path, monkeypatch: MonkeyPatch
    ) -> None:
        """仅包含非视觉描述（无关键词）时，不生成 vision_prompt.md。"""
        from clipwright.config import settings

        monkeypatch.setattr(settings, "persona_dir", tmp_path)
        forge = ChatForge()
        session = self._make_session("vp_nonvis", [
            {"role": "user", "content": "我说话语速偏快，喜欢用短句。"},
        ])
        forge._sessions["vp_nonvis"] = session

        import asyncio
        asyncio.run(forge.commit("vp_nonvis", persona_id="vp_nonvis"))

        assert not (tmp_path / "vp_nonvis" / "vision_prompt.md").exists()

    def test_commit_noclobber_existing_vision_prompt(
        self, tmp_path, monkeypatch: MonkeyPatch
    ) -> None:
        """预先存在的 vision_prompt.md 在 commit 后内容必须保持不变。"""
        from clipwright.config import settings

        monkeypatch.setattr(settings, "persona_dir", tmp_path)
        pdir = tmp_path / "vp_existing"
        _write_persona_dir(pdir, persona_id="vp_existing")
        original = "用户手写的既有视觉需求，绝不可覆盖"
        (pdir / "vision_prompt.md").write_text(original, encoding="utf-8")

        forge = ChatForge()
        session = self._make_session("vp_existing", [
            {"role": "user", "content": "我要全新自动生成的高饱和风格画面。"},
        ])
        forge._sessions["vp_existing"] = session

        import asyncio
        asyncio.run(forge.commit("vp_existing", persona_id="vp_existing"))

        content = (pdir / "vision_prompt.md").read_text(encoding="utf-8")
        assert content == original

    def test_build_vision_prompt_full_statement_no_truncation(self) -> None:
        """视觉相关用户消息应完整保留，不截断。"""
        tail = "结尾唯一标记 VISION_TAIL_88"
        content = "画面风格要求：" + "高对比、强纹理、霓虹光效，" * 60 + tail
        assert len(content) > 500

        prompt = ChatForge._build_vision_prompt_from_session([
            {"role": "user", "content": content},
        ])
        assert content in prompt
        assert tail in prompt
        assert len(prompt) > 500

    def test_build_vision_prompt_filters_non_visual(self) -> None:
        """无视觉关键词的消息不应进入视觉需求 Prompt。"""
        messages = [
            {"role": "user", "content": "我说话喜欢用短句，节奏快。"},
            {"role": "user", "content": "画面要冷色调，转场全部用硬切。"},
            {"role": "user", "content": "[系统] 用户上传了参考文档「稿子」（2000 字）。"},
        ]
        prompt = ChatForge._build_vision_prompt_from_session(messages)
        assert "画面要冷色调，转场全部用硬切。" in prompt
        assert "短句" not in prompt
        assert "[系统]" not in prompt
