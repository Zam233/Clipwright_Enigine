"""全局配置管理 + 日志配置。"""

from __future__ import annotations

import logging
from datetime import timezone, timedelta
from pathlib import Path
from typing import Literal, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

# ── 时区 ──
TIME_ZONE = timezone(timedelta(hours=8), "Asia/Shanghai")
MONGO_TRANSACTIONS_ENABLED = False


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
    plugin_data_dir: Path = Path("PluginData")
    project_dir: Path = Path("projects")

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

    # --- MongoDB ---
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db_name: str = "clipwright"

    # --- 渲染 ---
    render_output_dir: Path = Path("renders")
    max_concurrent_renders: int = 2
    render_encoder: str = "libx264"  # libx264 / h264_nvenc / hevc_nvenc / hevc_amf
    render_preset: str = "medium"    # ultrafast/fast/medium/slow
    render_trim_cache: bool = True

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

    # --- TTS / 声音克隆 ---
    # 阿里云百炼（DashScope）凭据，用于声音克隆与语音合成
    tts_dashscope_api_key: str = ""
    tts_workspace_id: str = ""
    # 默认音色模型 provider：qwen-tts | cosyvoice | minimax
    tts_default_provider: Literal["qwen-tts", "cosyvoice", "minimax"] = "qwen-tts"
    # 各 provider 默认目标模型
    tts_qwen_model: str = "qwen3-tts-vc-2026-01-22"
    tts_cosyvoice_model: str = "cosyvoice-v3.5-plus"
    tts_minimax_model: str = "MiniMax/speech-2.8-turbo"
    # 音色元数据 / 合成音频 / 克隆样本 存储路径
    tts_voice_db: Path = Path("PluginData/voices/voices.json")
    tts_output_dir: Path = Path("PluginData/voices/audio")
    tts_upload_dir: Path = Path("PluginData/voices/uploads")
    # 公网上传服务（逗号分隔，按顺序优先级；内置 uguu / catbox）。
    # 仅 CosyVoice / MiniMax 克隆需要公网音频 URL；Qwen-TTS 用 base64 不受影响。
    tts_public_upload_services: str = "uguu,catbox"


settings = Settings()


# ── 日志配置 ─────────────────────────────────────────

_LOG_FORMAT = "[%(asctime)s] %(levelname)-7s %(name)s: %(message)s"


def setup_logging() -> logging.Logger:
    """配置全局日志：所有 logger 都输出到终端。"""
    level = logging.DEBUG if settings.debug else logging.INFO

    # 配置根 logger（影响所有子 logger）
    root = logging.getLogger()
    root.setLevel(level)

    # 清除已有 handler，避免重复
    for h in root.handlers[:]:
        root.removeHandler(h)

    handler = logging.StreamHandler()
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt="%H:%M:%S"))
    root.addHandler(handler)

    # Clipwright 自家 logger
    logger = logging.getLogger("clipwright")
    logger.setLevel(level)
    logger.propagate = True  # 确保传播到根 logger

    # 压制第三方库的 DEBUG 日志
    for noisy in ("httpx", "httpcore", "chromadb", "urllib3", "PIL", "matplotlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    return logger


logger = setup_logging()
