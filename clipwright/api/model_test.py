"""模型测试 API — 快速验证 LLM / Embedding / Reranker。"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter
from pydantic import BaseModel

from clipwright.config import settings
from clipwright.rag.embedder import get_embedder, reset_embedder
from clipwright.rag.reranker import Reranker
from clipwright.rag.vector_store import ScoredChunk
from clipwright.services.llm import LLMService

router = APIRouter(prefix="/api/test", tags=["model-test"])


class LlmTestRequest(BaseModel):
    prompt: str
    model: str = ""


class EmbedTestRequest(BaseModel):
    text: str
    provider: str = ""


class RerankTestRequest(BaseModel):
    query: str
    candidates: list[str]
    top_k: int = 5


@router.post("/llm")
async def test_llm(req: LlmTestRequest) -> dict:
    """测试 LLM 调用。"""
    svc = LLMService()
    messages = [{"role": "user", "content": req.prompt}]
    try:
        resp = await svc.generate(messages=messages, model=req.model or None)
        return {
            "success": resp.success,
            "content": resp.content[:2000] if resp.content else "",
            "model": req.model or settings.llm_model,
            "provider": settings.llm_provider,
            "usage": {
                "input_tokens": resp.usage.input_tokens,
                "output_tokens": resp.usage.output_tokens,
            },
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/embed")
async def test_embed(req: EmbedTestRequest) -> dict:
    """测试嵌入模型。"""
    try:
        if req.provider:
            settings.rag_embed_provider = req.provider  # type: ignore
            reset_embedder()
        embedder = get_embedder()
        vec = await asyncio.to_thread(embedder.embed, [req.text])
        return {
            "success": True,
            "dimension": len(vec[0]) if vec else 0,
            "vector_preview": [round(v, 6) for v in vec[0][:8]],
            "model": settings.rag_embed_model,
            "provider": settings.rag_embed_provider,
        }
    except Exception as e:
        # 出错后重置缓存，允许下次重试
        reset_embedder()
        return {"success": False, "error": str(e)}


@router.post("/rerank")
async def test_rerank(req: RerankTestRequest) -> dict:
    """测试重排序模型。"""
    try:
        reranker = Reranker()
        candidates = [
            ScoredChunk(id=f"c{i}", text=t, score=0.5)
            for i, t in enumerate(req.candidates)
        ]
        results = await reranker.rerank(req.query, candidates, top_k=req.top_k)
        return {
            "success": True,
            "results": [
                {"text": c.text[:200], "score": round(c.score, 4)}
                for c in results
            ],
            "model": settings.rag_rerank_model,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/config")
async def get_config() -> dict:
    """返回当前模型配置。"""
    return {
        "llm": {
            "provider": settings.llm_provider,
            "model": settings.llm_model,
            "base_url": settings.llm_base_url,
            "has_api_key": bool(settings.llm_api_key),
        },
        "embed": {
            "provider": settings.rag_embed_provider,
            "model": settings.rag_embed_model,
            "dim": settings.rag_embed_dim,
            "base_url": settings.rag_embed_base_url,
            "has_api_key": bool(settings.rag_embed_api_key or settings.llm_api_key),
        },
        "rerank": {
            "model": settings.rag_rerank_model,
            "base_url": settings.rag_rerank_base_url,
        },
        "rag": {
            "top_k": settings.rag_top_k,
            "rerank_top_k": settings.rag_rerank_top_k,
            "chunk_size": settings.rag_chunk_size,
        },
    }
