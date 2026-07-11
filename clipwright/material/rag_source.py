"""RAG 知识库素材源 — 基于向量检索 + 重排序的语义搜索。

复用 clipwright/rag/ 模块的完整管线：
- Chunker: 按 Markdown 标题 + 句子分块
- Embedder: sentence-transformer / OpenAI / Ollama
- VectorStore: ChromaDB
- Reranker: Cross-Encoder 重排序
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from clipwright.material.base import MaterialSource
from clipwright.schema.material import MaterialAsset, MaterialType
from clipwright.rag.retriever import Retriever


class RagKnowledgeSource(MaterialSource):
    """RAG 知识库素材源。

    基于已有 RAG 管线做语义搜索，将知识文档块作为素材返回。
    """

    source_id: str = ""
    source_name: str = ""

    def __init__(
        self,
        source_id: str,
        persona_id: str,
        source_name: str = "",
        retriever: Optional[Retriever] = None,
    ) -> None:
        self.source_id = source_id
        self.source_name = source_name or source_id
        self._persona_id = persona_id
        self._retriever = retriever or Retriever()
        self._documents: list[MaterialAsset] = []

    async def index_persona(self, persona_id: str) -> int:
        """为指定 Persona 建立知识库索引，返回索引的文档块数。"""
        from clipwright.config import settings
        from clipwright.rag.chunker import chunk_document

        kdir = settings.persona_dir / persona_id / "knowledge"
        if not kdir.exists():
            return 0

        index_path = kdir / "index.yaml"
        if not index_path.exists():
            return 0

        import yaml
        with open(index_path, encoding="utf-8") as f:
            index = yaml.safe_load(f) or []

        from clipwright.schema.persona import KnowledgeDoc
        docs: list[KnowledgeDoc] = []
        for entry in index:
            fpath = kdir / entry["file"]
            if fpath.exists():
                content = fpath.read_text(encoding="utf-8")
                docs.append(KnowledgeDoc(
                    id=entry.get("id", ""),
                    title=entry.get("title", ""),
                    content=content,
                    source=entry.get("source", ""),
                ))

        result = self._retriever.index_persona_knowledge(persona_id, docs)
        return result.total_chunks

    async def search(
        self,
        query: str,
        top_k: int = 10,
        **kwargs: Any,
    ) -> list[tuple[MaterialAsset, float]]:
        """通过 RAG 向量检索 + 重排序搜索知识库。"""
        results = await self._retriever.retrieve(
            self._persona_id,
            query,
            top_k=top_k,
            rerank=kwargs.get("rerank", True),
        )

        assets: list[tuple[MaterialAsset, float]] = []
        for i, chunk in enumerate(results.chunks):
            source_text = chunk.text[:200]
            asset = MaterialAsset(
                id=f"rag_{self._persona_id}_{i}",
                title=source_text,
                type=MaterialType.TEXT,
                tags=chunk.metadata.get("tags", []),
                source=self.source_id,
                metadata={
                    "persona_id": self._persona_id,
                    "chunk_text": chunk.text,
                    "score": chunk.score,
                    "doc_source": chunk.metadata.get("source", ""),
                },
            )
            # 分数 0-1 归一化
            score = max(0.0, min(1.0, chunk.score)) if chunk.score else 0.0
            assets.append((asset, score))

        return assets

    async def has_index(self) -> bool:
        """检查是否有已建立的向量索引。"""
        return self._retriever.has_index(self._persona_id)

    async def delete_index(self) -> None:
        """删除向量索引。"""
        self._retriever.delete_index(self._persona_id)

    @property
    def retriever(self) -> Retriever:
        return self._retriever
