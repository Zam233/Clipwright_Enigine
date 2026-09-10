"""Settings 字段元数据 — 「后端配置」设置页的单一事实来源。

类型/默认值/枚举选项从 ``Settings.model_fields`` 派生（防漂移）；
本模块只补充 UI 展示所需的 group/label/secret/hot 信息。

不变量：Settings 的每个字段都必须有元数据（tests/clipwright/test_settings_api.py
有覆盖度检查）——新增 Settings 字段而忘记配元数据会直接红灯。

hot 语义（依据各配置的读取点核实，见 docs/development.md「运行时配置」）：
- hot=True  每请求/每调用实时读取，改后立即生效；
- hot=False 仅在启动/构造时读取一次（CORS 中间件、Mongo、uvicorn、
  线程池、路径快照等），改后需重启才生效。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, get_args, get_origin

from pydantic.fields import FieldInfo

from clipwright.config import Settings


@dataclass(frozen=True)
class FieldMeta:
    """单个 Settings 字段的 UI 元数据。"""

    key: str
    group: str
    label: str
    secret: bool = False
    hot: bool = True
    description: str = ""
    kind: str = "string"  # string|int|float|bool|enum|list|path
    choices: tuple[str, ...] = ()


GROUPS: list[dict[str, str]] = [
    {"id": "llm", "label": "模型与生成"},
    {"id": "vision", "label": "视觉识别"},
    {"id": "rag", "label": "检索与 RAG"},
    {"id": "tts", "label": "语音与 TTS"},
    {"id": "render", "label": "渲染与编码"},
    {"id": "remote_render", "label": "远程渲染"},
    {"id": "pipeline", "label": "管线与素材"},
    {"id": "search", "label": "联网搜索"},
    {"id": "security", "label": "安全与鉴权"},
    {"id": "plugins", "label": "插件治理"},
    {"id": "storage", "label": "存储与路径"},
    {"id": "service", "label": "服务与预算"},
]

# (key, group, label, secret, hot, description)
_ROWS: list[tuple[str, str, str, bool, bool, str]] = [
    # ── 模型与生成 ──
    ("llm_provider", "llm", "LLM Provider", False, False, "openai / anthropic / ollama；进程内单例已快照，需重启"),
    ("llm_api_key", "llm", "LLM API Key", True, True, "主模型密钥"),
    ("llm_model", "llm", "主模型", False, True, "复杂生成（简报/规划/场景编排）用模型名"),
    ("llm_base_url", "llm", "LLM Base URL", False, True, "OpenAI 兼容 API 地址（Ollama/vLLM 等）"),
    ("llm_instructions", "llm", "系统指令", False, True, "注入所有 LLM 会话的系统级指令"),
    ("llm_flash_model", "llm", "Flash 模型", False, True, "轻量任务（意图判断/查询改写）用模型"),
    ("llm_flash_provider", "llm", "Flash Provider", False, False, "缺省复用主 LLM provider；需重启"),
    ("llm_flash_api_key", "llm", "Flash API Key", True, True, "缺省复用主 LLM 密钥"),
    ("llm_flash_base_url", "llm", "Flash Base URL", False, True, "缺省复用主 LLM 地址"),
    # ── 视觉识别 ──
    ("vision_provider", "vision", "视觉 Provider", False, True, "llm / transformers / none"),
    ("vision_model", "vision", "视觉模型", False, True, "transformers 分类模型名"),
    ("vision_top_k", "vision", "视觉 Top-K", False, True, "分类返回的标签数"),
    ("vision_device", "vision", "视觉设备", False, True, "cpu / cuda"),
    ("vision_llm_provider", "vision", "视觉 LLM Provider", False, True, "缺省复用主 LLM"),
    ("vision_llm_model", "vision", "视觉 LLM 模型", False, True, "缺省复用主 LLM"),
    ("vision_llm_api_key", "vision", "视觉 LLM Key", True, True, "缺省复用主 LLM 密钥"),
    ("vision_llm_base_url", "vision", "视觉 LLM Base URL", False, True, "缺省复用主 LLM 地址"),
    # ── 检索与 RAG ──
    ("rag_embed_provider", "rag", "Embedding Provider", False, True, "sentence_transformer / openai / ollama"),
    ("rag_embed_model", "rag", "Embedding 模型", False, True, "向量模型名"),
    ("rag_embed_dim", "rag", "Embedding 维度", False, True, "向量维度（需与模型一致）"),
    ("rag_embed_api_key", "rag", "Embedding Key", True, True, "云端嵌入密钥"),
    ("rag_embed_base_url", "rag", "Embedding Base URL", False, True, "云端嵌入地址"),
    ("rag_rerank_model", "rag", "Rerank 模型", False, True, "重排模型名"),
    ("rag_rerank_base_url", "rag", "Rerank Base URL", False, True, "重排服务地址"),
    ("rag_rerank_api_key", "rag", "Rerank Key", True, True, "重排服务密钥"),
    ("rag_top_k", "rag", "召回 Top-K", False, True, "RAG 召回条数"),
    ("rag_rerank_top_k", "rag", "Rerank Top-K", False, True, "重排候选条数"),
    ("rag_chunk_size", "rag", "分块大小", False, True, "知识库分块字符数"),
    ("rag_chunk_overlap", "rag", "分块重叠", False, True, "相邻分块重叠字符数"),
    ("rag_embed_batch_size", "rag", "嵌入批大小", False, True, "批量嵌入每批条数"),
    # ── 语音与 TTS ──
    ("tts_dashscope_api_key", "tts", "DashScope Key", True, True, "阿里云百炼凭据（克隆/合成）"),
    ("tts_workspace_id", "tts", "DashScope 工作空间", False, True, "百炼 workspace id"),
    ("tts_default_provider", "tts", "默认 TTS Provider", False, True, "qwen-tts / cosyvoice / minimax"),
    ("tts_qwen_model", "tts", "Qwen-TTS 模型", False, True, ""),
    ("tts_cosyvoice_model", "tts", "CosyVoice 模型", False, True, ""),
    ("tts_minimax_model", "tts", "MiniMax 模型", False, True, ""),
    ("tts_voice_db", "tts", "音色库路径", False, False, "音色元数据 JSON；静态挂载，需重启"),
    ("tts_output_dir", "tts", "合成音频目录", False, False, "静态挂载目录，改后需重启"),
    ("tts_upload_dir", "tts", "克隆样本目录", False, False, ""),
    ("tts_public_upload_services", "tts", "公网上传服务", False, True, "逗号分隔；CosyVoice/MiniMax 克隆需要"),
    # ── 渲染与编码 ──
    ("max_concurrent_renders", "render", "最大并发渲染", False, False, "线程池在导入时固定，需重启"),
    ("renderer_backend", "render", "渲染后端", False, True, "local = 本机 ffmpeg；remote = 远程渲染服务（B-3）"),
    ("render_encoder", "render", "编码器", False, True, "空 = 自动探测（GPU 优先）"),
    ("render_preset", "render", "编码预设", False, True, "ultrafast / fast / medium / slow"),
    ("render_trim_cache", "render", "裁剪缓存", False, True, "（当前版本未接线，预留）"),
    ("caption_renderer", "render", "字幕渲染器", False, True, "ass = libass 全样式；drawtext = 回退"),
    ("ffmpeg_path", "render", "ffmpeg 路径", False, True, "留空自动探测"),
    ("ffprobe_path", "render", "ffprobe 路径", False, True, "留空自动探测"),
    ("render_output_dir", "render", "成片输出目录", False, False, "静态挂载目录，改后需重启"),
    # ── 远程渲染 ──
    ("remote_render_url", "remote_render", "远程渲染地址", False, True, "留空 = 仅本地渲染"),
    ("remote_render_token", "remote_render", "远程渲染令牌", True, True, ""),
    ("remote_render_fallback", "remote_render", "远程失败回退本地", False, True, ""),
    ("remote_render_poll_interval", "remote_render", "轮询间隔(秒)", False, True, ""),
    ("remote_render_timeout", "remote_render", "远程超时(秒)", False, True, ""),
    ("remote_render_max_download_mb", "remote_render", "产物下载上限(MB)", False, True, "防异常产物打爆磁盘"),
    ("remote_render_poll_max_failures", "remote_render", "轮询失败容忍次数", False, True, ""),
    # ── 管线与素材 ──
    ("pipeline_concurrency", "pipeline", "动画生成并发", False, True, "MG 逐片段 LLM 并发路数"),
    ("material_concurrency", "pipeline", "素材检索并发", False, True, "场景级并行路数"),
    ("enable_visual_llm", "pipeline", "视觉 LLM 素材校验", False, True, "开启后产生视觉 API 成本"),
    ("structure_double_draft", "pipeline", "结构双稿择优", False, True, "双倍 LLM 调用，择优启发式仅比场景数"),
    ("library_dir", "pipeline", "全局素材库目录", False, True, "无 project_id 上传的落点"),
    # ── 联网搜索 ──
    ("enable_web_search", "search", "启用联网搜索", False, True, "兼容无前缀 ENABLE_WEB_SEARCH"),
    ("web_search_provider", "search", "搜索 Provider", False, True, "bocha / baidu"),
    ("web_search_api_key", "search", "搜索 API Key", True, True, ""),
    ("web_search_base_url", "search", "搜索 Base URL", False, True, ""),
    ("web_search_timeout", "search", "搜索超时(秒)", False, True, ""),
    ("web_search_max_results", "search", "搜索结果数", False, True, ""),
    # ── 安全与鉴权 ──
    ("api_token", "security", "API 令牌", True, True, "设置后所有 /api/* 需 Bearer 认证"),
    ("allow_insecure", "security", "允许无鉴权对外开放", False, False, "绑定非回环地址且无鉴权时放行启动（自认风险）；仅启动时读取"),
    ("cors_origins", "security", "CORS 允许来源", False, False, "逗号分隔；中间件启动时注册，需重启"),
    ("account_verify_mode", "security", "账号校验模式", False, True, "off / token / jwt"),
    ("account_url", "security", "账号服务地址", False, True, "ClipWright Server（8090）"),
    ("account_jwt_secret", "security", "JWT 共享密钥", True, True, "jwt 模式必填，与 Server 一致"),
    ("account_jwt_algorithm", "security", "JWT 算法", False, True, "默认 HS256"),
    ("webhook_secret_key", "security", "Webhook 加密密钥", True, False, "Fernet key；加密器已缓存，需重启"),
    ("rate_limit_enabled", "security", "启用限流", False, True, "进程内滑动窗口"),
    ("rate_limit_window_sec", "security", "限流窗口(秒)", False, True, ""),
    ("rate_limit_max_requests", "security", "窗口内最大请求数", False, True, ""),
    # ── 插件治理 ──
    ("plugin_signature_key", "plugins", "插件签名密钥", True, False, "加载器按次读取；已加载插件需重载"),
    ("plugin_require_signature", "plugins", "强制签名校验", False, False, "对已加载插件不追溯，需重载/重启"),
    ("plugin_allowed_permissions", "plugins", "允许的插件权限", False, True, "逗号分隔白名单"),
    # ── 存储与路径 ──
    ("persona_dir", "storage", "Persona 目录", False, False, "仓库启动时快照，需重启"),
    ("plugin_dir", "storage", "插件目录", False, False, "加载器启动时构造，需重启"),
    ("plugin_data_dir", "storage", "插件数据目录", False, False, "需重启"),
    ("project_dir", "storage", "项目目录", False, False, "管理器导入时固化，需重启"),
    # ── 服务与预算 ──
    ("host", "service", "监听地址", False, False, "uvicorn 启动参数，需重启"),
    ("port", "service", "监听端口", False, False, "uvicorn 启动参数，需重启"),
    ("debug", "service", "调试模式", False, False, "日志级别在导入时配置，需重启"),
    ("mongo_uri", "service", "MongoDB URI", True, False, "可能含凭据；连接在启动时建立，需重启"),
    ("mongo_db_name", "service", "MongoDB 库名", False, False, "需重启"),
    ("llm_monthly_token_budget", "service", "LLM 月度预算", False, True, "全局 token 预算；0 = 不限"),
]


def _derive_kind(annotation: Any) -> tuple[str, tuple[str, ...]]:
    """从类型注解推导控件类型与枚举选项。"""
    origin = get_origin(annotation)
    if origin is Literal:
        return "enum", tuple(str(a) for a in get_args(annotation))
    if origin is None and annotation is bool:
        return "bool", ()
    if annotation is int:
        return "int", ()
    if annotation is float:
        return "float", ()
    if annotation is list or origin is list:
        return "list", ()
    args = get_args(annotation)
    if args:  # Optional[...] / Union[...]：取非 None 的那个
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return _derive_kind(non_none[0])
    try:
        if issubclass(annotation, str):
            return "string", ()
    except TypeError:
        pass
    return "string", ()


def _build_meta() -> dict[str, FieldMeta]:
    fields: dict[str, FieldInfo] = Settings.model_fields
    out: dict[str, FieldMeta] = {}
    for key, group, label, secret, hot, desc in _ROWS:
        info = fields.get(key)
        if info is None:
            # 元数据与 Settings 漂移：宁可启动期暴露（覆盖度测试兜底）也不静默吞
            raise KeyError(f"config_schema 引用了不存在的 Settings 字段: {key}")
        kind, choices = _derive_kind(info.annotation)
        out[key] = FieldMeta(
            key=key, group=group, label=label, secret=secret, hot=hot,
            description=desc, kind=kind, choices=choices,
        )
    return out


FIELD_META: dict[str, FieldMeta] = _build_meta()

# Settings 中存在但尚未纳入 UI 的字段（应为空；覆盖度测试断言）
MISSING_META: list[str] = sorted(set(Settings.model_fields) - set(FIELD_META))
