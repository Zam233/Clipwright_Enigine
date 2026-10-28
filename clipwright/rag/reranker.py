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

#: 「显式关闭重排」的模型名（与"配置写错了"区分开——前者是 INFO，后者是 WARNING）。
#: 本地交叉编码器首次加载实测约 306 秒，所以关掉是常见且合理的选择；
#: `.env.example` 默认就给这个值，避免留一个"看起来像模型名但不合法"的名字。
DISABLED_MODEL_NAMES: frozenset[str] = frozenset({"", "disabled", "none", "off", "no"})


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

    #: 进程级失败记录：`配置键 → 失败原因`。
    #:
    #: 为什么需要：`search_shots(rerank=True)` 是**每次检索**都会走的默认路径，
    #: 而重排器配置无效时每次都要走一遍导入/连接尝试（实测单次 23 秒）——
    #: 同样的失败重试 N 次毫无意义，只是把"配置错了"变成"系统很慢"。
    #:
    #: 为什么**按配置键控**而不是一个全局布尔：全局闩锁会把"改好配置"也一起锁死
    #: （必须重启进程才恢复），而且会让测试之间互相污染——实测第一版就是这么
    #: 打红了两条既有用例（一个测试把闩锁打开，后面的测试全被短路）。
    #: 现在：同一份无效配置不再重试；换了配置（模型名/地址/key 任一变化）就重新尝试。
    _unavailable: dict[tuple[str, str, bool], str] = {}

    def _config_key(self) -> tuple[str, str, bool]:
        return (str(self._model_name or ""), str(self._base_url or ""),
                bool(self._api_key))

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

    @classmethod
    def reset_failure_latch(cls) -> None:
        """清掉全部失败记录（改完配置后调用，不必重启进程）。"""
        cls._unavailable.clear()

    @property
    def unavailable_reason(self) -> str | None:
        return type(self)._unavailable.get(self._config_key())

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

        # **Provider 绑定优先于一切**（既有设计：绑定存在时源为唯一真源）。
        #
        # 顺序很重要：实测踩到——`.env` 里把 `RAG_RERANK_MODEL` 设成 `disabled`（有意关闭本地重排）
        # 之后，如果把"显式关闭"的判定放在绑定之前，就会**把用户的 provider 绑定也一起关掉**，
        # 绑定的自有模型根本没机会生效。绑定给了模型，就听绑定的。
        impl = _bound_reranker()
        if impl is not None:
            client, bound_model = impl
            return await self._rerank_via_provider(
                client, bound_model, query, candidates, top_k)

        latched = type(self)._unavailable.get(self._config_key())
        if latched:
            logger.debug("重排器不可用（同一配置已失败过，跳过重试）：%s", latched)
            return candidates[:top_k]

        # 显式关闭 vs 配置错误：两者的日志级别必须不同。
        # `disabled`/`none`/空 是**有意关闭**（本地交叉编码器首次加载约 306 秒，
        # 关掉是合理选择），打 WARNING 会让人以为配置坏了；
        # 而"看起来像模型名但不含 `/`"才是配置错误，必须显式告警。
        if str(self._model_name or "").strip().lower() in DISABLED_MODEL_NAMES:
            type(self)._unavailable[self._config_key()] = "已显式关闭（DISABLED_MODEL_NAMES）"
            logger.info("重排器已显式关闭（%r），检索按融合分排序", self._model_name)
            return candidates[:top_k]

        if self._base_url and not self._is_hf_mirror(self._base_url):
            # ── API 模式 ──
            try:
                return await self._rerank_api(query, candidates, top_k)
            except Exception as exc:  # noqa: BLE001 — 失败回退原序，并记闩锁
                type(self)._unavailable[self._config_key()] = f"API 模式失败：{exc}"
                logger.warning("重排序 API 失败（回退原始顺序，后续检索不再重试）: %s", exc)
                return candidates[:top_k]
        else:
            # ── 本地模式 ──
            try:
                return self._rerank_local(query, candidates, top_k)
            except Exception as exc:  # noqa: BLE001 — 失败回退原序，并记闩锁
                type(self)._unavailable[self._config_key()] = f"本地模式失败：{exc}"
                logger.warning("重排序不可用（回退原始顺序，后续检索不再重试）: %s", exc)
                return candidates[:top_k]

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
        # **先校验模型名，再导入 sentence_transformers**。
        #
        # 顺序很重要（实测：一次检索白等 23 秒）：这句 import 在本环境要 20 秒左右，
        # 而"模型名不含 `/`"是无配置时最常见的状态——先 import 再报错等于
        # **每次检索都先浪费 20 秒，然后静默退回无重排**。
        # 配置错误应该在毫秒级暴露，而不是用"慢"来掩盖。
        model_name = self._model_name
        if "/" not in model_name:
            raise RuntimeError(
                f"重排序模型名无效: '{model_name}'。"
                f"需要完整的 HuggingFace 仓库名，例如 'BAAI/bge-reranker-v2-m3'。"
                f"请检查 CLIPWRIGHT_RAG_RERANK_MODEL 配置。"
            )

        from sentence_transformers import CrossEncoder

        if self._base_url:
            os.environ.setdefault("HF_ENDPOINT", self._base_url)

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
            async with httpx.AsyncClient(timeout=120) as client:
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
