"""配置变更后的缓存失效钩子。

settings 单例被就地 setattr 后，部分模块持有「按旧配置构建」的缓存实例
（LLM 客户端、embedding 模型、视觉服务单例、编码器探测结果），
需要显式失效才能让新配置真正生效。本模块维护 prefix → 钩子 的注册表，
由 ``api/settings.py`` 在每次 PUT/reset 后调用 :func:`apply_hooks`。

钩子函数体内部惰性 import，避免 services ↔ api 循环依赖。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Callable, Iterable

logger = logging.getLogger("clipwright.settings")

_HOOKS: dict[str, list[Callable[[], None]]] = defaultdict(list)


def register(prefixes: Iterable[str], fn: Callable[[], None]) -> None:
    """注册钩子：任一变更键以 prefixes 之一开头时调用 fn。"""
    for p in prefixes:
        _HOOKS[p].append(fn)


def _builtin_hooks() -> None:
    """内置钩子（模块首次使用时注册一次）。"""
    if _HOOKS:  # 已注册
        return

    def _clear_llm_clients() -> None:
        from clipwright.services import llm as llm_mod

        llm_mod.clear_client_cache()

    def _reset_embedder() -> None:
        from clipwright.rag.embedder import reset_embedder

        reset_embedder()

    def _reset_vision_service() -> None:
        # api/vision.py 持有模块级 VisionService 单例（模型在实例内缓存）
        import clipwright.api.vision as vision_api

        reset = getattr(vision_api, "reset_vision_service", None)
        if callable(reset):
            reset()

    def _reset_render_caches() -> None:
        from clipwright.services.render import reset_render_caches

        reset_render_caches()

    register(("llm_api_key", "llm_model", "llm_base_url", "llm_instructions",
              "llm_flash_model", "llm_flash_api_key", "llm_flash_base_url",
              "vision_llm_provider", "vision_llm_model", "vision_llm_api_key",
              "vision_llm_base_url"),
             _clear_llm_clients)
    register(("rag_embed_provider", "rag_embed_model", "rag_embed_dim",
              "rag_embed_api_key", "rag_embed_base_url", "rag_embed_batch_size"),
             _reset_embedder)
    register(("vision_provider", "vision_model", "vision_top_k", "vision_device"),
             _reset_vision_service)
    register(("render_encoder",), _reset_render_caches)


def apply_hooks(changed_keys: Iterable[str]) -> list[str]:
    """对变更键执行匹配的失效钩子；返回实际触发的钩子名列表（去重）。"""
    _builtin_hooks()
    fired: list[str] = []
    seen: set[int] = set()
    for key in changed_keys:
        for prefix, fns in _HOOKS.items():
            if not key.startswith(prefix):
                continue
            for fn in fns:
                if id(fn) in seen:
                    continue
                seen.add(id(fn))
                try:
                    fn()
                    fired.append(f"{prefix}({fn.__name__})")
                except Exception:
                    # 失效钩子失败不应阻断配置保存本身，但必须留痕
                    logger.exception("配置变更钩子执行失败 (key=%s)", key)
    return fired
