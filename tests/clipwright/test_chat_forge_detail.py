"""ChatForge 会话详情 + 同步回复落盘测试。"""
from __future__ import annotations

from pathlib import Path

import pytest

from clipwright.services.chat_forge import ChatForge
from clipwright.config import settings


@pytest.fixture()
def forge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ChatForge:
    monkeypatch.setattr(ChatForge, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(ChatForge, "ATTACHMENTS_DIR", tmp_path / "attachments")
    monkeypatch.setattr(settings, "persona_dir", tmp_path / "personas")
    monkeypatch.setattr(ChatForge, "_has_api_key", staticmethod(lambda: False))
    return ChatForge()


@pytest.mark.asyncio
async def test_detail_returns_messages_and_prompt_drafts(forge: ChatForge) -> None:
    sid = (await forge.start())["session_id"]
    await forge.message(sid, "我想做冷峻的科技评论")
    detail = forge.get_detail(sid)
    assert detail is not None
    assert len(detail["messages"]) == 2
    assert detail["messages"][0]["role"] == "user"
    assert detail["messages"][1]["role"] == "assistant"
    assert "冷峻的科技评论" in detail["prompt_draft"]
    assert detail["progress"].keys() == {
        "identity", "language", "rhythm", "visual", "audio", "constraints",
    }


@pytest.mark.asyncio
async def test_sync_reply_survives_restart(forge: ChatForge) -> None:
    sid = (await forge.start())["session_id"]
    await forge.message(sid, "冷峻、快节奏、数据说话")
    # 模拟重启：新实例从同一目录恢复
    again = ChatForge()
    detail = again.get_detail(sid)
    assert detail is not None
    assert len(detail["messages"]) == 2
    assert detail["messages"][1]["role"] == "assistant"


@pytest.mark.asyncio
async def test_patch_draft_accepts_falsy_and_revision(forge: ChatForge) -> None:
    sid = (await forge.start())["session_id"]
    before = forge.get_detail(sid)["revision"]
    detail = forge.patch_draft(sid, {
        "identity": {"tone": "sharp"},
        "language": {"academic_density": 0, "slang_ratio": False},
    }, expected_revision=before)
    assert detail is not None
    assert detail["persona_draft"]["identity"]["tone"] == "sharp"
    assert detail["persona_draft"]["language"]["academic_density"] == 0
    assert detail["persona_draft"]["language"]["slang_ratio"] is False
    assert detail["revision"] == before + 1
    with pytest.raises(ValueError):
        forge.patch_draft(sid, {"identity": {"tone": "x"}}, expected_revision=before)


@pytest.mark.asyncio
async def test_attachment_is_materialized_without_inline_data_url(forge: ChatForge) -> None:
    sid = (await forge.start())["session_id"]
    await forge.message(sid, "参考附件", attachments=[
        {"type": "file", "name": "note.txt", "mime": "text/plain", "data_url": "data:text/plain;base64,5L2g5aW9"},
    ])
    entry = forge.get_detail(sid)["messages"][0]
    att = entry["attachments"][0]
    assert "data_url" not in att
    assert att["storage_ref"].endswith(".txt")
    assert att["size"] > 0
    assert forge.read_attachment(sid, att["id"]) is not None


@pytest.mark.asyncio
async def test_commit_is_idempotent_and_does_not_overwrite(forge: ChatForge) -> None:
    sid = (await forge.start())["session_id"]
    forge.patch_draft(sid, {"identity": {"tone": "sharp"}})
    first = await forge.commit(sid, persona_id="persona_chat_demo", persona_name="Demo")
    again = await forge.commit(sid, persona_id="persona_chat_demo", persona_name="Demo")
    assert again.persona_id == first.persona_id
    assert again.parameter.identity.tone == "sharp"


@pytest.mark.asyncio
async def test_upload_quota_and_mime(forge: ChatForge) -> None:
    sid = (await forge.start())["session_id"]
    meta = forge.store_upload(sid, "a.txt", "text/plain", b"hello")
    assert meta["size"] == 5
    assert forge.read_attachment(sid, meta["id"]) is not None
    with pytest.raises(Exception):
        forge.store_upload(sid, "x.exe", "application/x-msdownload", b"x")
