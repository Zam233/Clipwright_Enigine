"""内置 Rerank Provider（方案 B4）。

两个内置实现：
- ``http``：通用重排序 API（DashScope / Cohere / 自建 `/rerank` 端点）；
- ``local``：sentence-transformers CrossEncoder 本地模型。

工厂形态：按 Provider 源凭据产出供 `rag/reranker.py` 调用的对象；
响应格式兼容解析留在 core（`rag/reranker.py::_parse_api_response`）。
"""
from __future__ import annotations

import os
from typing import Any, Mapping, Optional

from clipwright.config import logger
from clipwright.providers.transport import build_http_client


class HttpRerankProvider:
    """HTTP 重排序端点（远程）。"""

    name = "http"

    def default_model(self) -> str:
        from clipwright.config import settings
        return settings.rag_rerank_model

    def create_reranker(
        self, *, model: str, api_key: str | None, base_url: str | None,
        timeout_sec: float = 0.0, proxy: str = "", headers: Mapping[str, Any] | None = None,
    ) -> Any:
        return HttpReranker(
            model=model, api_key=api_key, base_url=base_url,
            timeout_sec=timeout_sec, proxy=proxy, headers=headers,
        )


class LocalRerankProvider:
    """本地 CrossEncoder（无网络传输参数）。"""

    name = "local"

    def default_model(self) -> str:
        from clipwright.config import settings
        return settings.rag_rerank_model

    def create_reranker(
        self, *, model: str, api_key: str | None, base_url: str | None,
        timeout_sec: float = 0.0, proxy: str = "", headers: Mapping[str, Any] | None = None,
    ) -> Any:
        return LocalCrossEncoder(model=model, hf_endpoint=base_url or "")


class HttpReranker:
    """调用远程 `/rerank` 端点，返回 [(索引, 分数)]（降序）。"""

    def __init__(
        self, *, model: str, api_key: Optional[str], base_url: Optional[str],
        timeout_sec: float = 0.0, proxy: str = "",
        headers: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self._model_name = model
        self._api_key = api_key
        self._base_url = base_url or ""
        self._timeout_sec = float(timeout_sec or 30.0)
        self._proxy = proxy or ""
        self._headers = dict(headers or {})

    async def rerank(
        self, *, query: str, documents: list[str], model: str = "", top_n: int = 0,
    ) -> list[tuple[int, float]]:
        import httpx

        url = self._base_url.rstrip("/")
        headers = {"Content-Type": "application/json"}
        headers.update({str(k): str(v) for k, v in self._headers.items()})
        if self._api_key:
            headers.setdefault("Authorization", f"Bearer {self._api_key}")

        payload = {
            "model": model or self._model_name,
            "query": query,
            "documents": documents,
            "top_n": top_n or len(documents),
        }
        http_client = build_http_client(self._proxy)
        # 失败时返回空列表，由 core 回退原始顺序（与历史行为一致）
        try:
            if http_client is not None:
                async with httpx.AsyncClient(
                    timeout=self._timeout_sec, proxy=self._proxy,
                ) as client:
                    resp = await client.post(url, json=payload, headers=headers)
            else:
                async with httpx.AsyncClient(timeout=self._timeout_sec) as client:
                    resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            logger.warning("重排序 API 调用失败（回退原始顺序）: %s", e)
            return []
        finally:
            if http_client is not None:
                try:
                    http_client.close()
                except Exception:  # noqa: BLE001
                    pass
        return parse_rerank_response(data, len(documents))


class LocalCrossEncoder:
    """本地 CrossEncoder 打分（懒加载模型）。"""

    def __init__(self, *, model: str, hf_endpoint: str = "") -> None:
        self._model_name = model
        self._hf_endpoint = hf_endpoint or ""
        self._model: Any = None

    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        from sentence_transformers import CrossEncoder

        if self._hf_endpoint:
            os.environ.setdefault("HF_ENDPOINT", self._hf_endpoint)
        if "/" not in self._model_name:
            raise RuntimeError(
                f"重排序模型名无效: '{self._model_name}'。"
                f"需要完整的 HuggingFace 仓库名，例如 'BAAI/bge-reranker-v2-m3'。"
            )
        self._model = CrossEncoder(self._model_name)
        return self._model

    async def rerank(
        self, *, query: str, documents: list[str], model: str = "", top_n: int = 0,
    ) -> list[tuple[int, float]]:
        import asyncio

        def _score() -> list[tuple[int, float]]:
            pairs = [(query, doc) for doc in documents]
            scores = self._load().predict(pairs).tolist()
            if scores and isinstance(scores[0], list):
                scores = [s[0] for s in scores]
            ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
            return [(i, float(s)) for i, s in ranked]

        return await asyncio.to_thread(_score)


def parse_rerank_response(data: Any, doc_count: int) -> list[tuple[int, float]]:
    """兼容多种重排序响应格式 → [(索引, 分数)]（降序）。"""
    raw: Any = []
    if isinstance(data, dict):
        raw = data.get("results") or data.get("output") or data.get("data") or []
    elif isinstance(data, list):
        raw = data

    out: list[tuple[int, float]] = []
    if isinstance(raw, list):
        for pos, item in enumerate(raw):
            if isinstance(item, dict):
                idx = item.get("index")
                score = item.get("relevance_score", item.get("score", item.get("relevanceScore", 0)))
                if idx is not None and 0 <= int(idx) < doc_count:
                    out.append((int(idx), float(score)))
            elif isinstance(item, (int, float)) and pos < doc_count:
                out.append((pos, float(item)))
    out.sort(key=lambda x: x[1], reverse=True)
    return out


def ensure_rerank_providers_registered() -> None:
    """把两个内置重排序 provider 登记进门面（幂等）。"""
    from clipwright.plugins.contribution import (
        BUILTIN_OWNER,
        Contribution,
        ContributionKind,
        ContributionRegistry,
    )

    if ContributionRegistry.resolve(ContributionKind.RERANK_PROVIDER, "http") is not None:
        return
    ContributionRegistry.register(BUILTIN_OWNER, [
        Contribution(ContributionKind.RERANK_PROVIDER, "http", HttpRerankProvider()),
        Contribution(ContributionKind.RERANK_PROVIDER, "local", LocalRerankProvider()),
    ], dispatch=False)


def resolve_rerank_provider(name: str) -> Any | None:
    """解析重排序 provider 实现（懒注册）；未知 → None。"""
    from clipwright.plugins.contribution import ContributionKind, ContributionRegistry

    ensure_rerank_providers_registered()
    impl = ContributionRegistry.resolve(ContributionKind.RERANK_PROVIDER, str(name or ""))
    return impl.object if impl is not None else None


def rerank_provider_names() -> list[str]:
    """已注册的重排序 provider 名（前端选项与错误信息共用）。"""
    from clipwright.plugins.contribution import ContributionKind, ContributionRegistry

    ensure_rerank_providers_registered()
    return [c.id for _, c in ContributionRegistry.list(ContributionKind.RERANK_PROVIDER)]
