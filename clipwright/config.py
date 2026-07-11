"""全局配置管理 + 日志配置。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


# 查找 .env：优先包目录，其次工作目录
_pkg_dir = Path(__file__).resolve().parent
_env_file = _pkg_dir / ".env"
if not _env_file.exists():
    _env_file = Path.cwd() / ".env"
_env_file_str = str(_env_file) if _env_file.exists() else ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CLIPWRIGHT_",
        env_file=_env_file_str,
        env_file_encoding="utf-8",
    )

    # --- 路径 ---
    persona_dir: Path = Path("personas")
    plugin_dir: Path = Path("plugins")

    # --- IsoBase / LLM ---
    llm_provider: Literal["openai", "anthropic", "ollama"] = "anthropic"
    llm_api_key: str = ""
    # 模型名直接透传给 IsoBase → SDK：
    #   anthropic → Anthropic Messages API 模型名 (claude-sonnet-4-6, claude-opus-4-6, ...)
    #   openai    → OpenAI Chat Completions API 模型名 (gpt-4o, gpt-5.4-mini, ...)
    #   ollama    → Ollama 本地模型名 (llama3, qwen2, ...)
    llm_model: str = "claude-sonnet-4-6"
    # OpenAI 兼容 API 的 base_url（Ollama: http://localhost:11434/v1, vLLM/Together: ...）
    llm_base_url: Optional[str] = None
    llm_instructions: str = "You are ClipWright, an AI video content orchestration engine."

    # --- 服务 ---
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False

    # --- 渲染 ---
    render_output_dir: Path = Path("renders")

    # --- 视觉识别模型 ---
    vision_provider: Literal["llm", "transformers", "none"] = "transformers"
    vision_model: str = "google/vit-base-patch16-224"
    vision_top_k: int = 5
    vision_device: str = "cpu"
    # 视觉 LLM 独立配置（不配置时复用主 LLM 参数）
    vision_llm_provider: Optional[str] = None
    vision_llm_model: Optional[str] = None
    vision_llm_api_key: Optional[str] = None
    vision_llm_base_url: Optional[str] = None

    # --- 素材库 ---
    library_dir: Path = Path("library")

    # --- RAG / Embedding ---
    rag_embed_provider: Literal["sentence_transformer", "openai", "ollama"] = "sentence_transformer"
    rag_embed_model: str = "BAAI/bge-small-zh-v1.5"
    rag_embed_dim: int = 512
    rag_embed_api_key: Optional[str] = None
    rag_embed_base_url: Optional[str] = None
    rag_rerank_model: str = "BAAI/bge-reranker-v2-m3"
    rag_rerank_base_url: Optional[str] = None
    rag_rerank_api_key: Optional[str] = None
    rag_top_k: int = 5
    rag_rerank_top_k: int = 20
    rag_chunk_size: int = 512
    rag_chunk_overlap: int = 64
    rag_embed_batch_size: int = 20


settings = Settings()


# ── 日志配置 ─────────────────────────────────────────

def setup_logging() -> logging.Logger:
    """配置并返回全局 logger。"""
    logger = logging.getLogger("clipwright")
    logger.setLevel(logging.DEBUG if settings.debug else logging.INFO)

    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setLevel(logging.DEBUG)
        fmt = logging.Formatter(
            "[%(asctime)s] %(levelname)-7s %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
        handler.setFormatter(fmt)
        logger.addHandler(handler)

    return logger


logger = setup_logging()
