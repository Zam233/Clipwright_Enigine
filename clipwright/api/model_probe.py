"""模型测试 API — 快速验证 LLM / Embedding / Reranker。

模块名不用 ``*_test.py`` 后缀，避免被 pytest 当作测试收集。

P5 修复：本模块此前只读 `settings.llm_*` / `rag_*`，而实际调用走「模型提供商」
的角色绑定 → 测试页显示的 provider/model 与真实生效的**不一致**（诊断误导）。
现在统一用 `_effective_*()` 解析「角色绑定优先、settings 兜底」，并在响应里
标明来源（`effective` / `source`）。

P4 修复：`/api/test/embed` 曾直接改写全局 `settings.rag_embed_provider`（进程级
副作用，且在有角色绑定时被静默忽略）。现在改为按需构造一次性 embedder，不碰全局。
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from clipwright.config import settings
from clipwright.rag.embedder import get_embedder
from clipwright.rag.reranker import Reranker
from clipwright.rag.vector_store import ScoredChunk
from clipwright.services.llm import LLMService

router = APIRouter(prefix="/api/test", tags=["model-test"])


def _binding(role: str) -> dict[str, Any] | None:
    """角色绑定的源信息（无绑定 → None）。"""
    try:
        from clipwright.services import provider_sources as ps
        return ps.resolve(role)
    except Exception:  # noqa: BLE001 — 存储层异常按未绑定处理
        return None


def _effective_llm() -> dict[str, Any]:
    bound = _binding("main")
    if bound:
        return {"effective": "provider_source", "source": bound.get("source_name") or "",
                "provider": bound.get("provider") or "", "model": bound.get("model") or ""}
    return {"effective": "settings", "source": "",
            "provider": settings.llm_provider, "model": settings.llm_model}


def _effective_embed() -> dict[str, Any]:
    bound = _binding("embed")
    if bound:
        return {"effective": "provider_source", "source": bound.get("source_name") or "",
                "provider": bound.get("provider") or "", "model": bound.get("model") or ""}
    return {"effective": "settings", "source": "",
            "provider": settings.rag_embed_provider, "model": settings.rag_embed_model}


def _effective_rerank() -> dict[str, Any]:
    bound = _binding("rerank")
    if bound:
        return {"effective": "provider_source", "source": bound.get("source_name") or "",
                "provider": bound.get("provider") or "", "model": bound.get("model") or ""}
    return {"effective": "settings", "source": "",
            "provider": "", "model": settings.rag_rerank_model}


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
    """测试 LLM 调用（报告真实生效的 provider/model 与来源）。"""
    eff = _effective_llm()
    svc = LLMService()
    messages = [{"role": "user", "content": req.prompt}]
    try:
        resp = await svc.generate(messages=messages, model=req.model or None)
        return {
            "success": resp.success,
            "content": resp.content[:2000] if resp.content else "",
            "model": req.model or eff["model"],
            "provider": eff["provider"],
            "effective": eff["effective"],
            "source": eff["source"],
            "usage": {
                "input_tokens": resp.usage.input_tokens,
                "output_tokens": resp.usage.output_tokens,
            },
        }
    except Exception as e:
        return {"success": False, "error": str(e), **eff}


@router.post("/embed")
async def test_embed(req: EmbedTestRequest) -> dict:
    """测试嵌入模型。

    - 有 `embed` 角色绑定 → 用绑定（源是唯一真源），并说明请求里的 provider 被忽略；
    - 无绑定且传了 `provider` → 用门面按需构造**一次性** embedder（不改全局 settings）。
    """
    eff = _effective_embed()
    note = ""
    try:
        if req.provider and eff["effective"] == "provider_source":
            note = f"已忽略 provider={req.provider}：当前使用角色绑定的源（{eff['source']}）"
            embedder = get_embedder()
        elif req.provider:
            from clipwright.providers.embedding import resolve_embedding_provider
            impl = resolve_embedding_provider(req.provider)
            if impl is None:
                raise ValueError(f"未知的嵌入 provider: {req.provider}")
            embedder = impl.create_embedder(
                model=settings.rag_embed_model,
                api_key=settings.rag_embed_api_key or None,
                base_url=settings.rag_embed_base_url or None,
            )
            eff = {**eff, "provider": req.provider}
        else:
            embedder = get_embedder()
        vec = await asyncio.to_thread(embedder.embed, [req.text])
        return {
            "success": True,
            "dimension": len(vec[0]) if vec else 0,
            "vector_preview": [round(v, 6) for v in vec[0][:8]],
            "model": eff["model"],
            "provider": eff["provider"],
            "effective": eff["effective"],
            "source": eff["source"],
            **({"note": note} if note else {}),
        }
    except Exception as e:
        return {"success": False, "error": str(e), **eff}


@router.post("/rerank")
async def test_rerank(req: RerankTestRequest) -> dict:
    """测试重排序模型（报告真实生效的来源）。"""
    eff = _effective_rerank()
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
            "model": eff["model"],
            "provider": eff["provider"],
            "effective": eff["effective"],
            "source": eff["source"],
        }
    except Exception as e:
        return {"success": False, "error": str(e), **eff}


@router.get("/config")
async def get_config() -> dict:
    """返回当前模型配置（含「角色绑定 or settings 兜底」的生效来源）。"""
    llm_eff, embed_eff, rerank_eff = _effective_llm(), _effective_embed(), _effective_rerank()
    return {
        "llm": {
            "provider": llm_eff["provider"],
            "model": llm_eff["model"],
            "effective": llm_eff["effective"],
            "source": llm_eff["source"],
            "base_url": settings.llm_base_url,
            "has_api_key": bool(settings.llm_api_key),
        },
        "embed": {
            "provider": embed_eff["provider"],
            "model": embed_eff["model"],
            "effective": embed_eff["effective"],
            "source": embed_eff["source"],
            "dim": settings.rag_embed_dim,
            "base_url": settings.rag_embed_base_url,
            "has_api_key": bool(settings.rag_embed_api_key or settings.llm_api_key),
        },
        "rerank": {
            "model": rerank_eff["model"],
            "provider": rerank_eff["provider"],
            "effective": rerank_eff["effective"],
            "source": rerank_eff["source"],
            "base_url": settings.rag_rerank_base_url,
        },
        "rag": {
            "top_k": settings.rag_top_k,
            "rerank_top_k": settings.rag_rerank_top_k,
            "chunk_size": settings.rag_chunk_size,
        },
    }
