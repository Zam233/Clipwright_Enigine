"""E3: LLM generate 对 transient 错误指数退避重试。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from clipwright.services.llm import LLMService


def _fake_resp(success=True, status_code=200, content="ok"):
    return SimpleNamespace(
        success=success,
        status_code=status_code,
        content=content,
        usage=None,
        tool_calls=[],
        reasoning_content="",
    )


def _service(client_generate) -> LLMService:
    svc = LLMService.__new__(LLMService)
    client = SimpleNamespace(generate=client_generate)
    svc._client = client
    svc._flash_client = client
    svc.provider = "openai"
    return svc


class TestLlmRetry:
    @pytest.mark.asyncio
    async def test_retry_after_connection_error(self) -> None:
        """首次抛连接错误、二次成功 → generate 调用 2 次并返回成功。"""
        calls = []

        def _gen(messages, model=None, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise ConnectionError("reset")
            return _fake_resp(success=True, content="ok")

        svc = _service(_gen)
        with patch("clipwright.services.llm.asyncio.sleep", new=AsyncMock()):
            resp = await svc.generate([{"role": "user", "content": "hi"}])

        assert resp.success is True
        assert resp.content == "ok"
        assert len(calls) == 2

    @pytest.mark.asyncio
    async def test_retry_on_transient_429(self) -> None:
        """前两次 429、第三次成功 → 调用 3 次（默认 2 次重试）。"""
        calls = []

        def _gen(messages, model=None, **kwargs):
            calls.append(1)
            if len(calls) < 3:
                return _fake_resp(success=False, status_code=429, content="rate limited")
            return _fake_resp(success=True, content="ok")

        svc = _service(_gen)
        with patch("clipwright.services.llm.asyncio.sleep", new=AsyncMock()):
            resp = await svc.generate([{"role": "user", "content": "hi"}])

        assert resp.success is True
        assert len(calls) == 3

    @pytest.mark.asyncio
    async def test_no_retry_on_non_transient_400(self) -> None:
        """非 transient 400 → 立即返回不重试。"""
        calls = []

        def _gen(messages, model=None, **kwargs):
            calls.append(1)
            return _fake_resp(success=False, status_code=400, content="bad request")

        svc = _service(_gen)
        resp = await svc.generate([{"role": "user", "content": "hi"}])

        assert resp.success is False
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_transient_500_exhausts_retries(self) -> None:
        """连续 500 → 重试耗尽后返回最后一次响应（不抛异常）。"""
        calls = []

        def _gen(messages, model=None, **kwargs):
            calls.append(1)
            return _fake_resp(success=False, status_code=500, content="server error")

        svc = _service(_gen)
        with patch("clipwright.services.llm.asyncio.sleep", new=AsyncMock()):
            resp = await svc.generate([{"role": "user", "content": "hi"}])

        assert resp.success is False
        assert len(calls) == 3  # 1 次 + 2 次重试

    @pytest.mark.asyncio
    async def test_success_first_attempt_no_retry(self) -> None:
        calls = []

        def _gen(messages, model=None, **kwargs):
            calls.append(1)
            return _fake_resp(success=True, content="ok")

        svc = _service(_gen)
        resp = await svc.generate([{"role": "user", "content": "hi"}])

        assert resp.success is True
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_exhausted_retries_return_last_response(self) -> None:
        """max_retries=0 → 无重试，单次 429 即返回失败响应。"""
        calls = []

        def _gen(messages, model=None, **kwargs):
            calls.append(1)
            return _fake_resp(success=False, status_code=429, content="rate limited")

        svc = _service(_gen)
        resp = await svc.generate([{"role": "user", "content": "hi"}], max_retries=0)

        assert resp.success is False
        assert len(calls) == 1
