"""RAG 模块测试 — Chunker / Embedder / VectorStore / Reranker / Retriever。"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

import pytest

from clipwright.rag.chunker import Chunk, chunk_document
from clipwright.rag.embedder import get_embedder, reset_embedder
from clipwright.rag.reranker import Reranker
from clipwright.rag.retriever import Retriever
from clipwright.rag.vector_store import ScoredChunk, VectorStore
from clipwright.schema.persona import KnowledgeDoc


def _make_store() -> tuple[VectorStore, str]:
    """创建使用临时目录的 VectorStore。"""
    td = tempfile.mkdtemp()
    store = VectorStore(persist_dir=Path(td) / ".chroma")
    return store, td


# ── Chunker ──

class TestChunker:
    def test_empty(self) -> None:
        assert chunk_document("") == []

    def test_short_text(self) -> None:
        chunks = chunk_document("hello world", doc_id="test")
        assert len(chunks) == 1
        assert "hello" in chunks[0].text

    def test_markdown_headings(self) -> None:
        doc = "# 第一章\n内容一\n\n# 第二章\n内容二"
        chunks = chunk_document(doc, doc_id="md")
        assert len(chunks) == 2
        assert "第一章" in chunks[0].text
        assert "第二章" in chunks[1].text

    def test_long_text_split(self) -> None:
        long = "测试句子。" * 200
        chunks = chunk_document(long, chunk_size=200, chunk_overlap=20, doc_id="long")
        assert len(chunks) > 1


# ── Embedder ──

class TestEmbedder:
    def test_sentence_transformer(self) -> None:
        reset_embedder()
        embedder = get_embedder()
        vec = embedder.embed(["测试文本"])
        assert len(vec) == 1
        assert len(vec[0]) > 0
        assert embedder.dim > 0

    def test_query_embedding(self) -> None:
        reset_embedder()
        embedder = get_embedder()
        qv = embedder.embed_query("冷峻风格")
        # 验证返回了合理的向量（维度 > 0 且包含数值）
        assert len(qv) > 0
        assert any(abs(v) > 1e-6 for v in qv)  # 不全为零


# ── VectorStore ──

class TestVectorStore:
    def test_index_and_search(self) -> None:
        store, td = _make_store()
        try:
            chunks = [
                Chunk(id="c1", text="冷峻科技评论风格", source="test", persona_id="p"),
                Chunk(id="c2", text="温暖Vlog日常分享", source="test", persona_id="p"),
            ]
            n = store.index_chunks("test_p", chunks)
            assert n == 2
            results = store.search("test_p", "冷峻", top_k=5)
            assert len(results) > 0
        finally:
            store.clear()

    def test_no_index(self) -> None:
        store, td = _make_store()
        try:
            results = store.search("nonexistent", "test", top_k=5)
            assert results == []
        finally:
            store.clear()

    def test_delete_collection(self) -> None:
        store, td = _make_store()
        try:
            store.index_chunks("del_test", [Chunk(id="x", text="test", source="s")])
            store.delete_collection("del_test")
            results = store.search("del_test", "test", top_k=5)
            assert results == []
        finally:
            store.clear()

    def test_double_index_replaces(self) -> None:
        store, td = _make_store()
        try:
            store.index_chunks("rep_test", [Chunk(id="a", text="冷峻风格", source="s")])
            store.index_chunks("rep_test", [Chunk(id="b", text="温暖风格", source="s")])
            results = store.search("rep_test", "温暖", top_k=5)
            assert len(results) > 0
            assert "温暖" in results[0].text
        finally:
            store.clear()


# ── Reranker ──

class TestReranker:
    def test_rerank_empty(self) -> None:
        r = Reranker()
        assert r.rerank("query", []) == []


# ── Retriever ──

class TestRetriever:
    def test_index_and_retrieve(self) -> None:
        store, td = _make_store()
        try:
            retriever = Retriever(vector_store=store)
            docs = [
                KnowledgeDoc(id="d1", title="风格指南", content="冷峻科技评论，黑白画面，快速剪辑"),
                KnowledgeDoc(id="d2", title="脚本", content="温暖Vlog，明亮色调，舒缓节奏"),
            ]
            result = retriever.index_persona_knowledge("test_ret", docs)
            assert result.total_docs == 2
            assert result.total_chunks > 0

            async def _search():
                return await retriever.retrieve("test_ret", "冷峻科技", top_k=5, rerank=False)
            search_result = asyncio.run(_search())
            assert search_result.total_chunks > 0
            assert len(search_result.context) > 0
        finally:
            store.clear()

    def test_retrieve_no_index(self) -> None:
        store, td = _make_store()
        try:
            retriever = Retriever(vector_store=store)
            result = asyncio.run(retriever.retrieve("no_index", "test"))
            assert result.total_chunks == 0
            assert result.context == ""
        finally:
            store.clear()

    def test_index_from_directory(self) -> None:
        from clipwright.persona.repository import PersonaRepository
        from clipwright.schema.persona import PersonaManifest, ParameterLayer

        td = tempfile.mkdtemp()
        try:
            repo = PersonaRepository(Path(td))
            manifest = PersonaManifest(
                persona_id="rag_test",
                parameter=ParameterLayer(persona_id="rag_test"),
                knowledge=[
                    KnowledgeDoc(id="k1", title="指南", content="冷峻科技评论风格", source="manual"),
                ],
            )
            repo.save_manifest(manifest)

            store = VectorStore(persist_dir=Path(td) / "chroma")
            retriever = Retriever(vector_store=store)
            result = retriever.index_from_directory("rag_test", Path(td) / "rag_test")
            assert result.total_docs == 1
            assert result.total_chunks > 0
        finally:
            store.clear()
