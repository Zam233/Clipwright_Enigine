"""JSON 目录素材源 — 从 JSON 文件中读取素材列表。

支持两种搜索模式：
- 关键词匹配（默认，<100 条素材时适用）
- 向量检索（素材量大时，自动使用嵌入模型 + 重排序，需调用 build_index()）
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from clipwright.config import logger
from clipwright.material.base import MaterialSource
from clipwright.schema.material import MaterialAsset, MaterialType


class JsonCatalogSource(MaterialSource):
    """从 JSON 文件加载的素材目录。

    小规模用关键词匹配；大规模时调用 build_index() 切换为向量检索。
    """

    source_id: str = ""
    source_name: str = ""

    def __init__(
        self,
        source_id: str,
        catalog_path: str | Path,
        source_name: str = "",
    ) -> None:
        self.source_id = source_id
        self.source_name = source_name or source_id
        self._catalog_path = Path(catalog_path)
        self._assets: list[MaterialAsset] = []
        self._vector_store: Any = None
        self._embedder: Any = None
        self._reranker: Any = None
        self._index_collection: str = f"mat_{source_id}"
        self._load()

    # ── 加载 ──

    def _load(self) -> None:
        """从 JSON 文件加载素材列表。"""
        if not self._catalog_path.exists():
            return
        raw = json.loads(self._catalog_path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            raw = raw.get("assets", raw.get("materials", []))
        for item in raw if isinstance(raw, list) else []:
            try:
                asset = MaterialAsset(
                    id=item.get("id", ""),
                    title=item.get("title", item.get("name", "")),
                    type=MaterialType(item["type"]) if "type" in item else MaterialType.VIDEO,
                    url=item.get("url"),
                    local_path=item.get("local_path"),
                    thumbnail_url=item.get("thumbnail_url"),
                    tags=item.get("tags", []),
                    duration_sec=item.get("duration_sec") or item.get("duration"),
                    file_size_bytes=item.get("file_size_bytes"),
                    resolution=item.get("resolution"),
                    source=self.source_id,
                    metadata=item.get("metadata", {}),
                )
                self._assets.append(asset)
            except (KeyError, ValueError):
                continue

    # ── 向量索引（大规模素材时使用） ──

    async def build_index(self, force_rebuild: bool = False) -> int:
        """将素材目录建立为向量索引。

        之后搜索会使用嵌入模型 + 重排序，而非关键词匹配。
        """
        from clipwright.rag.embedder import get_embedder
        from clipwright.rag.vector_store import VectorStore

        self._embedder = get_embedder()
        self._vector_store = VectorStore()

        if force_rebuild:
            self._vector_store.delete_collection(self._index_collection)

        # 将每个素材编码为文本块
        from clipwright.rag.chunker import Chunk
        chunks: list[Chunk] = []
        for i, asset in enumerate(self._assets):
            text = f"{asset.title} {' '.join(asset.tags)} {json.dumps(asset.metadata, ensure_ascii=False)}"
            chunks.append(Chunk(
                id=f"mat_{asset.id}",
                text=text,
                asset_id=asset.id,
                title=asset.title,
                type=asset.type,
                tags=",".join(asset.tags),
                source=self.source_id,
            ))

        # 建立向量索引
        indexed = self._vector_store.index_chunks(self._index_collection, chunks)
        logger.info("素材目录 %s 向量索引完成: %d 条", self.source_id, indexed)
        return indexed

    def has_index(self) -> bool:
        """检查是否已有向量索引。"""
        if self._vector_store is None:
            return False
        try:
            from clipwright.rag.vector_store import VectorStore as VS
            vs = VS()
            results = vs.search(self._index_collection, "test", top_k=1)
            return len(results) > 0
        except Exception:
            return False

    # ── 搜索 ──

    async def search(
        self,
        query: str,
        top_k: int = 10,
        rerank: bool = True,
        **kwargs: Any,
    ) -> list[tuple[MaterialAsset, float]]:
        """搜索素材。

        如果有向量索引，使用语义检索 + 可选重排序；
        否则回退到关键词匹配。
        """
        if self._vector_store is not None and self.has_index():
            return await self._vector_search(query, top_k, rerank)
        return self._keyword_search(query, top_k)

    async def _vector_search(
        self, query: str, top_k: int, rerank: bool
    ) -> list[tuple[MaterialAsset, float]]:
        """向量检索 + 重排序。"""
        from clipwright.config import settings
        from clipwright.rag.vector_store import VectorStore as VS

        vs = VS()

        # 阶段 1：向量检索
        candidates = vs.search(
            self._index_collection,
            query,
            top_k=settings.rag_rerank_top_k,
        )

        if not candidates:
            return []

        # 阶段 2：重排序
        if rerank:
            from clipwright.rag.reranker import Reranker
            reranker = Reranker()
            candidates = reranker.rerank(query, candidates, top_k=top_k)
        else:
            candidates = candidates[:top_k]

        # 映射回 MaterialAsset
        asset_map = {a.id: a for a in self._assets}
        results: list[tuple[MaterialAsset, float]] = []
        for c in candidates:
            asset_id = c.metadata.get("asset_id", "")
            asset = asset_map.get(asset_id)
            if asset:
                score = max(0.0, min(1.0, c.score)) if c.score else 0.0
                results.append((asset, score))
        return results

    def _keyword_search(
        self, query: str, top_k: int
    ) -> list[tuple[MaterialAsset, float]]:
        """关键词标签匹配搜索（回退方案）。"""
        query_lower = query.lower()
        query_terms = query_lower.split()

        scored: list[tuple[MaterialAsset, float]] = []
        for asset in self._assets:
            score = self._match(asset, query_terms)
            if score > 0:
                scored.append((asset, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    # ── 其他 ──

    async def get_asset(self, asset_id: str) -> MaterialAsset | None:
        for a in self._assets:
            if a.id == asset_id:
                return a
        return None

    async def count(self) -> int:
        return len(self._assets)

    async def list_all(self) -> list[MaterialAsset]:
        return list(self._assets)

    @staticmethod
    def _match(asset: MaterialAsset, query_terms: list[str]) -> float:
        """关键词相关度评分。"""
        score = 0.0
        text_pool = (
            [asset.title.lower()]
            + [t.lower() for t in asset.tags]
            + [str(v).lower() for v in asset.metadata.values() if isinstance(v, str)]
        )
        for term in query_terms:
            for text in text_pool:
                if term in text:
                    score += 0.3
                if text == term:
                    score += 0.2
        if any(term in asset.title.lower() for term in query_terms):
            score += 0.4
        return min(score, 1.0)
