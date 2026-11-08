"""内置 Rerank Provider 实现（**能力方**：插件拥有实现，engine 只留协议与注册表）。

- ``http``：通用重排序 API（DashScope / Cohere / 自建 `/rerank` 端点）；
- ``local``：sentence-transformers CrossEncoder 本地模型。

响应格式解析 `parse_rerank_response` 仍属**平台解析**，留在 engine（`providers/rerank`）。
"""
from __future__ import annotations

import os
from typing import Any, Mapping, Optional

from clipwright.config import logger
from clipwright.providers.rerank import parse_rerank_response
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
