"""TypeForge 会话/LLM 草稿测试。"""
from __future__ import annotations

from pathlib import Path

import pytest

from clipwright.services.type_forge import TypeForge


class _LLM:
    async def generate(self, messages=None, **kwargs):
        class R:
            success = True
            content = '{"reply":"已记录","type_draft":{"id":"demo_type","name":"示例类型","prompts":{"genre":{"text":"快速知识解说"}}},"missing_fields":["shot_params"],"progress":{"genre":1}}'
        return R()


@pytest.fixture()
def forge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TypeForge:
    monkeypatch.setattr(TypeForge, "SESSIONS_DIR", tmp_path / "typeforge")
    f = TypeForge()
    f._llm = _LLM()
    return f


@pytest.mark.asyncio
async def test_message_and_detail(forge: TypeForge) -> None:
    sid = (await forge.start())["session_id"]
    out = await forge.message(sid, "做知识区快节奏类型")
    assert out["draft"]["id"] == "demo_type"
    detail = forge.detail(sid)
    assert detail is not None and detail["messages"][0]["role"] == "user"
    assert detail["draft"]["prompts"]["genre"]["text"] == "快速知识解说"


@pytest.mark.asyncio
async def test_restart_restores_session(forge: TypeForge) -> None:
    sid = (await forge.start())["session_id"]
    await forge.message(sid, "做知识区快节奏类型")
    again = TypeForge()
    again._llm = forge._llm
    assert again.detail(sid) is not None


@pytest.mark.asyncio
async def test_commit_creates_custom_type(forge: TypeForge, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import clipwright.api.type_maker as tm

    monkeypatch.setattr(tm, "_USER_TYPES_DIR", tmp_path / "user_types")
    sid = (await forge.start())["session_id"]
    definition = {
        "id": "typeforge_test_type", "name": "TypeForge 测试",
        "description": "测试类型",
        "shot_params": {"min_shot_sec": 1, "max_shot_sec": 3, "transition_type": "cut"},
        "prompts": {"genre": {"text": "快节奏知识解说"}},
        "param_rules": [], "signature_traits": [], "declares": {}, "waivers": [],
    }
    result = await forge.commit(sid, "typeforge_test_type", definition)
    assert result["status"] == "created"
    assert (tmp_path / "user_types" / "typeforge_test_type.yaml").exists()
    from clipwright.category.registry import CategoryRegistry
    CategoryRegistry.unregister("typeforge_test_type")


@pytest.mark.asyncio
async def test_builtin_type_detail_includes_prompts() -> None:
    from clipwright.api.type_maker import get_type
    from clipwright.category import CategoryRegistry, KnowledgeLongformPlugin
    CategoryRegistry.register(KnowledgeLongformPlugin())
    try:
        data = await get_type("knowledge_longform")
        assert data["builtin"] is True
        assert "structure" in data["prompts"]
        assert "genre" in data["prompts"]
        assert data["shot_params"]["max_shot_sec"] > 0
    finally:
        CategoryRegistry.unregister("knowledge_longform")


def test_type_forge_http_404_400_409_and_create() -> None:
    from fastapi.testclient import TestClient
    from clipwright.main import app
    client = TestClient(app)
    assert client.get("/api/type-forge/chat/sessions/missing").status_code == 404
    stream = client.post("/api/type-forge/chat/message/stream", json={
        "session_id": "missing", "message": "x", "attachments": [],
    })
    assert stream.status_code == 200
    assert "session not found" in stream.text
    assert client.post("/api/type-forge/chat/commit", json={
        "session_id": "missing", "type_id": "x", "definition": {},
    }).status_code == 404

    sid = client.post("/api/type-forge/chat/start", json={}).json()["session_id"]
    bad = {
        "id": "typeforge_http_bad", "name": "坏类型",
        "shot_params": {"min_shot_sec": 1, "max_shot_sec": 5,
                        "transition_type": "rm -rf", "transition_duration_sec": 0.5},
        "prompts": {}, "param_rules": [], "signature_traits": [], "declares": {}, "waivers": [],
    }
    assert client.post("/api/type-forge/chat/commit", json={
        "session_id": sid, "type_id": "typeforge_http_bad", "definition": bad,
    }).status_code == 400

    good = {**bad, "id": "typeforge_http_ok", "name": "HTTP 测试",
            "shot_params": {"min_shot_sec": 1, "max_shot_sec": 3,
                            "transition_type": "cut", "transition_duration_sec": 0.2},
            "prompts": {"genre": {"text": "快节奏知识解说"}}}
    created = client.post("/api/type-forge/chat/commit", json={
        "session_id": sid, "type_id": "typeforge_http_ok", "definition": good,
    })
    assert created.status_code == 200
    assert client.post("/api/type-forge/chat/commit", json={
        "session_id": sid, "type_id": "typeforge_http_ok", "definition": good,
    }).status_code == 409
    client.delete("/api/type-maker/typeforge_http_ok")


@pytest.mark.asyncio
async def test_message_accepts_text_attachment(forge: TypeForge) -> None:
    import base64
    sid = (await forge.start())["session_id"]
    encoded = base64.b64encode("品牌关键词：极简、冷色".encode()).decode()
    await forge.message(sid, "按附件做类型", [{
        "type": "file", "name": "brief.txt", "mime": "text/plain",
        "data_url": f"data:text/plain;base64,{encoded}",
    }])
    detail = forge.detail(sid)
    assert detail is not None
    assert "品牌关键词" in detail["messages"][0]["content"]
