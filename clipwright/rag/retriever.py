"""检索管线 — 整合嵌入 → 向量检索 → 重排序 → 上下文组装。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from clipwright.config import settings
from clipwright.rag.chunker import Chunk, chunk_document
from clipwright.rag.embedder import get_embedder
from clipwright.rag.reranker import Reranker
from clipwright.rag.vector_store import ScoredChunk, VectorStore
from clipwright.schema.persona import KnowledgeDoc


class RetrievalResult:
    """检索结果。"""
    context: str
    chunks: list[ScoredChunk]
    total_chunks: int

    def __init__(self, context: str, chunks: list[ScoredChunk]) -> None:
        self.context = context
        self.chunks = chunks
        self.total_chunks = len(chunks)


class IndexResult:
    """索引结果。"""
    total_chunks: int
    total_docs: int

    def __init__(self, total_chunks: int, total_docs: int) -> None:
        self.total_chunks = total_chunks
        self.total_docs = total_docs


class Retriever:
    """完整检索管线。"""

    def __init__(
        self,
        vector_store: Optional[VectorStore] = None,
        reranker: Optional[Reranker] = None,
    ) -> None:
        self._vector_store = vector_store or VectorStore()
        self._reranker = reranker
        self._reranker_enabled = True

    @property
    def reranker(self) -> Reranker:
        if self._reranker is None:
            self._reranker = Reranker()
        return self._reranker

    # ── 知识库索引 ──

    def index_persona_knowledge(
        self,
        persona_id: str,
        knowledge_docs: list[KnowledgeDoc],
    ) -> IndexResult:
        """为 Persona 的全部知识库文档建立向量索引。"""
        all_chunks: list[Chunk] = []
        for doc in knowledge_docs:
            chunks = chunk_document(
                content=doc.content,
                source=doc.title or doc.source or doc.id,
                persona_id=persona_id,
                doc_id=doc.id,
            )
            all_chunks.extend(chunks)

        total = self._vector_store.index_chunks(persona_id, all_chunks)
        return IndexResult(total_chunks=total, total_docs=len(knowledge_docs))

    def index_from_directory(
        self,
        persona_id: str,
        knowledge_dir: Path,
    ) -> IndexResult:
        """从 Persona 磁盘目录读取知识库并建索引。"""
        from clipwright.schema.persona import KnowledgeDoc

        kdir = knowledge_dir / "knowledge"
        index_path = kdir / "index.yaml"
        if not index_path.exists():
            return IndexResult(0, 0)

        import yaml
        with open(index_path, encoding="utf-8") as f:
            index = yaml.safe_load(f) or []

        docs: list[KnowledgeDoc] = []
        for entry in index:
            fpath = kdir / entry["file"]
            content = fpath.read_text(encoding="utf-8") if fpath.exists() else ""
            docs.append(KnowledgeDoc(
                id=entry.get("id", ""),
                title=entry.get("title", ""),
                content=content,
                source=entry.get("source", ""),
            ))

        return self.index_persona_knowledge(persona_id, docs)

    # ── 检索 ──

    async def retrieve(
        self,
        persona_id: str,
        query: str,
        top_k: int | None = None,
        rerank: bool = True,
    ) -> RetrievalResult:
        """执行完整检索：向量搜索 → 可选重排序 → 组装上下文。"""
        top_k = top_k or settings.rag_top_k

        # 1. 向量检索
        candidates = self._vector_store.search(
            persona_id,
            query,
            top_k=settings.rag_rerank_top_k,
        )

        # 1b. 没有索引时自动建索引（懒加载）
        if not candidates:
            from clipwright.config import settings as cfg
            kdir = cfg.persona_dir / persona_id / "knowledge"
            if kdir.exists() and (kdir / "index.yaml").exists():
                self.index_from_directory(persona_id, cfg.persona_dir / persona_id)
                candidates = self._vector_store.search(
                    persona_id,
                    query,
                    top_k=settings.rag_rerank_top_k,
                )

        if not candidates:
            return RetrievalResult(context="", chunks=[])

        # 2. 重排序
        if rerank:
            candidates = await self.reranker.rerank(query, candidates, top_k=top_k)
        else:
            candidates = candidates[:top_k]

        # 3. 组装上下文
        context = self._build_context(candidates)

        return RetrievalResult(context=context, chunks=candidates)

    @staticmethod
    def _build_context(chunks: list[ScoredChunk]) -> str:
        """将检索结果组装为可注入 prompt 的上下文。"""
        parts: list[str] = []
        for i, c in enumerate(chunks):
            source = c.metadata.get("source", "")
            header = f"【参考 {i+1}】" + (f" 来自「{source}」" if source else "")
            parts.append(f"{header}\n{c.text}")
        return "\n\n---\n\n".join(parts)

    # ── 管理 ──

    def has_index(self, persona_id: str) -> bool:
        """检查 Persona 是否有向量索引。"""
        try:
            chunks = self._vector_store.search(persona_id, "test", top_k=1)
            return True
        except (ValueError, FileNotFoundError):
            return False

    def delete_index(self, persona_id: str) -> None:
        """删除 Persona 的向量索引。"""
        self._vector_store.delete_collection(persona_id)
