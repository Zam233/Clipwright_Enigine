"""内置 Embedding Provider 实现（**能力方**：插件拥有实现，engine 只有协议与注册表）。

- ``openai``：OpenAI 兼容 ``/embeddings``（含 Ollama、DashScope 兼容模式等自建端点）；
- ``local``：sentence-transformers 本地模型（需完整 HF 仓库名）。

传输层（超时/代理/自定义头）仍是宿主能力：`clipwright.providers.transport.build_http_client`。
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

    def resolve_base_url(self, base_url: str | None) -> str | None:
        """默认端点归能力方：Ollama 走 llm_base_url，其余用调用方给的值。"""
        return base_url

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

    def resolve_base_url(self, base_url: str | None) -> str | None:
        """Ollama 的历史默认值：未显式配置时跟随 `llm_base_url`。"""
        from clipwright.config import settings

        return base_url or settings.llm_base_url or None


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
