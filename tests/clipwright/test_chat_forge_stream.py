"""ChatForge 流式回复（SSE）测试。

历史欠账：会话此前只能等模型产出完整 JSON 再渲染，长回复时界面长时间空白。
本轮加 `POST /api/persona/forge/chat/message/stream`（POST 的 SSE）+ 服务层
`message_stream()` 异步生成器。

覆盖：
- 服务层：离线路径（无 API key）不假装流式；有 key 时逐块 yield delta 并在末尾合并草稿；
  JSON 解析失败时把原文当回复（与同步路径一致）；上游中断时保留已产出的部分文本；
- 与同步路径的**状态一致性**：流式与同步产出的会话消息/草稿相同；
- 附件校验错误在流开始后以 `error` 事件给出（而不是 500）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, AsyncIterator

import pytest

from clipwright.services.chat_forge import (
    ChatForge,
    ChatForgeAttachmentError,
    _default_draft,
)


class _Chunk:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeLLM:
    """可编排的假 LLM：`generate` 返回整体响应，`stream_generate` 逐块产出。"""

    def __init__(self, chunks: list[str], *, fail_at: int | None = None) -> None:
        self._chunks = chunks
        self._fail_at = fail_at
        self.stream_calls: list[list[dict[str, str]]] = []

    async def stream_generate(self, messages, **kwargs) -> AsyncIterator[_Chunk]:  # noqa: ANN001
        self.stream_calls.append(messages)
        for index, chunk in enumerate(self._chunks):
            if self._fail_at is not None and index == self._fail_at:
                raise RuntimeError("upstream exploded")
            yield _Chunk(chunk)

    async def generate(self, messages=None, **kwargs):  # noqa: ANN001
        class _Resp:
            success = True
            status_code = 200
            content = "".join(self._chunks)  # type: ignore[arg-type]

        return _Resp()


@pytest.fixture()
def forge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ChatForge:
    monkeypatch.setattr(ChatForge, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(ChatForge, "_has_api_key", staticmethod(lambda: True))
    return ChatForge()


async def _collect(forge: ChatForge, **kwargs) -> list[dict[str, Any]]:
    return [event async for event in forge.message_stream(**kwargs)]


def _json_reply(**overrides: Any) -> str:
    payload = {
        "reply": "好的，我按这个方向调整。",
        "persona_draft": {
            "identity": {"tone": "sharp"},
            "language": {"academic_density": 0.3},
        },
        "missing_dimensions": ["rhythm"],
        "progress": {"identity": 0.8},
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


class TestStreamEvents:
    @pytest.mark.asyncio
    async def test_deltas_then_done(self, forge: ChatForge) -> None:
        chunked = [_json_reply()[:20], _json_reply()[20:]]
        forge._llm = _FakeLLM(chunked)

        sid = (await forge.start())["session_id"]
        events = await _collect(forge, session_id=sid, user_message="我要冷峻风格")

        deltas = [e for e in events if e["type"] == "delta"]
        done = [e for e in events if e["type"] == "done"]
        assert len(deltas) == 2
        assert "".join(e["text"] for e in deltas) == _json_reply()
        assert len(done) == 1
        assert done[0]["reply"] == "好的，我按这个方向调整。"
        assert done[0]["session_id"] == sid
        # done 里带完整状态（草稿/进度/待补维度）
        assert done[0]["persona_draft"]["identity"]["tone"] == "sharp"
        assert done[0]["progress"] == {"identity": 0.8}
        assert done[0]["missing_dimensions"] == ["rhythm"]

    @pytest.mark.asyncio
    async def test_draft_merged_and_persisted(self, forge: ChatForge) -> None:
        forge._llm = _FakeLLM([_json_reply()])
        sid = (await forge.start())["session_id"]
        await _collect(forge, session_id=sid, user_message="冷峻一点")

        session = forge._sessions[sid]
        assert session.persona_draft["identity"]["tone"] == "sharp"
        # assistant 消息存的是 reply（不是整段 JSON）
        assistant = [m for m in session.messages if m.get("role") == "assistant"]
        assert assistant and assistant[-1]["content"] == "好的，我按这个方向调整。"
        assert "persona_draft" not in assistant[-1]["content"]

        # 落盘可恢复
        restored = ChatForge()
        assert restored._sessions[sid].persona_draft["identity"]["tone"] == "sharp"

    @pytest.mark.asyncio
    async def test_stream_matches_sync_state(self, forge: ChatForge) -> None:
        """同样的模型输出，流式与同步路径必须得到同一个会话状态。"""
        reply = _json_reply()
        stream_sid = (await forge.start())["session_id"]
        forge._llm = _FakeLLM([reply])
        await _collect(forge, session_id=stream_sid, user_message="同一句话")

        sync_forge = ChatForge()
        sync_forge._llm = _FakeLLM([reply])
        sync_sid = (await sync_forge.start())["session_id"]
        await sync_forge.message(session_id=sync_sid, user_message="同一句话")

        stream_session = forge._sessions[stream_sid]
        sync_session = sync_forge._sessions[sync_sid]
        assert stream_session.persona_draft == sync_session.persona_draft
        assert [m["content"] for m in stream_session.messages] == [m["content"] for m in sync_session.messages]

    @pytest.mark.asyncio
    async def test_unparseable_reply_falls_back_to_raw_text(self, forge: ChatForge) -> None:
        forge._llm = _FakeLLM(["这不是 JSON，只是模型的一段话。"])
        sid = (await forge.start())["session_id"]
        events = await _collect(forge, session_id=sid, user_message="随便说点")

        done = [e for e in events if e["type"] == "done"][0]
        assert done["reply"] == "这不是 JSON，只是模型的一段话。"
        assert done.get("_parse_error")
        assistant = [m for m in forge._sessions[sid].messages if m.get("role") == "assistant"]
        assert assistant[-1]["content"] == "这不是 JSON，只是模型的一段话。"

    @pytest.mark.asyncio
    async def test_upstream_failure_keeps_partial(self, forge: ChatForge) -> None:
        forge._llm = _FakeLLM(["前半段", _json_reply()], fail_at=1)
        sid = (await forge.start())["session_id"]
        events = await _collect(forge, session_id=sid, user_message="流一半断了")

        assert [e["type"] for e in events] == ["delta", "error"]
        assert events[0]["text"] == "前半段"
        assert events[1]["partial"] == "前半段"
        # 已产出的部分文本要留住（用户不必重问）
        assistant = [m for m in forge._sessions[sid].messages if m.get("role") == "assistant"]
        assert assistant[-1]["content"] == "前半段"

    @pytest.mark.asyncio
    async def test_offline_path_is_single_delta_and_done(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ChatForge, "SESSIONS_DIR", tmp_path / "offline")
        monkeypatch.setattr(ChatForge, "_has_api_key", staticmethod(lambda: False))
        forge = ChatForge()
        forge._llm = _FakeLLM(["不该被调用"])
        sid = (await forge.start())["session_id"]
        events = await _collect(forge, session_id=sid, user_message="离线")

        assert [e["type"] for e in events] == ["delta", "done"]
        assert "API key" in events[0]["text"] or "帧艺" in events[0]["text"]
        assert events[1]["session_id"] == sid

    @pytest.mark.asyncio
    async def test_attachments_passed_into_context(self, forge: ChatForge) -> None:
        forge._llm = _FakeLLM([_json_reply()])
        sid = (await forge.start())["session_id"]
        await _collect(forge, session_id=sid, user_message="看图", attachments=[
            {"type": "file", "name": "a.txt", "data_url": "data:text/plain;base64,QQ=="},
        ])
        sent = forge._llm.stream_calls[0]  # type: ignore[attr-defined]
        assert any("a.txt" in m["content"] for m in sent)

    @pytest.mark.asyncio
    async def test_bad_attachments_raise_before_stream(self, forge: ChatForge) -> None:
        """附件非法要在开流前抛（API 层转成事件，服务层保持可测）。"""
        forge._llm = _FakeLLM([_json_reply()])
        sid = (await forge.start())["session_id"]
        with pytest.raises(ChatForgeAttachmentError):
            await _collect(forge, session_id=sid, user_message="x",
                           attachments=[{"type": "video", "data_url": "data:video/mp4;base64,AA"}])

    @pytest.mark.asyncio
    async def test_new_session_is_created_on_demand(self, forge: ChatForge) -> None:
        forge._llm = _FakeLLM([_json_reply()])
        events = await _collect(forge, session_id="fresh_sid", user_message="你好")
        assert events[-1]["type"] == "done"
        assert events[-1]["session_id"] == "fresh_sid"
        assert "fresh_sid" in forge._sessions


class TestStreamApi:
    def _client(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from fastapi.testclient import TestClient

        from clipwright.api import chat_forge as api
        from clipwright.main import app

        monkeypatch.setattr(ChatForge, "SESSIONS_DIR", tmp_path / "api")
        monkeypatch.setattr(ChatForge, "_has_api_key", staticmethod(lambda: False))
        monkeypatch.setattr(api, "_forge", ChatForge())
        return TestClient(app), api._forge

    def test_sse_event_stream_shape(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        client, forge = self._client(tmp_path, monkeypatch)
        sid = client.post("/api/persona/forge/chat/start", json={}).json()["session_id"]

        with client.stream("POST", "/api/persona/forge/chat/message/stream",
                           json={"session_id": sid, "message": "你好"}) as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            body = "".join(chunk for chunk in resp.iter_text())

        events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
        types = [e["type"] for e in events]
        assert types[-1] == "done"
        assert "delta" in types

    def test_bad_attachment_reported_as_error_event(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        client, _forge = self._client(tmp_path, monkeypatch)
        sid = client.post("/api/persona/forge/chat/start", json={}).json()["session_id"]
        with client.stream("POST", "/api/persona/forge/chat/message/stream",
                           json={"session_id": sid, "message": "x",
                                 "attachments": [{"type": "image"}]}) as resp:
            body = "".join(chunk for chunk in resp.iter_text())
        events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
        assert events and events[-1]["type"] == "error"
        assert "附件" in events[-1]["detail"]
