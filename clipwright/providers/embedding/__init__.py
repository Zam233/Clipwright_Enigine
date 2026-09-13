"""内置 Embedding Provider（方案 B4）。

两个内置实现：
- ``openai``：OpenAI 兼容 ``/embeddings``（含 Ollama、DashScope 兼容模式等自建端点）；
- ``local``：sentence-transformers 本地模型（需完整 HF 仓库名）。

工厂形态：按 Provider 源凭据产出 `rag/embedder.py::BaseEmbedder` 同形对象，
超时/代理/自定义头作为一等参数落到 httpx 传输层（不再只存不用）。
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

from clipwright.providers.transport import build_http_client


class OpenAIEmbeddingProvider:
    """OpenAI 兼容嵌入端点（远程）。"""

    name = "openai"

    def default_model(self) -> str:
        from clipwright.config import settings
        return settings.rag_embed_model

    def create_embedder(
        self, *, model: str, api_key: str | None, base_url: str | None,
        timeout_sec: float = 0.0, proxy: str = "", headers: Mapping[str, Any] | None = None,
    ) -> Any:
        return RemoteEmbedder(
            model=model, api_key=api_key, base_url=base_url,
            timeout_sec=timeout_sec, proxy=proxy, headers=headers,
        )


class OllamaEmbeddingProvider(OpenAIEmbeddingProvider):
    """本地 Ollama（OpenAI 兼容端点），默认无 api_key。"""

    name = "ollama"


class LocalEmbeddingProvider:
    """sentence-transformers 本地模型（无网络传输参数）。"""

    name = "local"

    def default_model(self) -> str:
        from clipwright.config import settings
        return settings.rag_embed_model

    def create_embedder(
        self, *, model: str, api_key: str | None, base_url: str | None,
        timeout_sec: float = 0.0, proxy: str = "", headers: Mapping[str, Any] | None = None,
    ) -> Any:
        from clipwright.rag.embedder import SentenceTransformerEmbedder
        return SentenceTransformerEmbedder(model_name=model or None)


class RemoteEmbedder:
    """OpenAI 兼容嵌入客户端（惰性建连；支持超时/代理/自定义头）。"""

    def __init__(
        self, *, model: str, api_key: Optional[str], base_url: Optional[str],
        timeout_sec: float = 0.0, proxy: str = "",
        headers: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self._model_name = model
        self._api_key = api_key
        self._base_url = base_url
        self._timeout_sec = float(timeout_sec or 0.0)
        self._proxy = proxy or ""
        self._headers = dict(headers or {})
        self._client: Any = None

    def _sdk(self) -> Any:
        if self._client is not None:
            return self._client
        from openai import OpenAI

        kwargs: dict[str, Any] = {
            "api_key": self._api_key,
            "base_url": self._base_url,
        }
        if self._timeout_sec:
            kwargs["timeout"] = self._timeout_sec
        if self._headers:
            kwargs["default_headers"] = {str(k): str(v) for k, v in self._headers.items()}
        http_client = build_http_client(self._proxy)
        if http_client is not None:
            kwargs["http_client"] = http_client
        try:
            self._client = OpenAI(**kwargs)
        except Exception:  # noqa: BLE001 — 代理/头不被 SDK 接受时回退基础构造
            self._client = OpenAI(api_key=self._api_key, base_url=self._base_url)
        return self._client

    def embed(self, texts: list[str]) -> list[list[float]]:
        from clipwright.config import settings
        batch_size = max(1, min(len(texts), settings.rag_embed_batch_size))
        out: list[list[float]] = []
        client = self._sdk()
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            resp = client.embeddings.create(input=batch, model=self._model_name)
            out.extend(d.embedding for d in resp.data)
        return out

    def embed_query(self, text: str) -> list[float]:
        resp = self._sdk().embeddings.create(input=[text], model=self._model_name)
        return resp.data[0].embedding

    @property
    def dim(self) -> int:
        return _KNOWN_EMBED_DIMS.get(self._model_name, 1536)


#: 已知模型维度（未收录模型按 1536 兜底，与历史行为一致）
_KNOWN_EMBED_DIMS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
    "bge-m3": 1024,
    "text-embedding-v3": 1024,
    "qwen3-embedding-8b": 4096,
}


def ensure_embedding_providers_registered() -> None:
    """把三个内置嵌入 provider 登记进门面（幂等）。"""
    from clipwright.plugins.contribution import (
        BUILTIN_OWNER,
        Contribution,
        ContributionKind,
        ContributionRegistry,
    )

    if ContributionRegistry.resolve(ContributionKind.EMBEDDING_PROVIDER, "openai") is not None:
        return
    ContributionRegistry.register(BUILTIN_OWNER, [
        Contribution(ContributionKind.EMBEDDING_PROVIDER, "openai", OpenAIEmbeddingProvider()),
        Contribution(ContributionKind.EMBEDDING_PROVIDER, "ollama", OllamaEmbeddingProvider()),
        Contribution(ContributionKind.EMBEDDING_PROVIDER, "local", LocalEmbeddingProvider()),
    ], dispatch=False)


def resolve_embedding_provider(name: str) -> Any | None:
    """解析嵌入 provider 实现（懒注册）；未知 → None。"""
    from clipwright.plugins.contribution import ContributionKind, ContributionRegistry

    ensure_embedding_providers_registered()
    impl = ContributionRegistry.resolve(ContributionKind.EMBEDDING_PROVIDER, str(name or ""))
    return impl.object if impl is not None else None


def embedding_provider_names() -> list[str]:
    """已注册的嵌入 provider 名（前端选项与错误信息共用）。"""
    from clipwright.plugins.contribution import ContributionKind, ContributionRegistry

    ensure_embedding_providers_registered()
    return [c.id for _, c in ContributionRegistry.list(ContributionKind.EMBEDDING_PROVIDER)]
