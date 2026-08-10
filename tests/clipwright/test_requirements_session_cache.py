"""E5: 会话级缓存 — RAG 检索 + Persona 上下文按 session/persona 缓存（TTL 10min）。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from clipwright.services.requirements_service import (
    RequirementsService,
    clear_session_caches,
)


@pytest.fixture(autouse=True)
def _clear_caches():
    """每个用例前清空模块级缓存，避免用例间串扰。"""
    clear_session_caches()
    yield
    clear_session_caches()


def _make_service() -> RequirementsService:
    svc = RequirementsService.__new__(RequirementsService)
    svc._llm = AsyncMock()
    svc._cleanup_started = True
    return svc


def _retriever_mock() -> AsyncMock:
    ret = AsyncMock()
    result = SimpleNamespace(context="RAG1")
    ret.retrieve.return_value = result
    return ret


class TestRagCache:
    async def test_same_session_same_query_retriever_called_once(self) -> None:
        svc = _make_service()
        ret = _retriever_mock()
        with patch("clipwright.rag.retriever.Retriever", return_value=ret):
            r1 = await svc._retrieve_knowledge("p1", "q", session_id="s1")
            r2 = await svc._retrieve_knowledge("p1", "q", session_id="s1")

        assert r1 == "RAG1" and r2 == "RAG1"
        assert ret.retrieve.call_count == 1

    async def test_different_query_refreshes(self) -> None:
        svc = _make_service()
        ret = _retriever_mock()
        with patch("clipwright.rag.retriever.Retriever", return_value=ret):
            await svc._retrieve_knowledge("p1", "q1", session_id="s1")
            await svc._retrieve_knowledge("p1", "q2", session_id="s1")

        assert ret.retrieve.call_count == 2

    async def test_different_session_same_query_refreshes(self) -> None:
        svc = _make_service()
        ret = _retriever_mock()
        with patch("clipwright.rag.retriever.Retriever", return_value=ret):
            await svc._retrieve_knowledge("p1", "q", session_id="s1")
            await svc._retrieve_knowledge("p1", "q", session_id="s2")

        assert ret.retrieve.call_count == 2

    async def test_cache_cleared_by_helper(self) -> None:
        svc = _make_service()
        ret = _retriever_mock()
        with patch("clipwright.rag.retriever.Retriever", return_value=ret):
            await svc._retrieve_knowledge("p1", "q", session_id="s1")
            clear_session_caches()
            await svc._retrieve_knowledge("p1", "q", session_id="s1")

        assert ret.retrieve.call_count == 2


class TestPersonaCache:
    def test_persona_context_cached(self) -> None:
        svc = _make_service()
        manifest = SimpleNamespace(
            parameter=SimpleNamespace(model_dump=lambda mode="json": {"audio": {}}),
            prompt="P",
        )
        loader = unittest.mock.Mock(return_value=manifest)
        with patch("clipwright.persona.loader.load_persona_by_id", loader):
            c1 = svc._build_full_persona_context({"persona_id": "p1"})
            c2 = svc._build_full_persona_context({"persona_id": "p1"})

        assert c1 == c2
        assert loader.call_count == 1

    def test_persona_cache_cleared_by_helper(self) -> None:
        svc = _make_service()
        manifest = SimpleNamespace(
            parameter=SimpleNamespace(model_dump=lambda mode="json": {"audio": {}}),
            prompt="P",
        )
        loader = unittest.mock.Mock(return_value=manifest)
        with patch("clipwright.persona.loader.load_persona_by_id", loader):
            svc._build_full_persona_context({"persona_id": "p1"})
            clear_session_caches()
            svc._build_full_persona_context({"persona_id": "p1"})

        assert loader.call_count == 2
