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
    {"id": "content_index", "label": "素材内容索引"},
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

#: C1：提醒用户「角色绑定优先于本页平铺字段」——两处都能配同一件事，
#: 但生效顺序是「模型提供商」的角色绑定 > 本页（settings）> 默认值。
_BINDING_NOTE = "（在「模型提供商」绑定该角色后以绑定为准）"

# (key, group, label, secret, hot, description)
_ROWS: list[tuple[str, str, str, bool, bool, str]] = [
    # ── 模型与生成 ──
    ("llm_provider", "llm", "LLM Provider", False, False, f"openai / anthropic / ollama；{_BINDING_NOTE}；需重启"),
    ("llm_api_key", "llm", "LLM API Key", True, True, f"主模型密钥{_BINDING_NOTE}"),
    ("llm_model", "llm", "主模型", False, True, f"复杂生成（简报/规划/场景编排）用模型名{_BINDING_NOTE}"),
    ("llm_base_url", "llm", "LLM Base URL", False, True, f"OpenAI 兼容 API 地址（Ollama/vLLM 等）{_BINDING_NOTE}"),
    ("llm_instructions", "llm", "系统指令", False, True, "注入所有 LLM 会话的系统级指令"),
    ("llm_flash_model", "llm", "Flash 模型", False, True, f"轻量任务（意图判断/查询改写）用模型{_BINDING_NOTE}"),
    ("llm_flash_provider", "llm", "Flash Provider", False, False, f"缺省复用主 LLM provider；{_BINDING_NOTE}；需重启"),
    ("llm_flash_api_key", "llm", "Flash API Key", True, True, f"缺省复用主 LLM 密钥{_BINDING_NOTE}"),
    ("llm_flash_base_url", "llm", "Flash Base URL", False, True, f"缺省复用主 LLM 地址{_BINDING_NOTE}"),
    # ── 视觉识别 ──
    ("vision_provider", "vision", "视觉 Provider", False, True, "llm / transformers / none"),
    ("vision_model", "vision", "视觉模型", False, True, "transformers 分类模型名"),
    ("vision_top_k", "vision", "视觉 Top-K", False, True, "分类返回的标签数"),
    ("vision_device", "vision", "视觉设备", False, True, "cpu / cuda"),
    ("vision_llm_provider", "vision", "视觉 LLM Provider", False, True, "缺省复用主 LLM"),
    ("vision_llm_model", "vision", "视觉 LLM 模型", False, True, f"缺省复用主 LLM{_BINDING_NOTE}"),
    ("vision_llm_api_key", "vision", "视觉 LLM Key", True, True, f"缺省复用主 LLM 密钥{_BINDING_NOTE}"),
    ("vision_llm_base_url", "vision", "视觉 LLM Base URL", False, True, f"缺省复用主 LLM 地址{_BINDING_NOTE}"),
    # ── 素材内容索引（R1） ──
    ("content_index_enabled", "content_index", "启用内容索引", False, True,
     "L0 索引：ffmpeg 切分 + 抽帧 + 廉价统计，零云端调用"),
    ("content_index_scene_threshold", "content_index", "场景切分阈值", False, True,
     "越小越敏感、镜头越多（与 scene_detect 工具同口径）"),
    ("content_index_min_shot_sec", "content_index", "最短镜头时长", False, True,
     "低于该时长的镜头并入邻居，避免转场碎片污染索引"),
    ("content_index_keyframes_per_shot", "content_index", "每镜头抽帧数", False, True,
     ">=2 才能算帧间差（运动强度）"),
    ("content_index_max_shots", "content_index", "单素材最大镜头数", False, True,
     "超长素材兜底上限"),
    ("content_index_mirror_mongo", "content_index", "镜像到 Mongo", False, True,
     "开启后支持跨项目「我所有素材里找 X」"),
    ("content_index_visual_llm", "content_index", "云端多模态描述", False, True,
     "开启后对镜头调用视觉 LLM 生成描述（产生视觉 API 成本）"),
    ("content_index_llm_max_shots", "content_index", "云调用上限（镜头数）", False, True,
     "每次索引最多送多少个镜头给云端多模态；0=不限（仍受月预算约束）"),
    ("content_index_clip_enabled", "content_index", "启用 CLIP 标签", False, True,
     "零样本开放词表标签，能回答「找有咖啡杯的镜头」这类问题"),
    ("content_index_clip_model", "content_index", "CLIP 模型", False, True,
     "HF 权重名；首次使用会下载（约 600MB）"),
    ("content_index_clip_model_path", "content_index", "CLIP 本地目录", False, True,
     "填了只从本地加载、绝不联网（离线/内网环境用）；留空按 HF 缓存"),
    ("content_index_clip_vocab", "content_index", "CLIP 词表（JSON）", False, True,
     '形如 {"咖啡杯": "a coffee cup"}；留空用内置默认词表'),
    ("content_index_label_concurrency", "content_index", "标注并发", False, True,
     "CPU 推理并发；过高反而拖慢整机"),
    ("content_index_audio_events", "content_index", "音频事件检测", False, True,
     "静音/大响段/起始沿（纯 numpy，无模型，成本近零）"),
    ("content_index_asr", "content_index", "自动转写对齐", False, True,
     "需要 whisper / faster-whisper 或插件 STT 后端；未安装时自动跳过"),
    ("content_index_asr_language", "content_index", "转写语言", False, True, "zh / en / auto"),
    ("content_index_event_domain", "content_index", "领域事件词表", False, True,
     "generic / sports / food / tech_review / knowledge"),
    ("content_index_event_lexicon", "content_index", "追加事件词表（JSON）", False, True,
     '形如 {"sports": ["远射"]}；留空只用内置词表'),
    ("content_index_blurry_sharpness", "content_index", "模糊判定阈值", False, True,
     "Laplacian 方差上界；经验值，需在真实素材上标定"),
    ("content_index_visual_vectors", "content_index", "画面语义向量（R9）", False, True,
     "给每个镜头算 CLIP 视觉向量存进索引；关掉后语义检索退化为纯文本检索"),
    ("content_index_gradual_detect", "content_index", "渐变转场补检", False, True,
     "补检交叉溶解/淡入淡出（scene 滤镜测不到）；实测 2/2 检出但有 1 处涌浪误报"),
    # ── 检索与 RAG ──
    ("rag_embed_provider", "rag", "Embedding Provider", False, True, f"sentence_transformer / openai / ollama{_BINDING_NOTE}"),
    ("rag_embed_model", "rag", "Embedding 模型", False, True, f"向量模型名{_BINDING_NOTE}"),
    ("rag_embed_dim", "rag", "Embedding 维度", False, True, "向量维度（需与模型一致）"),
    ("rag_embed_api_key", "rag", "Embedding Key", True, True, f"云端嵌入密钥{_BINDING_NOTE}"),
    ("rag_embed_base_url", "rag", "Embedding Base URL", False, True, f"云端嵌入地址{_BINDING_NOTE}"),
    ("rag_rerank_model", "rag", "Rerank 模型", False, True, "重排模型名"),
    ("rag_rerank_base_url", "rag", "Rerank Base URL", False, True, "重排服务地址"),
    ("rag_rerank_api_key", "rag", "Rerank Key", True, True, "重排服务密钥"),
    ("rag_top_k", "rag", "召回 Top-K", False, True, "RAG 召回条数"),
    ("rag_rerank_top_k", "rag", "Rerank Top-K", False, True, "重排候选条数"),
    ("rag_chunk_size", "rag", "分块大小", False, True, "知识库分块字符数"),
    ("rag_chunk_overlap", "rag", "分块重叠", False, True, "相邻分块重叠字符数"),
    ("rag_embed_batch_size", "rag", "嵌入批大小", False, True, "批量嵌入每批条数"),
    # ── 语音与 TTS ──
    ("tts_dashscope_api_key", "tts", "DashScope Key", True, True, f"阿里云百炼凭据（克隆/合成）{_BINDING_NOTE}"),
    ("tts_workspace_id", "tts", "DashScope 工作空间", False, True, "百炼 workspace id"),
    ("tts_default_provider", "tts", "默认 TTS Provider", False, True, f"qwen-tts / cosyvoice / minimax{_BINDING_NOTE}"),
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
    # ── 动画 LLM 化（M3）──
    ("animation_llm_generation", "pipeline", "动画按类型 LLM 生成", False, True,
     "开：逻辑动画按「类型提示词 + 视频类型 + persona」由 LLM 生成；关闭退回内置模板/图解 SVG"),
    ("animation_llm_concurrency", "pipeline", "动画生成并发", False, True,
     "类型化 LLM 生成的并发上限（等待型，默认与管线并发一致）"),
    ("persona_prompt_max_chars", "pipeline", "persona 提示词上限", False, True,
     "统一截断上限（旧实现 400/600/1500/2000 四处不一致）；超长会写 trace"),
    ("animation_allow_scrim", "pipeline", "动画允许半透明底板", False, True,
     "开启后保留 alpha≤阈值的底板，避免文字直压实拍导致浅背景不可读"),
    ("animation_scrim_max_alpha", "pipeline", "底板最大透明度", False, True,
     "仅保留 alpha 不超过该值的半透明底板（默认 0.6）"),
    ("animation_render_scrim", "pipeline", "渲染期底板兜底", False, True,
     "开：按背景亮度给动画文字自动烘焙半透明底板；关：完全依赖生成期投影"),
    ("animation_scrim_alpha", "pipeline", "底板强度", False, True,
     "底板 alpha（0.75 约把浅背景压到 0.38 亮度）"),
    ("animation_scrim_grow", "pipeline", "底板外扩(px)", False, True,
     "文字包围盒向外扩展的像素，决定底板大小"),
    ("animation_scrim_blur", "pipeline", "底板边缘模糊", False, True, "柔化底板边缘"),
    ("animation_scrim_light_threshold", "pipeline", "底板亮度阈值", False, True,
     "背景平均亮度超过该值才加底板（0-255，默认 140）"),
    ("animation_motion_min_shift_ratio", "pipeline", "主运动位移门槛", False, True,
     "主运动位移占画布宽比例下限（0.04 ≈ 77px@1920），低于则触发定向修复"),
    ("animation_motion_min_coverage", "pipeline", "动效覆盖门槛", False, True,
     "关键帧覆盖片段时长比例下限（0.45），低于则视为只在入场动"),
    ("animation_motion_check", "pipeline", "渲染后动效自检", False, True,
     "解码动画 MOV 抽帧算多样性，不合格写 warning（不阻断渲染）"),
    ("animation_motion_check_fps", "pipeline", "自检抽样帧率", False, True,
     "抽帧帧率（默认 4fps，越高越准也越慢）"),
    ("animation_motion_check_min_frames", "pipeline", "自检最少不同帧", False, True,
     "不同帧数下限（默认 8）"),
    ("animation_motion_check_min_ratio", "pipeline", "自检多样性比例", False, True,
     "不同帧占比下限（默认 0.25）"),
    ("enable_visual_llm", "pipeline", "视觉 LLM 素材校验", False, True, "开启后产生视觉 API 成本"),
    ("render_review", "render", "渲染后复核", False, True, "切点复核图 + 全片总览图；多一次抽帧，开启视觉时另计费"),
    ("render_review_min_score", "render", "复核视觉评分门槛", False, True, "0=不启用；低于门槛只置 review_failed，不改渲染成败"),
    ("edit_enable_color_match", "pipeline", "跨镜头色彩统一", False, True, "按平均亮度校准片段亮度（默认关；请求 extra_params 可覆盖）"),
    ("edit_enable_pv_llm_tools", "pipeline", "PV 工具自检", False, True, "PV 引擎联动时允许 LLM 用工具做一轮自检（默认关，额外 LLM 成本）"),
    ("agent_stage_min_timeout_sec", "pipeline", "阶段墙钟下限(秒)", False, True, "主 Agent 自填的阶段超时不得低于此值；0=不设下限"),
    ("agent_degradation_policy", "pipeline", "降级处置策略", False, True, "require_receipt=无 remediated/accepted 收据即判失败；warn=只记录"),
    ("text_animation_llm", "pipeline", "文字动画 LLM 生成", False, True, "关闭后回退 AnimationCatalog 固定关键帧表"),
    ("structure_double_draft", "pipeline", "结构双稿择优", False, True, "双倍 LLM 调用，择优启发式仅比场景数"),
    ("library_dir", "pipeline", "全局素材库目录", False, True, "无 project_id 上传的落点"),
    # ── 联网搜索 ──
    ("enable_web_search", "search", "启用联网搜索", False, True, "兼容无前缀 ENABLE_WEB_SEARCH"),
    ("web_search_provider", "search", "搜索 Provider", False, True,
     "deepseek（官方 Search/Fetch，默认）/ bocha / baidu / tavily / serper / bing / local（本地抓取，无需密钥）"),
    ("web_search_deepseek_api_key", "search", "DeepSeek 搜索/Fetch Key", True, True,
     "留空则复用主 LLM 的 DeepSeek key（llm_base_url 指向 api.deepseek.com 时）；"
     "环境变量别名：DEEPSEEK_API_KEY / WEB_SEARCH_DEEPSEEK_API_KEY"),
    ("web_search_api_key", "search", "搜索 API Key", True, True, "主 provider 的 key；专属 key 缺失时回落到它"),
    ("web_search_tavily_api_key", "search", "Tavily API Key", True, True, ""),
    ("web_search_serper_api_key", "search", "Serper API Key", True, True, ""),
    ("web_search_bing_api_key", "search", "Bing API Key", True, True, ""),
    ("web_search_allow_local", "search", "允许本地抓取搜索", False, True,
     "无需密钥：抓 DuckDuckGo/Bing 结果页；关闭后必须配付费 key 才能联网"),
    ("web_assets_ingest", "search", "联网素材落盘", False, True, "把 web 搜索到的图片/视频存进 materials/web/ 并记录来源许可"),
    ("web_assets_allowed_domains", "search", "素材域名白名单", False, True,
     "逗号分隔；非空时只允许这些域名（子域按后缀匹配）"),
    ("web_assets_denied_domains", "search", "素材域名黑名单", False, True,
     "逗号分隔；永远优先于白名单"),
    ("web_assets_respect_robots", "search", "遵守 robots.txt", False, True,
     "关闭后不再检查 robots；被拒时会在降级里看到原因"),
    ("web_assets_cache_ttl_hours", "search", "素材缓存(小时)", False, True,
     "同一 URL 在 TTL 内复用已落盘文件，0 = 关闭缓存"),
    ("agent_tool_dedupe", "pipeline", "重复查询护栏", False, True,
     "同参数的只读查询直接复用上次结果，避免模型反复摸底烧光回合"),
    ("agent_kernel_recovery", "pipeline", "Kernel 确定性补派", False, True,
     "模型提前收尾时由运行时补派缺失阶段（计 usage、进事件流）"),
    ("agent_stage_min_timeout_sec", "pipeline", "阶段最小超时(秒)", False, True,
     "阶段超时下限，防止大面积阶段被统一短超时掐断"),
    ("agent_degradation_policy", "pipeline", "降级收据策略", False, True,
     "require_receipt = 未处置降级不收尾；warn = 只记警告"),
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
    ("plugin_allow_ui_bundle", "plugins", "允许插件 UI bundle", False, True,
     "M8 受信任档：iframe 沙箱加载插件界面；需插件带签名（默认关闭）"),
    # ── 存储与路径 ──
    ("persona_dir", "storage", "Persona 目录", False, False, "仓库启动时快照，需重启"),
    ("plugin_dir", "storage", "插件目录", False, False, "加载器启动时构造，需重启"),
    ("plugin_data_dir", "storage", "插件数据目录", False, False, "需重启"),
    ("project_dir", "storage", "项目目录", False, False, "管理器导入时固化，需重启"),
    # ── 工作区（PRD workspace-prd-2026-10-24）──
    ("workspace_dir", "storage", "工作区根目录", False, False, "项目全部产物（工程/素材/下载/成片）的根；需重启"),
    ("workspace_scan_enabled", "pipeline", "自动扫描工作区素材", False, True,
     "轮询 materials/ 发现新素材（用户手动投放的入口）"),
    ("workspace_scan_interval_sec", "pipeline", "扫描间隔（秒）", False, True, "默认 20s；越小越及时，越大越省 IO"),
    ("workspace_vision_enabled", "pipeline", "素材视觉解读", False, True,
     "对未索引素材做标签/描述（默认本地 CLIP，选 llm 才计费）"),
    ("workspace_vision_daily_budget_usd", "pipeline", "视觉日预算（USD）", False, True,
     "超出后剩余文件标 deferred，索引本身仍完成"),
    ("workspace_cache_max_gb", "storage", "工作区缓存上限（GB）", False, True, "超出按最旧删除；0=不限制"),
    # ── 服务与预算 ──
    ("host", "service", "监听地址", False, False, "uvicorn 启动参数，需重启"),
    ("port", "service", "监听端口", False, False, "uvicorn 启动参数，需重启"),
    ("debug", "service", "调试模式", False, False, "日志级别在导入时配置，需重启"),
    ("mongo_uri", "service", "MongoDB URI", True, False, "可能含凭据；连接在启动时建立，需重启"),
    ("mongo_db_name", "service", "MongoDB 库名", False, False, "需重启"),
    ("llm_monthly_token_budget", "service", "LLM 月度预算", False, True, "全局 token 预算；0 = 不限"),
    # ── 调度中枢（PRD v1.4 / B2）──
    ("supervisor_mode", "pipeline", "调度中枢模式", False, True,
     "off = 关闭（默认）；shadow = 只记录归因与诊断书不改执行；on = 启用派工（B3+）"),
    ("supervisor_attribution_timeout_sec", "pipeline", "归因兜底超时(秒)", False, True,
     "规则不命中时的 LLM 兜底超时；失败回退规则结论"),
    # ── 路线 C：全 agent 化（PRD F12 / C2–C3）──
    ("agent_mode", "pipeline", "主 Agent 模式（已废弃）", False, True,
     "已失去作用：创作恒走主 Agent（Task）。保留字段只为兼容旧配置文件解析，不要再用"),
    ("pipeline_enabled", "pipeline", "允许老管线执行（已失效）", False, True,
     "该开关已不再被任何代码读取：/api/pipeline/run-async 与 retry 一律 410。"
     "保留字段只为兼容旧配置解析"),
    ("agent_decide_policy", "pipeline", "主 Agent 决策策略", False, True,
     "off=只走规则；auto=失败必判、成功轻量步走规则；always（默认）=每步都调 LLM 决策"),
    ("agent_decide_stream", "pipeline", "决策走流式", False, True,
     "true（默认）=决策时思考逐块到前端；provider 不支持时自动回退非流式"),
    ("kernel_mode", "pipeline", "Kernel 主循环形态", False, True,
     "tool_loop（默认）=模型拿工具 schema 自主调用（原生 tool-use loop）；"
     "shadow=跑 loop 只记录不落副作用；plan=旧计划表 adapter，仅回滚/测试用"),
    ("agent_loop_max_turns", "pipeline", "tool loop 模型回合上限", False, True,
     "单次 tool-use loop 最多几轮模型往返（默认 12，不可置空）"),
    ("agent_loop_max_tool_calls", "pipeline", "tool loop 工具调用上限", False, True,
     "单次 tool-use loop 最多几次工具调用（默认 60，含派发子 agent）"),
    ("context_budget_chars", "pipeline", "决策上下文预算(字符)", False, True,
     "超出按优先级裁剪，并记 context_loaded.trimmed"),
    ("report_max_chars", "pipeline", "工作区报告上限(字符)", False, True,
     "单份 reports/*.md 的上限；超出截断并标记"),
    ("eval_report_dir", "pipeline", "评估报告目录", False, True,
     "golden_eval 写、/api/eval 读；留空 = `_cache/eval`"),
    ("workspace_roots", "pipeline", "工作区父目录白名单", False, True,
     "JSON 数组；留空=只用 workspace_dir。填了之后前端可在这些目录下新建工作区"),
    ("agent_max_turns", "pipeline", "Agent 回合上限", False, True,
     "单个任务最多几个 plan-step 回合（默认 30，不可置空）"),
    ("agent_max_tool_calls", "pipeline", "Agent 工具调用上限", False, True,
     "单个任务最多几次工具调用（默认 80，不可置空）"),
    ("agent_max_wall_sec", "pipeline", "Agent 墙钟上限(秒)", False, True,
     "单个任务最长运行时间（默认 3600，不可置空）"),
    # ── 模型路由（PRD F8 / B4）──
    ("model_routes", "llm", "模型路由表", False, True,
     "按阶段配置候选模型（JSON）；留空 = 全部沿用主/闪模型"),
    ("model_whitelist", "llm", "模型白名单", False, True,
     "provider/model 或裸 model 精确白名单；留空 = 允许所有已配置 provider"),
    ("risk_judge_model", "llm", "风险判定模型", False, True,
     "命令风险判定用模型；必须命中白名单，留空 = 最便宜的 cheap 档"),
    # ── 类型同一性 LLM 裁判（PRD F12 L2/L3 / V17）──
    ("genre_judge_enabled", "llm", "类型裁判整体开关", False, True,
     "默认关（D7）：关时 genre-check 写 judge=skipped；开则真调 LLM 做同一性复核"),
    ("genre_judge_use_flash", "llm", "类型裁判用闪模型", False, True,
     "开（默认）= 闪模型省 token；关 = 主模型（判据更细但更贵）"),
    # ── 预算（PRD F6 / B5）──
    ("pipeline_budget_usd", "service", "管线预算(USD)", False, True,
     "留空 = 无上限（只记账不阻断）；填写后达 70% 降级、100% 停止派工"),
    ("model_prices", "llm", "模型单价表", False, True,
     '{"provider/model": {"in": 0.003, "out": 0.015}}（每 1k token）；未配置的模型按 0 计'),
    # ── 子代理与执行（PRD F10/F11）──
    ("max_concurrent_subagents", "pipeline", "子代理并发上限", False, True,
     "同一管线同时存活的子代理数（默认 6）"),
    ("supervisor_auto_spawn", "pipeline", "自动派生只读核查员", False, True,
     "归因置信度 low 时由编排层派一个只读子代理查看现场；仅 supervisor_mode=on 生效"),
    ("exec_allow_network", "pipeline", "允许执行联网", False, True,
     "默认禁网；打开后执行网关的子进程可联网（诊断用）"),
    ("exec_timeout_sec", "pipeline", "执行超时(秒)", False, True,
     "单条命令/脚本的墙钟上限（默认 60）"),
    ("exec_hard_floor_never_authorizable", "pipeline", "硬底线永不授权", False, True,
     "默认 False：硬底线（读凭据/不可逆破坏）交 LLM 判定 + 两级人工确认；"
     "改 True 回到「规则直接拒绝、不可授权」的旧语义"),
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
    # 对象型配置（model_prices / model_routes）必须报 "dict"：前端据此渲染 JSON 文本域。
    # 旧实现在这里落到 "string"，于是单价表在设置页只能以单行文本编辑，填进去就 422。
    if annotation is dict or origin is dict:
        return "dict", ()
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
