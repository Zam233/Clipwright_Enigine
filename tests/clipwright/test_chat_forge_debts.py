"""ChatForge 历史欠账补齐测试：会话列表 / 删除、知识库 CRUD、图片附件。

对应审计里的欠账：
1. 只有 start + state（按 id 查），**没有会话列表**；
2. 知识库**只能追加**，不能列出/编辑/删除；
3. 消息只收纯文本，**没有图片/附件**。

这些测试全部走**离线路径**（无 API key → `_fallback_response`），因此不触网、结果确定。
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from clipwright.services.chat_forge import (
    ChatForge,
    ChatForgeAttachmentError,
    ChatForgeSession,
    _default_draft,
)


@pytest.fixture()
def forge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ChatForge:
    """把会话目录指到临时目录，避免污染仓库根目录的 chatforge_sessions/。

    同时**强制离线路径**（`_has_api_key` → False）：本机 .env 配了 key 时
    `_process` 会真的去打模型接口，测试既慢又不确定。
    """
    monkeypatch.setattr(ChatForge, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(ChatForge, "_has_api_key", staticmethod(lambda: False))
    return ChatForge()


def _png_data_url(payload: bytes = b"fake-png-bytes") -> str:
    return "data:image/png;base64," + base64.b64encode(payload).decode()


# ── 会话列表 / 删除 ──────────────────────────────────


class TestSessionList:
    @pytest.mark.asyncio
    async def test_list_returns_started_sessions(self, forge: ChatForge) -> None:
        first = await forge.start()
        second = await forge.start()
        sessions = {s["session_id"] for s in forge.list_sessions()}
        assert {first["session_id"], second["session_id"]} <= sessions

    @pytest.mark.asyncio
    async def test_list_has_title_counts_and_progress(self, forge: ChatForge) -> None:
        started = await forge.start()
        sid = started["session_id"]
        await forge.message(sid, "我想要冷峻的学术风格")
        await forge.message(sid, "节奏不要太快")
        await forge.add_knowledge(sid, "参考资料正文", source="ref.md")

        entry = next(s for s in forge.list_sessions() if s["session_id"] == sid)
        assert entry["title"] == "我想要冷峻的学术风格"
        assert entry["message_count"] == 2          # 知识库的系统消息不计入
        assert entry["knowledge_count"] == 1
        assert set(entry["progress"]) == set(_default_draft())
        # 时间字段可被前端直接解析排序
        assert entry["created_at"] and entry["updated_at"]

    @pytest.mark.asyncio
    async def test_list_sorted_by_updated_at_desc(self, forge: ChatForge) -> None:
        older = (await forge.start())["session_id"]
        newer = (await forge.start())["session_id"]
        await forge.message(older, "先说话")
        await forge.message(newer, "后说话")
        ids = [s["session_id"] for s in forge.list_sessions()]
        assert ids.index(newer) < ids.index(older)
        assert ids[0] == newer

    @pytest.mark.asyncio
    async def test_title_skips_system_messages(self, forge: ChatForge) -> None:
        sid = (await forge.start())["session_id"]
        await forge.add_knowledge(sid, "文档内容", source="doc.md")
        await forge.message(sid, "这是我真正的第一句话")
        entry = next(s for s in forge.list_sessions() if s["session_id"] == sid)
        assert entry["title"] == "这是我真正的第一句话"

    @pytest.mark.asyncio
    async def test_owner_filter(self, forge: ChatForge) -> None:
        sid = (await forge.start())["session_id"]
        forge.set_session_owner(sid, "user_a")
        mine = forge.list_sessions(owner_id="user_a", include_all=False)
        other = forge.list_sessions(owner_id="user_b", include_all=False)
        assert sid in {s["session_id"] for s in mine}
        assert sid not in {s["session_id"] for s in other}

    @pytest.mark.asyncio
    async def test_delete_removes_memory_and_file(self, forge: ChatForge) -> None:
        sid = (await forge.start())["session_id"]
        await forge.message(sid, "hi")
        path = forge._session_path(sid)
        assert path.exists()

        assert forge.delete_session(sid) is True
        assert forge.get_state(sid) is None
        assert not path.exists()
        # 幂等：再删一次返回 False（API 层转 404）
        assert forge.delete_session(sid) is False

    @pytest.mark.asyncio
    async def test_owner_persisted_and_restored(self, forge: ChatForge) -> None:
        sid = (await forge.start())["session_id"]
        forge.set_session_owner(sid, "user_z")
        assert json.loads(forge._session_path(sid).read_text(encoding="utf-8"))["owner_id"] == "user_z"

        restored = ChatForge()
        entry = next(s for s in restored.list_sessions() if s["session_id"] == sid)
        assert entry["owner_id"] == "user_z"


# ── 知识库 CRUD ──────────────────────────────────────


class TestKnowledgeCrud:
    @pytest.mark.asyncio
    async def test_add_assigns_stable_ids(self, forge: ChatForge) -> None:
        sid = (await forge.start())["session_id"]
        await forge.add_knowledge(sid, "正文内容", source="a.md")
        items = forge.list_knowledge(sid)
        assert items and items[0]["id"].startswith("kb_")
        assert items[0]["source"] == "a.md"
        assert items[0]["chars"] == len("正文内容")

    @pytest.mark.asyncio
    async def test_list_unknown_session_returns_none(self, forge: ChatForge) -> None:
        assert forge.list_knowledge("nope") is None

    @pytest.mark.asyncio
    async def test_get_full_content(self, forge: ChatForge) -> None:
        sid = (await forge.start())["session_id"]
        await forge.add_knowledge(sid, "完整正文" * 10, source="b.md")
        kb_id = forge.list_knowledge(sid)[0]["id"]
        doc = forge.get_knowledge(sid, kb_id)
        assert doc is not None
        assert doc["content"] == "完整正文" * 10
        assert forge.get_knowledge(sid, "kb_missing") is None

    @pytest.mark.asyncio
    async def test_update_content_and_source(self, forge: ChatForge) -> None:
        sid = (await forge.start())["session_id"]
        await forge.add_knowledge(sid, "旧内容", source="old.md")
        kb_id = forge.list_knowledge(sid)[0]["id"]

        updated = forge.update_knowledge(sid, kb_id, content="新内容", source="new.md")
        assert updated is not None
        assert updated["content"] == "新内容"
        assert updated["source"] == "new.md"
        # 只改一个字段时另一个保持不变
        again = forge.update_knowledge(sid, kb_id, content="又改了")
        assert again is not None
        assert again["source"] == "new.md"

    @pytest.mark.asyncio
    async def test_update_persists_to_disk(self, forge: ChatForge) -> None:
        sid = (await forge.start())["session_id"]
        await forge.add_knowledge(sid, "旧", source="o.md")
        kb_id = forge.list_knowledge(sid)[0]["id"]
        forge.update_knowledge(sid, kb_id, content="新")

        restored = ChatForge()
        items = restored.list_knowledge(sid)
        assert items and items[0]["id"] == kb_id
        assert restored.get_knowledge(sid, kb_id)["content"] == "新"

    @pytest.mark.asyncio
    async def test_update_unknown_returns_none(self, forge: ChatForge) -> None:
        sid = (await forge.start())["session_id"]
        assert forge.update_knowledge(sid, "kb_x", content="x") is None
        assert forge.update_knowledge("nope", "kb_x", content="x") is None

    @pytest.mark.asyncio
    async def test_delete_single_and_clear(self, forge: ChatForge) -> None:
        sid = (await forge.start())["session_id"]
        await forge.add_knowledge(sid, "一", source="1.md")
        await forge.add_knowledge(sid, "二", source="2.md")
        items = forge.list_knowledge(sid)
        assert len(items) == 2

        assert forge.delete_knowledge(sid, items[0]["id"]) is True
        assert forge.delete_knowledge(sid, items[0]["id"]) is False
        assert len(forge.list_knowledge(sid)) == 1

        assert forge.clear_knowledge(sid) == 1
        assert forge.list_knowledge(sid) == []

    @pytest.mark.asyncio
    async def test_legacy_session_without_ids_gets_them_on_restore(self, forge: ChatForge, tmp_path: Path) -> None:
        """老会话 JSON 里知识条目没有 id——恢复时必须补上，否则编辑/删除无从下手。"""
        path = forge._session_path("legacy1")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "session_id": "legacy1",
            "messages": [],
            "persona_draft": _default_draft(),
            "knowledge_base": [{"source": "old.md", "content": "老数据"}],
            "created_at": ChatForgeSession(session_id="legacy1").created_at.isoformat(),
            "updated_at": ChatForgeSession(session_id="legacy1").updated_at.isoformat(),
        }, ensure_ascii=False), encoding="utf-8")

        restored = ChatForge()
        items = restored.list_knowledge("legacy1")
        assert items and items[0]["id"].startswith("kb_")
        assert restored.get_knowledge("legacy1", items[0]["id"])["content"] == "老数据"


# ── 图片 / 文件附件 ──────────────────────────────────


class TestAttachments:
    @pytest.mark.asyncio
    async def test_image_attachment_stored_with_placeholder_context(self, forge: ChatForge) -> None:
        sid = (await forge.start())["session_id"]
        result = await forge.message(sid, "参考这张图的风格", attachments=[
            {"type": "image", "name": "ref.png", "mime": "image/png", "data_url": _png_data_url()},
        ])
        assert result["session_id"] == sid

        session = forge._sessions[sid]
        user_msg = [m for m in session.messages if m.get("role") == "user"][-1]
        assert user_msg["content"] == "参考这张图的风格"
        assert len(user_msg["attachments"]) == 1
        assert user_msg["attachments"][0]["name"] == "ref.png"
        # 未启用视觉能力时不编造内容，只留占位说明
        assert "图片" in user_msg["attachment_context"]

    @pytest.mark.asyncio
    async def test_attachments_survive_restart(self, forge: ChatForge) -> None:
        sid = (await forge.start())["session_id"]
        await forge.message(sid, "看图", attachments=[
            {"type": "image", "name": "a.png", "data_url": _png_data_url()},
        ])
        restored = ChatForge()
        session = restored._sessions[sid]
        user_msg = [m for m in session.messages if m.get("role") == "user"][-1]
        assert user_msg["attachments"][0]["name"] == "a.png"
        assert user_msg["attachment_context"]

    @pytest.mark.asyncio
    async def test_file_attachment(self, forge: ChatForge) -> None:
        sid = (await forge.start())["session_id"]
        await forge.message(sid, "附一份文案", attachments=[
            {"type": "file", "name": "script.txt", "data_url": "data:text/plain;base64,QUJD"},
        ])
        user_msg = [m for m in forge._sessions[sid].messages if m.get("role") == "user"][-1]
        assert "文件" in user_msg["attachment_context"]

    @pytest.mark.asyncio
    async def test_too_many_attachments_rejected(self, forge: ChatForge) -> None:
        sid = (await forge.start())["session_id"]
        with pytest.raises(ChatForgeAttachmentError):
            await forge.message(sid, "x", attachments=[
                {"type": "image", "data_url": _png_data_url()} for _ in range(forge.MAX_ATTACHMENTS + 1)
            ])
        # 校验失败不能把消息写进会话
        assert all(m.get("role") != "user" for m in forge._sessions[sid].messages)

    @pytest.mark.asyncio
    async def test_bad_type_and_missing_payload_rejected(self, forge: ChatForge) -> None:
        sid = (await forge.start())["session_id"]
        with pytest.raises(ChatForgeAttachmentError):
            await forge.message(sid, "x", attachments=[{"type": "video", "data_url": "data:video/mp4;base64,AA"}])
        with pytest.raises(ChatForgeAttachmentError):
            await forge.message(sid, "x", attachments=[{"type": "image"}])
        with pytest.raises(ChatForgeAttachmentError):
            await forge.message(sid, "x", attachments=["not-a-dict"])

    @pytest.mark.asyncio
    async def test_oversized_attachment_rejected(self, forge: ChatForge, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ChatForge, "MAX_ATTACHMENT_CHARS", 10)
        sid = (await forge.start())["session_id"]
        with pytest.raises(ChatForgeAttachmentError):
            await forge.message(sid, "x", attachments=[{"type": "image", "data_url": _png_data_url(b"0123456789")}])

    @pytest.mark.asyncio
    async def test_no_attachments_behaves_as_before(self, forge: ChatForge) -> None:
        sid = (await forge.start())["session_id"]
        await forge.message(sid, "纯文本消息")
        user_msg = [m for m in forge._sessions[sid].messages if m.get("role") == "user"][-1]
        assert "attachments" not in user_msg
        assert "attachment_context" not in user_msg

    def test_vision_summary_handles_unknown_shapes(self) -> None:
        assert ChatForge._summarize_vision(None) == ""
        assert ChatForge._summarize_vision({"tags": ["夜晚", "霓虹"]}) == "标签: 夜晚, 霓虹"
        assert "描述: 冷色调" in ChatForge._summarize_vision({"description": "冷色调"})
        assert "主色: #000000" in ChatForge._summarize_vision({"dominant_colors": ["#000000"]})


# ── API 端点 ──────────────────────────────────────────


class TestChatForgeApi:
    def _client(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from fastapi.testclient import TestClient

        from clipwright.api import chat_forge as api
        from clipwright.main import app

        monkeypatch.setattr(ChatForge, "SESSIONS_DIR", tmp_path / "api_sessions")
        monkeypatch.setattr(ChatForge, "_has_api_key", staticmethod(lambda: False))
        monkeypatch.setattr(api, "_forge", ChatForge())
        return TestClient(app)

    def test_sessions_list_and_delete(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        client = self._client(tmp_path, monkeypatch)
        sid = client.post("/api/persona/forge/chat/start", json={}).json()["session_id"]
        client.post("/api/persona/forge/chat/message", json={"session_id": sid, "message": "第一句"})

        listing = client.get("/api/persona/forge/chat/sessions")
        assert listing.status_code == 200
        body = listing.json()
        assert body["total"] >= 1
        assert any(s["session_id"] == sid and s["title"] == "第一句" for s in body["sessions"])

        assert client.delete(f"/api/persona/forge/chat/sessions/{sid}").status_code == 200
        assert client.delete(f"/api/persona/forge/chat/sessions/{sid}").status_code == 404

    def test_knowledge_crud_endpoints(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        client = self._client(tmp_path, monkeypatch)
        sid = client.post("/api/persona/forge/chat/start", json={}).json()["session_id"]
        client.post("/api/persona/forge/chat/knowledge", json={"session_id": sid, "content": "正文", "source": "r.md"})

        items = client.get(f"/api/persona/forge/chat/knowledge/{sid}").json()["items"]
        kb_id = items[0]["id"]
        assert client.get(f"/api/persona/forge/chat/knowledge/{sid}/{kb_id}").json()["content"] == "正文"

        put = client.put(f"/api/persona/forge/chat/knowledge/{sid}/{kb_id}", json={"content": "改后"})
        assert put.status_code == 200 and put.json()["content"] == "改后"
        assert client.put(f"/api/persona/forge/chat/knowledge/{sid}/{kb_id}", json={}).status_code == 400
        assert client.delete(f"/api/persona/forge/chat/knowledge/{sid}/{kb_id}").status_code == 200
        assert client.get(f"/api/persona/forge/chat/knowledge/{sid}/{kb_id}").status_code == 404

    def test_knowledge_unknown_session_404(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        client = self._client(tmp_path, monkeypatch)
        assert client.get("/api/persona/forge/chat/knowledge/nope").status_code == 404

    def test_message_with_attachment_and_bad_attachment(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        client = self._client(tmp_path, monkeypatch)
        sid = client.post("/api/persona/forge/chat/start", json={}).json()["session_id"]

        ok = client.post("/api/persona/forge/chat/message", json={
            "session_id": sid, "message": "看这张图",
            "attachments": [{"type": "image", "name": "r.png", "data_url": _png_data_url()}],
        })
        assert ok.status_code == 200, ok.text

        bad = client.post("/api/persona/forge/chat/message", json={
            "session_id": sid, "message": "x",
            "attachments": [{"type": "image"}],
        })
        assert bad.status_code == 400
        assert "附件" in bad.json()["detail"]
