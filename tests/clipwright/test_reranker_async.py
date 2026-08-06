"""Reranker 异步化测试（U9）— API 模式必须通过 httpx.AsyncClient 异步请求，不阻塞事件循环。"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clipwright.rag.reranker import Reranker
from clipwright.rag.vector_store import ScoredChunk


def _chunk(i: int, score: float = 0.5) -> ScoredChunk:
    return ScoredChunk(id=f"c{i}", text=f"文本{i}", score=score, metadata={})


def _mock_async_client(
    response: MagicMock | None = None,
    side_effect: Exception | None = None,
) -> MagicMock:
    """构造一个支持 async with 的 httpx.AsyncClient mock。"""
    client = MagicMock()
    if side_effect is not None:
        client.post = AsyncMock(side_effect=side_effect)
    else:
        client.post = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def test_rerank_is_coroutine_function() -> None:
    """公开接口 rerank 必须是协程函数（不存在同步调用入口）。"""
    assert inspect.iscoroutinefunction(Reranker.rerank)
    assert inspect.iscoroutinefunction(Reranker._rerank_api)


async def test_rerank_api_awaits_async_client() -> None:
    """API 模式必须通过 httpx.AsyncClient 异步发起 POST 并解析结果。"""
    reranker = Reranker(
        model_name="test-model",
        base_url="https://api.example.com/rerank",
        api_key="k",
    )
    candidates = [_chunk(0), _chunk(1), _chunk(2)]

    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value={
        "results": [
            {"index": 2, "relevance_score": 0.99},
            {"index": 0, "relevance_score": 0.10},
        ]
    })
    client = _mock_async_client(response=response)

    with patch("clipwright.rag.reranker.httpx.AsyncClient", return_value=client) as cls:
        results = await reranker.rerank("query", candidates, top_k=2)

    cls.assert_called_once()
    client.post.assert_awaited_once()
    # 按 relevance_score 排序，top_k=2
    assert [c.id for c in results] == ["c2", "c0"]
    assert results[0].score == pytest.approx(0.99)


async def test_rerank_api_failure_falls_back_to_original_order() -> None:
    """API 调用失败时回退到原始顺序，不抛异常。"""
    reranker = Reranker(
        model_name="test-model",
        base_url="https://api.example.com/rerank",
    )
    candidates = [_chunk(0, 0.9), _chunk(1, 0.8)]
    client = _mock_async_client(side_effect=RuntimeError("boom"))

    with patch("clipwright.rag.reranker.httpx.AsyncClient", return_value=client):
        results = await reranker.rerank("query", candidates, top_k=2)

    assert [c.id for c in results] == ["c0", "c1"]


async def test_rerank_empty_candidates_short_circuits() -> None:
    """空候选列表直接返回，不触发任何网络调用。"""
    reranker = Reranker(model_name="m", base_url="https://api.example.com/rerank")
    with patch("clipwright.rag.reranker.httpx.AsyncClient") as cls:
        results = await reranker.rerank("query", [])
    assert results == []
    cls.assert_not_called()
