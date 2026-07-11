"""RAG API — 知识库向量检索和索引管理。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from clipwright.persona.repository import PersonaRepository
from clipwright.rag.retriever import Retriever

router = APIRouter(prefix="/api/persona", tags=["rag"])

_retriever = Retriever()
_repo = PersonaRepository.from_settings()


class QueryRequest(BaseModel):
    query: str
    top_k: int = 5
    rerank: bool = True


class IndexRequest(BaseModel):
    force_rebuild: bool = False


@router.post("/{persona_id}/rag/query")
async def rag_query(persona_id: str, req: QueryRequest) -> dict:
    """对 Persona 的知识库执行语义检索。"""
    if not _repo.exists(persona_id):
        raise HTTPException(status_code=404, detail=f"Persona {persona_id} not found")

    result = await _retriever.retrieve(
        persona_id=persona_id,
        query=req.query,
        top_k=req.top_k,
        rerank=req.rerank,
    )

    return {
        "persona_id": persona_id,
        "query": req.query,
        "total_chunks": result.total_chunks,
        "context": result.context,
        "chunks": [
            {
                "id": c.id,
                "text": c.text[:200] + ("..." if len(c.text) > 200 else ""),
                "score": round(c.score, 4),
                "source": c.metadata.get("source", ""),
            }
            for c in result.chunks
        ],
    }


@router.post("/{persona_id}/rag/index")
async def rag_index(persona_id: str, req: IndexRequest) -> dict:
    """为 Persona 的知识库建立/刷新向量索引。"""
    if not _repo.exists(persona_id):
        raise HTTPException(status_code=404, detail=f"Persona {persona_id} not found")

    if req.force_rebuild:
        _retriever.delete_index(persona_id)

    manifest = _repo.load_manifest(persona_id)
    if not manifest.knowledge:
        raise HTTPException(status_code=400, detail="Persona has no knowledge documents")

    result = _retriever.index_persona_knowledge(persona_id, manifest.knowledge)

    return {
        "status": "ok",
        "persona_id": persona_id,
        "total_docs": result.total_docs,
        "total_chunks": result.total_chunks,
    }


@router.get("/{persona_id}/rag/status")
async def rag_status(persona_id: str) -> dict:
    """查询 Persona 的向量索引状态。"""
    if not _repo.exists(persona_id):
        raise HTTPException(status_code=404)

    manifest = _repo.load_manifest(persona_id)
    has_index = _retriever.has_index(persona_id)
    doc_count = len(manifest.knowledge or [])

    return {
        "persona_id": persona_id,
        "has_index": has_index,
        "knowledge_doc_count": doc_count,
        "indexed": has_index and doc_count > 0,
    }


@router.delete("/{persona_id}/rag/index")
async def rag_delete_index(persona_id: str) -> dict:
    """删除 Persona 的向量索引。"""
    if not _repo.exists(persona_id):
        raise HTTPException(status_code=404)
    _retriever.delete_index(persona_id)
    return {"status": "deleted", "persona_id": persona_id}
