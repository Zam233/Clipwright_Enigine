"""全局配置管理 + 日志配置。"""

from __future__ import annotations

import logging
from datetime import timezone, timedelta
from pathlib import Path
from typing import Literal, Optional

from pydantic import AliasChoices, Field
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

    # --- 插件治理（M1/M2/M8/M15）---
    # manifest 签名密钥；空则从 account_jwt_secret 派生；再为空则不启用签名强制
    plugin_signature_key: str = ""
    # True 时未签名插件拒绝加载（市场开放前最低安全线）
    plugin_require_signature: bool = False
    # 已知权限白名单（M1 权限声明校验；插件声明之外的权限将被拒绝）。
    # orchestrate（SA-2）：允许插件注册自定义 Agent 参与主管线编排/被子代理调用
    # P1-14: 默认最小集——shell 从默认白名单移除（等于全放行让权限声明
    # 沦为装饰）；需要 shell 的部署显式配置 CLIPWRIGHT_PLUGIN_ALLOWED_PERMISSIONS
    plugin_allowed_permissions: list[str] = Field(
        default_factory=lambda: ["network", "fs_read", "fs_write", "http", "noop", "orchestrate"],
    )

    # B-3：渲染后端选择（local=本机 ffmpeg；remote=远程渲染服务）。
    # 缺省 local 保持既有行为；注册表见 clipwright/providers/render/。
    renderer_backend: str = Field(default="local", description="渲染后端：local / remote")

    # M8：受信任 UI bundle 档（默认关闭）。开启后仍需插件 manifest 带签名
    # 才提供 /api/plugin/{id}/bundle（fail-closed：无签名一律 403）。
    plugin_allow_ui_bundle: bool = Field(
        default=False, description="允许插件 UI bundle（iframe 沙箱档；需插件签名）",
    )

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
    # --- Flash 模型（轻量快速）---
    # 用于简单任务：意图判断、确认分类、搜索查询改写等无需重型推理的场景。
    # 主 LLM (llm_model) 保留给复杂生成（创意简报、规划书、场景编排）。
    # 各项不配置时复用主 LLM 对应参数；仅 llm_flash_model 单独指定即可启用。
    llm_flash_model: Optional[str] = None
    llm_flash_provider: Optional[str] = None
    llm_flash_api_key: Optional[str] = None
    llm_flash_base_url: Optional[str] = None

    # --- 服务 ---
    host: str = "0.0.0.0"
    port: int = 8080
    debug: bool = False

    # --- 安全红线 ---
    # 显式承认「无鉴权对外开放」才允许绑定非回环地址启动（fail-closed）。
    # 仅用于受控本地/内网环境；生产部署应设置 api_token 或 account_verify_mode。
    allow_insecure: bool = False

    # --- Pipeline 并行度（稳妥有界并发）---
    # 动画阶段逐片段 LLM MG 生成的并发路数；默认 6（LLM 生成是网络等待，并发翻倍时间近半）。
    # 设置 1 即回到串行。
    pipeline_concurrency: int = 6
    # 素材阶段场景级并行路数；默认 6。设置 1 即回到逐场景串行。
    material_concurrency: int = 6
    # 素材帧验证是否启用视觉 LLM；默认 False（只做 ffmpeg 抽帧 is_blank + 本地启发式标题匹配）。
    # 打开后 frame_validator 工具调用 ImageAnalyzer 产出 match_score（视觉 API 成本）。
    enable_visual_llm: bool = False
    # 渲染后复核（切点复核图 + 全片总览图，V4/V7）；默认关闭（每次多一次抽帧 + 可选视觉调用）。
    # 打开后 RenderService 会把复核结论按问题类型派工（redo_agent），并写进 RenderResult。
    render_review: bool = False
    # 复核视觉评分门槛（V4 残余：评分入熔断）。<= 0 表示不启用门槛（默认，
    # 与质检 quality_min_score 同一语义）；高于 0 时评分低于门槛会置 review_failed。
    # 也可用环境变量 CLIPWRIGHT_REVIEW_MIN_SCORE 覆盖。
    render_review_min_score: int = 0

    # --- 素材内容索引（R1：镜头级结构化索引，"Agent 知道自己在剪什么"的底座）---
    # 默认开启：L0 索引只做 ffmpeg 切分 + 抽帧 + 廉价统计，零 ML、零云端调用。
    content_index_enabled: bool = True
    # ffmpeg 场景检测阈值（越小越敏感、镜头越多）；0.3 与 scene_detect 工具默认一致。
    content_index_scene_threshold: float = 0.3
    # 低于该时长的镜头并入邻居（转场/闪帧碎片），避免索引被碎片污染。
    content_index_min_shot_sec: float = 0.3
    # 每镜头抽几帧算统计；>=2 才能算帧间差（运动强度）。
    content_index_keyframes_per_shot: int = 2
    # 单段素材最多保留的镜头数（超长素材兜底）。
    content_index_max_shots: int = 2000
    # 是否把索引镜像到 Mongo（跨项目"我所有素材里找 X"的唯一载体）。
    content_index_mirror_mongo: bool = True
    # 云端多模态对（被选中的）镜头做画面描述；默认关（开启后产生视觉 API 成本）。
    content_index_visual_llm: bool = False
    # 云端多模态每次索引最多调用多少个镜头（预算闸门；0 = 不限制，仍受月预算约束）。
    content_index_llm_max_shots: int = 12
    # 是否用 CLIP 零样本给镜头打标签（开放词表，能回答"找有咖啡杯的镜头"这类问题）。
    content_index_clip_enabled: bool = True
    # CLIP 权重名（HF）；首次使用会下载（约 600MB）。
    content_index_clip_model: str = "openai/clip-vit-base-patch32"
    # 零样本标签词表（JSON：{"中文标签": "english prompt"}）；留空用内置默认词表。
    content_index_clip_vocab: str = ""
    # 镜头标注并发（CPU 推理，过高反而拖慢整机）。
    content_index_label_concurrency: int = 4
    # 音频事件检测（静音/大响段/起始沿，纯 numpy，无模型）——默认开。
    content_index_audio_events: bool = True
    # 自动转写并对齐到镜头；默认关（需要装 whisper/faster-whisper 或注册插件 STT 后端）。
    content_index_asr: bool = False
    content_index_asr_language: str = "zh"
    # 领域事件词表（generic/sports/food/tech_review/knowledge）。
    content_index_event_domain: str = "generic"
    # 追加词表（JSON：{"sports": ["远射"]}）；留空只用内置词表。
    content_index_event_lexicon: str = ""

    # --- 安全 ---
    # API 令牌：设置后所有 /api/* 请求需携带 Authorization: Bearer <token>
    # 留空为开发模式（直接放行，启动时打印警告）
    api_token: str = ""
    # CORS 允许的来源（逗号分隔）；api_token 设置后生效，"*" 表示允许全部
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # --- 账号体系（P3-3B，对接 K:\Clipwright Server）---
    # off  = 现状（api_token 或无鉴权，本地/内网部署）
    # token= 本地 api_token 强制（过渡模式）
    # jwt  = 校验 Server 签发的 JWT（与 CLIPWRIGHT_SERVER_JWT_SECRET 共享密钥本地验签）
    account_verify_mode: Literal["off", "token", "jwt"] = "off"
    account_url: str = ""          # ClipWright Server 地址（前端市场 API / 未来 token 模式 /verify）
    account_jwt_secret: str = ""   # 共享 JWT 密钥（jwt 模式必填，与 Server 一致）
    account_jwt_algorithm: str = "HS256"

    # --- Webhook（P8: secret 落盘加密密钥；缺省时从 account_jwt_secret 派生）---
    webhook_secret_key: str = ""   # Fernet key (base64 urlsafe, 32 bytes)；空则用 jwt secret 派生

    # --- 速率限制（P5-B2，内存滑动窗口；默认关闭）---
    rate_limit_enabled: bool = False
    rate_limit_window_sec: float = 60.0
    rate_limit_max_requests: int = 120

    # --- LLM 成本预算（P5-B3，全局月 token 预算；0=不限）---
    llm_monthly_token_budget: int = 0

    # --- MongoDB ---
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db_name: str = "clipwright"

    # --- 渲染 ---
    render_output_dir: Path = Path("renders")
    max_concurrent_renders: int = 2
    render_encoder: str = ""  # 空 = 运行时智能探测 (h264_nvenc if GPU 可用, else libx264)；也可显式指定 libx264 / h264_nvenc / hevc_nvenc / hevc_amf
    render_preset: str = "medium"    # ultrafast/fast/medium/slow
    render_trim_cache: bool = True
    # 字幕/文字渲染器：ass = libass（.ass 文件 + `-vf ass=`，14 个样式字段全部生效）| drawtext = 旧 drawtext 滤镜（回退）
    caption_renderer: Literal["ass", "drawtext"] = "ass"
    # ffmpeg/ffprobe 可执行文件路径（留空则自动探测 PATH 及常见安装位置，如 WinGet）
    ffmpeg_path: str = ""
    ffprobe_path: str = ""
    # 远程渲染服务地址（留空表示仅本地渲染）
    remote_render_url: str = ""
    # 远程渲染服务鉴权令牌
    remote_render_token: str = ""
    # 远程渲染不可用时是否回退本地渲染
    remote_render_fallback: bool = True
    # 远程渲染轮询间隔（秒）
    remote_render_poll_interval: float = 1.5
    # 远程渲染超时（秒）
    remote_render_timeout: int = 1800
    # 轮70（D7）：远程渲染产物下载上限（MB）——防远端异常产物打爆本地磁盘/内存
    remote_render_max_download_mb: int = 4096
    # 轮70（D7）：轮询连续瞬态失败容忍次数（超过才判定远程不可用）
    remote_render_poll_max_failures: int = 3

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

    # --- Web Search（联网搜索：Bocha 主 / 百度备）---
    # 环境变量名：优先 ENABLE_WEB_SEARCH 等无前缀形式（与计划/F3 测试一致），
    # 也兼容 CLIPWRIGHT_ 前缀（沿用 env_prefix="CLIPWRIGHT_" 的既有约定）。
    enable_web_search: bool = Field(
        default=False,
        validation_alias=AliasChoices("ENABLE_WEB_SEARCH", "CLIPWRIGHT_ENABLE_WEB_SEARCH"),
    )
    web_search_provider: Literal["bocha", "baidu"] = Field(
        default="bocha",
        validation_alias=AliasChoices("WEB_SEARCH_PROVIDER", "CLIPWRIGHT_WEB_SEARCH_PROVIDER"),
    )
    web_search_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("WEB_SEARCH_API_KEY", "CLIPWRIGHT_WEB_SEARCH_API_KEY"),
    )
    web_search_base_url: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("WEB_SEARCH_BASE_URL", "CLIPWRIGHT_WEB_SEARCH_BASE_URL"),
    )
    web_search_timeout: int = Field(
        default=15,
        validation_alias=AliasChoices("WEB_SEARCH_TIMEOUT", "CLIPWRIGHT_WEB_SEARCH_TIMEOUT"),
    )
    web_search_max_results: int = Field(
        default=5,
        validation_alias=AliasChoices("WEB_SEARCH_MAX_RESULTS", "CLIPWRIGHT_WEB_SEARCH_MAX_RESULTS"),
    )

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

    # --- 结构 Agent ---
    # voiceover 双稿择优（双倍最大 LLM 调用，择优启发式只比较场景数）。
    # 默认关闭；需要时置 true 开启。
    structure_double_draft: bool = False

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
