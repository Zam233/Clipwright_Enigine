"""嵌入模型接口 — 支持多 Provider。

配置（.env）:
  CLIPWRIGHT_RAG_EMBED_PROVIDER=sentence_transformer|openai
  CLIPWRIGHT_RAG_EMBED_MODEL=BAAI/bge-small-zh-v1.5
  CLIPWRIGHT_RAG_EMBED_DIM=512
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from functools import partial
from typing import Optional

from clipwright.config import settings


class BaseEmbedder(ABC):
    """嵌入模型基类。"""

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """批量生成嵌入向量。"""
        ...

    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        """为查询生成嵌入向量（可能加 query prefix）。"""
        ...

    @property
    @abstractmethod
    def dim(self) -> int:
        """向量维度。"""
        ...

    async def aembed(self, texts: list[str]) -> list[list[float]]:
        return await asyncio.to_thread(self.embed, texts)

    async def aembed_query(self, text: str) -> list[float]:
        return await asyncio.to_thread(self.embed_query, text)


class SentenceTransformerEmbedder(BaseEmbedder):
    """基于 sentence-transformers 的本地嵌入模型。"""

    def __init__(self, model_name: str | None = None) -> None:
        import sentence_transformers
        model_name = model_name or settings.rag_embed_model
        if "/" not in model_name:
            raise RuntimeError(
                f"嵌入模型名无效: '{model_name}'。"
                f"需要完整的 HuggingFace 仓库名，例如 'BAAI/bge-small-zh-v1.5'。"
                f"请检查 CLIPWRIGHT_RAG_EMBED_MODEL 配置。"
            )
        self._model = sentence_transformers.SentenceTransformer(model_name)
        self._model_name = model_name

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self._model.encode(texts, normalize_embeddings=True).tolist()

    def embed_query(self, text: str) -> list[float]:
        return self._model.encode(text, normalize_embeddings=True).tolist()

    @property
    def dim(self) -> int:
        return self._model.get_sentence_embedding_dimension()


class OpenAIEmbedder(BaseEmbedder):
    """基于 OpenAI 兼容 API 的嵌入模型（支持自建 base_url）。"""

    def __init__(
        self,
        model_name: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        from openai import OpenAI
        model_name = model_name or "text-embedding-3-small"
        self._model_name = model_name
        self._client = OpenAI(api_key=api_key, base_url=base_url)

    def embed(self, texts: list[str]) -> list[list[float]]:
        # 分片：受 api batch size 限制
        from clipwright.config import settings
        batch_size = min(len(texts), settings.rag_embed_batch_size)
        all_embeddings: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            resp = self._client.embeddings.create(input=batch, model=self._model_name)
            all_embeddings.extend(d.embedding for d in resp.data)
        return all_embeddings

    def embed_query(self, text: str) -> list[float]:
        resp = self._client.embeddings.create(input=[text], model=self._model_name)
        return resp.data[0].embedding

    @property
    def dim(self) -> int:
        # 已知 model → dim 映射
        mapping = {
            "text-embedding-3-small": 1536,
            "text-embedding-3-large": 3072,
            "text-embedding-ada-002": 1536,
        }
        return mapping.get(self._model_name, 1536)


# ── 工厂 ──

_embedder_instance: Optional[BaseEmbedder] = None


def get_embedder() -> BaseEmbedder:
    """获取（缓存的）嵌入模型实例。"""
    global _embedder_instance
    if _embedder_instance is not None:
        return _embedder_instance

    provider = settings.rag_embed_provider
    api_key = settings.rag_embed_api_key
    base_url = settings.rag_embed_base_url

    if provider == "sentence_transformer":
        _embedder_instance = SentenceTransformerEmbedder()
    elif provider in ("openai", "ollama"):
        _embedder_instance = OpenAIEmbedder(
            model_name=settings.rag_embed_model,
            api_key=api_key or settings.llm_api_key,
            base_url=(base_url or settings.llm_base_url) if provider == "ollama" else base_url,
        )
    else:
        raise ValueError(f"Unsupported embed provider: {provider}")

    return _embedder_instance


def reset_embedder() -> None:
    """重置嵌入模型缓存（测试用）。"""
    global _embedder_instance
    _embedder_instance = None
