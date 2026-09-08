"""轮69：需求对话 SSE 真流式——delta 块必须在 chat 完成前到达客户端。

回归背景：轮68 的实现把 on_delta 收集进列表，等 chat() 返回后才一次性
yield——客户端实际仍是"最后一起出现"。本用例用 asyncio.Event 门闩证明
delta 在 chat 未完成时即已产出。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from clipwright.services.requirements_service import RequirementsService


def _make_service(status: str = "gathering") -> RequirementsService:
    svc = RequirementsService.__new__(RequirementsService)
    svc._llm = AsyncMock()
    svc._cleanup_started = True
    svc.get_session = lambda sid: {"status": status}  # type: ignore[method-assign]
    return svc


async def _drain(gen, timeout: float = 2.0) -> list[dict]:
    out: list[dict] = []
    while True:
        try:
            out.append(await asyncio.wait_for(gen.__anext__(), timeout=timeout))
        except StopAsyncIteration:
            return out


class TestTrueStreaming:
    async def test_delta_yields_before_chat_completes(self) -> None:
        """核心断言：chat 阻塞在门闩上时，delta 已经能读到。"""
        svc = _make_service("gathering")
        gate = asyncio.Event()

        async def fake_chat(session_id, user_message, on_delta=None):
            if on_delta:
                on_delta("你好")
            await asyncio.wait_for(gate.wait(), timeout=1.5)
            if on_delta:
                on_delta("世界")
            return {"reply": "你好世界", "is_ready": False}

        svc.chat = fake_chat  # type: ignore[method-assign]
        gen = svc.stream_chat("s1", "hi")

        assert await gen.__anext__() == {"type": "status", "data": "typing"}
        first = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
        assert first == {"type": "delta", "data": "你好"}

        # 此刻 chat 仍被门闩阻塞——证明不是"缓冲后一起发"
        gate.set()
        rest = await _drain(gen)
        types = [c["type"] for c in rest]
        assert types == ["delta", "result"]
        assert rest[0]["data"] == "世界"
        assert rest[1]["data"]["reply"] == "你好世界"

    async def test_chat_error_becomes_error_block(self) -> None:
        svc = _make_service("init")

        async def boom(session_id, user_message, on_delta=None):
            raise RuntimeError("llm down")

        svc.chat = boom  # type: ignore[method-assign]
        chunks = await _drain(svc.stream_chat("s1", "hi"))
        assert chunks[0] == {"type": "status", "data": "typing"}
        assert chunks[1]["type"] == "error"

    async def test_non_gathering_state_has_no_delta(self) -> None:
        svc = _make_service("brief_ready")

        async def fake_chat(session_id, user_message, on_delta=None):
            assert on_delta is None  # 非 gathering 态不传回调
            return {"reply": "请确认", "is_ready": True}

        svc.chat = fake_chat  # type: ignore[method-assign]
        chunks = await _drain(svc.stream_chat("s1", "确认"))
        assert [c["type"] for c in chunks] == ["status", "result"]

    async def test_consumer_disconnect_cancels_task(self) -> None:
        """客户端断开 → 消费任务被取消 → 内部 chat 任务同样被取消，不泄漏。"""
        svc = _make_service("gathering")
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def slow_chat(session_id, user_message, on_delta=None):
            started.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelled.set()
                raise
            return {"reply": "x"}

        svc.chat = slow_chat  # type: ignore[method-assign]

        async def consume() -> None:
            async for _ in svc.stream_chat("s1", "hi"):
                pass

        consumer = asyncio.create_task(consume())
        await asyncio.wait_for(started.wait(), timeout=1.0)
        consumer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await consumer
        await asyncio.wait_for(cancelled.wait(), timeout=1.0)
        assert cancelled.is_set()


def _service_with_chunks(chunks: list[str]) -> RequirementsService:
    svc = RequirementsService.__new__(RequirementsService)

    async def fake_stream(messages, system_prompt=""):
        for c in chunks:
            yield SimpleNamespace(content=c)

    svc._llm = SimpleNamespace(stream_generate=fake_stream)  # type: ignore[assignment]
    return svc


class TestIncrementalReplyExtraction:
    """轮69：旧实现 reply_buf 只赋值一次 → 仅首块内容被推送。"""

    async def test_multi_chunk_emits_all_text(self) -> None:
        payload = '{"reply": "你好\\n世界，这是流式输出。", "is_ready": false}'
        chunks = [payload[i:i + 3] for i in range(0, len(payload), 3)]
        svc = _service_with_chunks(chunks)
        got: list[str] = []
        result = await svc._stream_gathering_llm(
            {"system_prompt": "s", "user_prompt": "u"}, got.append
        )
        assert "".join(got) == "你好\n世界，这是流式输出。"
        assert len(got) > 1, "增量应为多块，实际被合并成单块"
        assert result["reply"] == "你好\n世界，这是流式输出。"

    async def test_escaped_quote_and_split_escape(self) -> None:
        payload = '{"reply": "他说\\"你好\\"就走了", "is_ready": false}'
        # 逐字符切分——强制在转义序列中间断开
        svc = _service_with_chunks(list(payload))
        got: list[str] = []
        result = await svc._stream_gathering_llm(
            {"system_prompt": "s", "user_prompt": "u"}, got.append
        )
        assert "".join(got) == '他说"你好"就走了'
        assert result["reply"] == '他说"你好"就走了'

    async def test_unicode_escape_split_across_chunks(self) -> None:
        payload = '{"reply": "\\u4f60\\u597d", "is_ready": false}'
        svc = _service_with_chunks([payload[:12], payload[12:16], payload[16:]])
        got: list[str] = []
        result = await svc._stream_gathering_llm(
            {"system_prompt": "s", "user_prompt": "u"}, got.append
        )
        assert "".join(got) == "你好"
        assert result["reply"] == "你好"

    async def test_missing_reply_key_falls_back(self) -> None:
        svc = _service_with_chunks(['{"is_ready": false}'])
        got: list[str] = []
        result = await svc._stream_gathering_llm(
            {"system_prompt": "s", "user_prompt": "u"}, got.append
        )
        assert got == []
        assert result["reply"]
