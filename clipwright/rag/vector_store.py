"""向量数据库 — 基于 ChromaDB 的持久化存储。

按 persona_id 分 collection，存储路径在 knowledge/.chroma/ 下。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Optional

import chromadb
from chromadb.config import Settings as ChromaSettings

from clipwright.config import settings
from clipwright.rag.chunker import Chunk
from clipwright.rag.embedder import get_embedder


class ScoredChunk:
    """带检索得分的文档块。"""
    id: str
    text: str
    metadata: dict[str, Any]
    score: float

    def __init__(self, id: str, text: str, score: float, metadata: dict[str, Any] | None = None) -> None:
        self.id = id
        self.text = text
        self.score = score
        self.metadata = metadata or {}


def _chroma_path(persona_knowledge_dir: Path) -> Path:
    """ChromaDB 持久化路径（隐藏目录）。"""
    return persona_knowledge_dir / ".chroma"


class VectorStore:
    """向量存储，封装 ChromaDB 操作。"""

    def __init__(self, persist_dir: Optional[Path] = None) -> None:
        self._persist_dir = persist_dir or Path(".chroma_db")
        self._client = chromadb.PersistentClient(
            path=str(self._persist_dir),
            settings=ChromaSettings(anonymized_telemetry=False),
        )

    # ── Collection 管理 ──

    def _collection_name(self, persona_id: str) -> str:
        return f"persona_{persona_id}"

    def _get_collection(self, persona_id: str, create: bool = True):
        name = self._collection_name(persona_id)
        try:
            return self._client.get_collection(name)
        except (ValueError, chromadb.errors.NotFoundError):
            if create:
                return self._client.create_collection(
                    name=name,
                    metadata={"hnsw:space": "cosine"},
                )
            return None

    def delete_collection(self, persona_id: str) -> None:
        name = self._collection_name(persona_id)
        try:
            self._client.delete_collection(name)
        except (ValueError, chromadb.errors.NotFoundError):
            pass

    # ── 索引 ──

    def index_chunks(self, persona_id: str, chunks: list[Chunk]) -> int:
        """为 Persona 的全部知识库块建立向量索引。"""
        if not chunks:
            return 0

        embedder = get_embedder()
        texts = [c.text for c in chunks]
        ids = [c.id for c in chunks]
        metadatas = [c.metadata for c in chunks]

        # 批量生成嵌入
        embeddings = embedder.embed(texts)

        # 写入 ChromaDB（先删除旧的再写入）
        self.delete_collection(persona_id)
        collection = self._get_collection(persona_id, create=True)

        # 分批写入（ChromaDB 单批限制约 41666 个）
        batch_size = 100
        for i in range(0, len(ids), batch_size):
            end = i + batch_size
            collection.add(
                ids=ids[i:end],
                embeddings=embeddings[i:end],
                documents=texts[i:end],
                metadatas=metadatas[i:end],
            )

        return len(chunks)

    # ── 检索 ──

    def search(
        self,
        persona_id: str,
        query: str,
        top_k: int | None = None,
    ) -> list[ScoredChunk]:
        """语义检索，返回 Top-K 带分数结果。"""
        top_k = top_k or settings.rag_rerank_top_k

        collection = self._get_collection(persona_id, create=False)
        if collection is None:
            return []

        embedder = get_embedder()
        query_embedding = embedder.embed_query(query)

        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=min(top_k, 100),
        )

        chunks: list[ScoredChunk] = []
        if not results["ids"] or not results["ids"][0]:
            return chunks

        for i in range(len(results["ids"][0])):
            chunks.append(ScoredChunk(
                id=results["ids"][0][i],
                text=results["documents"][0][i] if results["documents"] else "",
                score=1.0 - (results["distances"][0][i] if results["distances"] else 0.0),
                metadata=results["metadatas"][0][i] if results["metadatas"] else {},
            ))

        return chunks

    # ── 清理 ──

    def clear(self) -> None:
        """删除所有 collection。"""
        try:
            collections = self._client.list_collections()
            for col in collections:
                try:
                    self._client.delete_collection(col.name)
                except Exception:
                    pass
        except Exception:
            pass

    @staticmethod
    def destroy(persona_knowledge_dir: Path) -> None:
        """删除 Persona 的向量库。"""
        chroma_path = _chroma_path(persona_knowledge_dir)
        if chroma_path.exists():
            shutil.rmtree(chroma_path)
