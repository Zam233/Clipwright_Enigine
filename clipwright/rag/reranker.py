"""重排序 — Cross-Encoder 或 API 服务。

支持两种模式：
1. 本地模式：`sentence-transformers` CrossEncoder 模型，从 HuggingFace 加载
2. API 模式：通过 `base_url` 调用远程重排序 API（如 DashScope/Cohere）

配置：
  CLIPWRIGHT_RAG_RERANK_MODEL=BAAI/bge-reranker-v2-m3  # 本地 HF 模型
  CLIPWRIGHT_RAG_RERANK_BASE_URL=...                    # 可选：API 模式
  CLIPWRIGHT_RAG_RERANK_API_KEY=...                     # 可选
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

import httpx

from clipwright.config import logger, settings
from clipwright.rag.vector_store import ScoredChunk


def _bound_reranker() -> Any | None:
    """B4：按「重排序」角色绑定产出 (客户端, 模型名)（未绑定 → None 走旧路径）。

    绑定存在时源为唯一真源（provider/model/base_url/key/超时/代理/自定义头）；
    provider 未注册或存储异常时返回 None，调用方回退既有 base_url 启发式分支。
    """
    try:
        from clipwright.services import provider_sources as ps
        bound = ps.resolve("rerank")
    except Exception:  # noqa: BLE001 — 存储层异常不阻断（回退旧配置）
        return None
    if not bound:
        return None

    from clipwright.providers.rerank import resolve_rerank_provider
    impl = resolve_rerank_provider(bound.get("provider") or "http")
    if impl is None:
        return None
    model = str(bound.get("model") or settings.rag_rerank_model)
    client = impl.create_reranker(
        model=model,
        api_key=bound.get("api_key"),
        base_url=bound.get("base_url"),
        timeout_sec=float(bound.get("timeout_sec") or 0.0),
        proxy=str(bound.get("proxy") or ""),
        headers=dict(bound.get("headers") or {}) or None,
    )
    return client, model


class Reranker:
    """重排序器 — 本地 CrossEncoder 或 API 服务。"""

    def __init__(
        self,
        model_name: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self._model_name = model_name or settings.rag_rerank_model
        self._base_url = base_url or settings.rag_rerank_base_url
        self._api_key = api_key or settings.rag_rerank_api_key
        self._model = None  # 懒加载（仅本地模式）

    async def rerank(
        self,
        query: str,
        candidates: list[ScoredChunk],
        top_k: int | None = None,
    ) -> list[ScoredChunk]:
        """对候选块进行重排序，返回 Top-K。"""
        if not candidates:
            return []

        top_k = top_k or settings.rag_top_k

        impl = _bound_reranker()
        if impl is not None:
            client, bound_model = impl
            return await self._rerank_via_provider(client, bound_model, query, candidates, top_k)

        if self._base_url and not self._is_hf_mirror(self._base_url):
            # ── API 模式 ──
            return await self._rerank_api(query, candidates, top_k)
        else:
            # ── 本地模式 ──
            return self._rerank_local(query, candidates, top_k)

    async def _rerank_via_provider(
        self,
        impl: Any,
        model: str,
        query: str,
        candidates: list[ScoredChunk],
        top_k: int,
    ) -> list[ScoredChunk]:
        """B4：走 Provider 源的「重排序」角色绑定（源为唯一真源）。"""
        try:
            ranked = await impl.rerank(
                query=query,
                documents=[c.text for c in candidates],
                model=model,
                top_n=min(top_k, len(candidates)),
            )
        except Exception as e:  # noqa: BLE001 — 与 API 模式同语义：失败回退原序
            logger.warning("重排序 provider 调用失败（回退原始顺序）: %s", e)
            return candidates[:top_k]
        if not ranked:
            return candidates[:top_k]
        out: list[ScoredChunk] = []
        for idx, score in ranked[:top_k]:
            if 0 <= idx < len(candidates):
                c = candidates[idx]
                out.append(ScoredChunk(id=c.id, text=c.text, score=float(score),
                                       metadata=c.metadata))
        return out or candidates[:top_k]

    # ── 本地 CrossEncoder ──

    def _lazy_load_local(self):
        if self._model is not None:
            return
        from sentence_transformers import CrossEncoder

        if self._base_url:
            os.environ.setdefault("HF_ENDPOINT", self._base_url)

        model_name = self._model_name
        if "/" not in model_name:
            raise RuntimeError(
                f"重排序模型名无效: '{model_name}'。"
                f"需要完整的 HuggingFace 仓库名，例如 'BAAI/bge-reranker-v2-m3'。"
                f"请检查 CLIPWRIGHT_RAG_RERANK_MODEL 配置。"
            )
        self._model = CrossEncoder(model_name)

    def _rerank_local(
        self,
        query: str,
        candidates: list[ScoredChunk],
        top_k: int,
    ) -> list[ScoredChunk]:
        self._lazy_load_local()

        pairs = [(query, c.text) for c in candidates]
        scores = self._model.predict(pairs).tolist()

        if scores and isinstance(scores[0], list):
            scores = [s[0] for s in scores]

        ranked = sorted(
            zip(candidates, scores),
            key=lambda x: x[1],
            reverse=True,
        )

        return [
            ScoredChunk(
                id=chunk.id,
                text=chunk.text,
                score=float(score),
                metadata=chunk.metadata,
            )
            for chunk, score in ranked[:top_k]
        ]

    # ── API 模式 ──

    async def _rerank_api(
        self,
        query: str,
        candidates: list[ScoredChunk],
        top_k: int,
    ) -> list[ScoredChunk]:
        """调用远程重排序 API（异步，不阻塞事件循环）。"""
        url = self._base_url.rstrip("/")
        api_key = self._api_key

        documents = [c.text for c in candidates]
        payload = {
            "model": self._model_name,
            "query": query,
            "documents": documents,
            "top_n": min(top_k, len(candidates)),
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}" if api_key else "",
        }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            # API 调用失败，回退到原始顺序（按原分数排序）
            return candidates[:top_k]

        # 解析多种 API 响应格式
        results = self._parse_api_response(data, candidates)

        # 如果 API 返回空，回退到原始顺序
        if not results:
            return candidates[:top_k]

        return results[:top_k]

    @staticmethod
    def _parse_api_response(
        data: dict,
        candidates: list[ScoredChunk],
    ) -> list[ScoredChunk]:
        """兼容多种重排序 API 的响应格式。"""
        results: list[ScoredChunk] = []

        # 格式 1: { "results": [{"index": 0, "relevance_score": 0.95}, ...] }
        raw = data.get("results") or data.get("output") or data.get("data") or []
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    idx = item.get("index")
                    score = item.get(
                        "relevance_score",
                        item.get("score", item.get("relevanceScore", 0)),
                    )
                    if idx is not None and idx < len(candidates):
                        results.append(ScoredChunk(
                            id=candidates[idx].id,
                            text=candidates[idx].text,
                            score=float(score),
                            metadata=candidates[idx].metadata,
                        ))

        # 格式 2: 返回就是带分数列表
        if not results and isinstance(raw, list):
            for item in raw:
                if isinstance(item, (int, float)):
                    results.append(ScoredChunk(
                        id=candidates[len(results)].id if len(results) < len(candidates) else "",
                        text=candidates[len(results)].text if len(results) < len(candidates) else "",
                        score=float(item),
                    ))

        if results:
            results.sort(key=lambda x: x.score, reverse=True)
        return results

    @staticmethod
    def _is_hf_mirror(url: str) -> bool:
        """判断是否为 HuggingFace 镜像地址。"""
        lower = url.lower()
        return "huggingface" in lower or "hf-mirror" in lower or "modelscope" in lower
