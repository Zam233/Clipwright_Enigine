"""全局配置管理 + 日志配置。"""

from __future__ import annotations

import logging
from datetime import timezone, timedelta
from pathlib import Path
from typing import Any, Literal, Optional

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

    # --- 工作区（PRD docs/workspace-prd-2026-10-24.md）---
    # 项目唯一产物根：工程文件 / 素材 / 下载 / 成片 / 缓存都在同一目录，可整体备份。
    workspace_dir: Path = Path("workspaces")
    # 轮询 materials/ 发现新素材（用户手动投放的入口）
    workspace_scan_enabled: bool = True
    workspace_scan_interval_sec: int = 20
    # 视觉解读：默认本地 CLIP（零 API 费用）；provider=llm 才按次计费
    workspace_vision_enabled: bool = True
    workspace_vision_daily_budget_usd: float = 1.0
    # cache/ 上限（超出按最旧删除），0 = 不限制
    workspace_cache_max_gb: float = 2.0

    # --- 插件治理（M1/M2/M8/M15）---
    # manifest 签名密钥；空则从 local_secret 派生；再为空则不启用签名强制
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
    # B7-T7.5：provider id 由插件声明，engine 不再用 Literal 白名单限制
    llm_provider: str = "anthropic"
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

    # --- 调度中枢（PRD v1.4 / 开发计划 B2 起）---
    # off    = 关闭（默认）：行为与改造前完全一致
    # shadow = 影子：只记录归因与诊断书，不改执行（B2 的验收状态）
    # on     = 启用：后续批次（B3+）才允许真正派工/执行
    supervisor_mode: Literal["off", "shadow", "on"] = "off"
    # 归因/诊断的兜底 LLM 调用超时（秒）；规则命中时不调用 LLM
    supervisor_attribution_timeout_sec: float = 15.0

    # --- 路线 C 双轨开关（PRD F12）---
    # off    = 老 6 阶段管线是唯一入口（行为与今天完全一致，A13 的对照基线）
    # shadow = Kernel 只规划、不执行（零副作用，用来看计划质量与预估成本）
    # on     = Kernel 执行（创作唯一入口；老 6 阶段管线已删除）
    agent_mode: Literal["off", "shadow", "on"] = "off"
    # 老 6 阶段管线执行开关——**已失效**（M3 删除执行引擎后，/run-async 与 retry
    # 永久 410，与此开关无关）。保留字段只为兼容旧配置文件的解析，不要再用它。
    pipeline_enabled: bool = False
    # 主 agent 每步决策策略（PRD §16.10.1）：off=只走规则；auto=失败必判/成功轻量步走规则；
    # always（默认）=**每步都调 LLM 决策**（用户裁定：每步决策都走 llm）。
    agent_decide_policy: Literal["off", "auto", "always"] = "always"
    # 决策是否走流式（思考逐块到达前端）；provider 不支持时自动回退非流式
    agent_decide_stream: bool = True
    # 主循环形态（modern-agent PRD §2.2 / §5.2 M1，PRD 原名 kernel_mode）：
    #   tool_loop = 原生 tool-use loop（模型拿 surface.schemas() 自主调用工具，默认）
    #   shadow    = 跑 tool_loop 但只记录不产生副作用，用于与 plan 对比（§5.2）
    #   （旧的 plan 形态已在 M3 删除：传它只发降级告警，然后按 tool_loop 运行）
    kernel_mode: Literal["plan", "tool_loop", "shadow"] = "tool_loop"
    # tool loop 的模型回合 / 工具调用上限（0 = 沿用 TaskLimits）
    #: 单次 tool loop 的软上限；0 = 不设软上限（仍有结构性护栏 STRUCTURAL_*）
    agent_loop_max_turns: int = 0
    agent_loop_max_tool_calls: int = 0
    # 决策上下文预算（字符）：超出按优先级裁剪，并记 context_loaded.trimmed（§17.11）
    context_budget_chars: int = 6000
    # 单份工作区报告上限（字符）；超出截断并标记（§17.3）
    report_max_chars: int = 20000
    # 允许创建工作区的父目录列表（§17.8）；空 = 只用 workspace_dir
    workspace_roots: list[Path] = []
    # 评估报告目录（closeout B10）：golden_eval 写到这里，/api/eval 读这里
    eval_report_dir: str = ""
    # Kernel 四道闸（PRD F5 / Q1 已裁定；数值可调，不可置空）
    # 回合数 30 → 40：长文稿（43 段）要跑 structure/material/edit/animation/audio
    # 五阶段 + 探查产物，实测 30 轮不够（E2E 2026-10-28 触闸收尾）。
    agent_max_turns: int = 80
    agent_max_tool_calls: int = 400
    agent_max_wall_sec: float = 0.0  # 0 = 不设上限

    # --- 终止机制收口（docs/agent-termination-devplan-2026-11-02.md §3/§6/§7）---
    # 2026-11-02 用户裁定 1：软着陆轮次 600（原来 PRD 的 360/400 作废），
    # 且**项目级可覆盖**——解析顺序 项目配置(project.json agent_state.termination)
    # > 这里的全局值 > 代码默认。任务创建时快照，运行中改配置不影响在跑任务。
    agent_soft_landing_round: int = 600
    #: 总开关：关掉即回到"撞上限直接收尾"的旧行为（新字段照写，便于对比与回滚）
    agent_soft_landing_enabled: bool = True
    # 收口窗口（独立于 soft_landing_round，收口期间不消耗普通轮次）
    agent_closeout_rounds: int = 8
    agent_closeout_tool_calls: int = 128
    agent_closeout_max_wall_sec: float = 600.0
    # 2026-11-02 用户裁定 2：为质量 agent 预留预算（escrow）。
    # 只在 budget_usd 非空时冻结；比例 + 最低额 + 上限三条一起用，避免小预算把
    # 质量 agent 饿死、大预算又预留过多。轮次/工具调用**不**预留（0=不限语义）。
    agent_quality_budget_reserve_ratio: float = 0.2
    agent_quality_budget_reserve_floor_usd: float = 0.05
    agent_quality_budget_reserve_cap_usd: float = 2.0
    # 2026-11-02 用户裁定 4：两个开关均启用。
    #: 终态原因枚举写盘（关掉只写旧 reason 字符串，用于极速回滚）
    agent_terminal_reason_v1: bool = True
    #: capped 作为独立 TaskStatus（关掉则 capped 仍按 done 上报，兼容旧前端）
    agent_capped_status_v1: bool = True
    #: Goal 语义（blocker 连续轮数门槛、goal_update 事件）
    agent_goal_v1: bool = True
    #: 同一 blocker 连续多少轮才允许宣告 blocked（照抄 DSH 的 3）
    agent_goal_blocked_after_rounds: int = 3

    # --- 模型路由（PRD F8 / 开发计划 B4）---
    # 按阶段配置候选模型：[{"provider","model","tier","price_in","price_out"}]；
    # 留空 = 不路由，所有阶段沿用主/闪模型（与改造前完全一致）。
    model_routes: dict[str, list[dict[str, Any]]] = {}
    # 精确白名单（"provider/model" 或裸 model）；留空 = 允许所有已配置 provider。
    model_whitelist: list[str] = []
    # 命令风险判定所用模型（B9）；必须命中白名单，留空 = 最便宜的 cheap 档。
    risk_judge_model: str = ""

    # --- 类型同一性 LLM 裁判（PRD F12 L2/L3 / V17）---
    # D7 决定：**默认关**（关时 genre-check 写 judge="skipped"，绝不假装判过）。
    # 单次请求可用 payload.enable_genre_judge 覆盖；本项用于真机取证/服务级开启。
    genre_judge_enabled: bool = False
    # 裁判用闪模型（省 token）；False 用主模型
    genre_judge_use_flash: bool = True

    # --- 子代理与执行（PRD F10/F11）---
    # 同一管线同时存活的子代理上限（PRD §12 已定⑤：在设置中配置）
    max_concurrent_subagents: int = 6
    # 自动派生：归因置信度 low 时，由编排层派一个**只读**核查员去看现场。
    # 仅在 supervisor_mode="on" 时生效（shadow/off 一律不派生，保证 off 等价性）。
    supervisor_auto_spawn: bool = True
    # 执行网关是否允许联网（默认禁网；打开后可用于诊断）
    exec_allow_network: bool = False
    exec_timeout_sec: float = 60.0
    # 硬底线（读凭据 / 不可逆破坏）是否仍然「不可授权」。
    # 默认 False = 按用户决策交由独立 LLM 判定 + 两级人工确认（授权窗口 + 再次确认）。
    # 改成 True 即回到「确定性规则直接拒绝、任何模式都不放行」的旧语义，无需改代码。
    exec_hard_floor_never_authorizable: bool = False

    # --- 预算（PRD F6 / 开发计划 B5）---
    # **留空 = 无上限**：只记账、不阻断、不降级（这是用户显式选择的取舍）。
    pipeline_budget_usd: Optional[float] = None
    # 单价表：{"provider/model" 或 "model": {"in": 0.003, "out": 0.015}}（每 1k token）
    model_prices: dict[str, dict[str, float]] = {}

    # --- 服务 ---
    host: str = "0.0.0.0"
    port: int = 8080
    debug: bool = False

    # --- 安全红线 ---
    # 显式承认「无鉴权对外开放」才允许绑定非回环地址启动（fail-closed）。
    # 仅用于受控本地/内网环境；生产部署应设置 api_token。
    allow_insecure: bool = False

    # --- Pipeline 并行度（稳妥有界并发）---
    # 动画阶段逐片段 LLM MG 生成的并发路数；默认 6（LLM 生成是网络等待，并发翻倍时间近半）。
    # 设置 1 即回到串行。
    pipeline_concurrency: int = 6
    # 素材阶段场景级并行路数；默认 6。设置 1 即回到逐场景串行。
    material_concurrency: int = 6
    # 逻辑动画主路径：按「动画类型提示词 + 视频类型 + persona」由 LLM 生成（M2/M3）。
    # 关闭后退回旧路径（mg_* 内置模板 / diagram_svg 图解），用于灰度与回滚。
    animation_llm_generation: bool = True
    # 文字动画 LLM 化（PRD text-animation-llm-2026-10-30）：True = prompt+LLM 生成；
    # False = 回退 AnimationCatalog 固定关键帧表（回滚开关）。
    text_animation_llm: bool = True
    # 类型化生成的并发上限（LLM 等待型，默认与 pipeline 并发一致）
    animation_llm_concurrency: int = 6
    # persona 提示词统一上限（PRD §16 R5）：旧实现 400/600/1500/2000 四处不同截断，
    # 同一个人格在不同路径拿到不同长度。统一到这里，超长时写 trace。
    persona_prompt_max_chars: int = 2000
    # 动画允许半透明底板（PRD §17 D7）：旧实现只要 vision_prompt 为空就强制
    # 背景透明，导致文字直接压在实拍画面上（浅背景不可读）。开启后半透明
    # 底板（alpha ≤ scrim_max_alpha）会被保留。
    animation_allow_scrim: bool = True
    animation_scrim_max_alpha: float = 0.6
    # 渲染期底板兜底（PRD §7.4）：对动画窗口的**背景采样亮度**，偏亮时把半透明
    # 底板烧进动画 MOV（文字条/卡片），解决「白字压在浅背景上看不清」。
    animation_render_scrim: bool = True
    animation_scrim_alpha: float = 0.75
    animation_scrim_grow: int = 8
    animation_scrim_blur: float = 6.0
    animation_scrim_light_threshold: float = 140.0
    # 动效客观门禁（PRD §5）：主运动位移占画布宽比例 / 关键帧覆盖时长比例。
    # 低于阈值时 critique 会强制走一次定向修复（见 generator._finalize_with_critique）。
    animation_motion_min_shift_ratio: float = 0.04
    animation_motion_min_coverage: float = 0.45
    # 渲染后像素级动效自检（PRD §5）：解码动画 MOV 抽帧算多样性，
    # 不合格只写 warning/metadata，不阻断渲染（动画已经生成，属于质量提示）。
    animation_motion_check: bool = True
    animation_motion_check_fps: int = 4
    animation_motion_check_min_frames: int = 8
    animation_motion_check_min_ratio: float = 0.25
    # 编辑期开关（此前 extra_params 里只有读取点、没有写入者）：
    # 请求显式传值时优先，否则用这里的全局默认（都默认关，行为不变）。
    edit_enable_color_match: bool = False
    edit_enable_pv_llm_tools: bool = False
    # 阶段墙钟下限（2026-10-29 E2E）：主 Agent 通过 delegate_agent 的 timeout_sec 自填值，
    # 实测它填 900s → 43 场 material（逐场搜素材 + 视觉 LLM）必然超时，重试三次仍是 900s，
    # 半小时白烧且 edit 因缺 candidate_clips 直接失败。0 = 不设下限（完全信任传入值）。
    agent_stage_min_timeout_sec: float = 3600.0
    # 降级处置策略（PRD modern-agent-review P0）：require_receipt = 无收据就判失败；
    # warn = 只记录不判失败（老行为，便于灰度）。
    # Kernel 确定性补派（2026-10-30）：模型提前收尾且提示词推不动时，由运行时
    # 用同一套工具面自己补派缺失阶段（计 usage、进事件流）。单测默认关闭。
    # 重复只读查询护栏（2026-10-30）：同参数 list_dir/read_report 直接返回上次结果。
    # 现场：一次任务里 list_dir 58 次、read_artifact 38 次，把回合烧光。
    agent_tool_dedupe: bool = Field(
        default=True,
        validation_alias=AliasChoices("AGENT_TOOL_DEDUPE", "CLIPWRIGHT_AGENT_TOOL_DEDUPE"),
    )
    agent_kernel_recovery: bool = Field(
        default=True,
        validation_alias=AliasChoices("AGENT_KERNEL_RECOVERY",
                                      "CLIPWRIGHT_AGENT_KERNEL_RECOVERY"),
    )
    agent_degradation_policy: Literal["require_receipt", "warn"] = Field(
        default="require_receipt",
        validation_alias=AliasChoices("AGENT_DEGRADATION_POLICY",
                                      "CLIPWRIGHT_AGENT_DEGRADATION_POLICY"),
    )
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
    # CLIP 权重的**本地目录**（离线/内网环境用）：填了就只从本地加载，绝不联网。
    # 空 = 按 HF 缓存加载（缓存缺失时会尝试下载，内网下会超时）。
    content_index_clip_model_path: str = ""
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
    # "模糊镜头"判定阈值（Laplacian 方差上界；越小越严）。
    # 现在还没有跨题材通用的绝对锐度尺度，这个值是经验起点，应在真实素材上标定。
    content_index_blurry_sharpness: float = 120.0
    # R9：给每个镜头算 CLIP 视觉向量（存进索引，供"换个说法也能搜到"的语义检索）。
    # 关掉后索引里没有画面向量，语义检索退化为纯文本检索（仍可用，但搜不到
    # "画面是海边、却没人写下海边"的镜头）。CLIP 不可用时自动跳过并如实标注。
    content_index_visual_vectors: bool = True
    # 渐变转场补检（交叉溶解/淡入淡出）：ffmpeg 的 scene 滤镜只看相邻帧差异，
    # 测不到溶接，会把溶接处当成同一个镜头。
    # 实测（4 段真实素材、2 处合成溶接）：**2/2 溶接检出**，但海浪镜头的连续涌动
    # 会误报 1 处（涌浪也是"持续的大幅变化"）。需要零误报的场合可以关掉。
    content_index_gradual_detect: bool = True

    # --- 安全 ---
    # API 令牌：设置后所有 /api/* 请求需携带 Authorization: Bearer <token>
    # 留空为开发模式（直接放行，启动时打印警告）
    api_token: str = ""
    # CORS 允许的来源（逗号分隔）；api_token 设置后生效，"*" 表示允许全部
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # --- 本地密钥（2026-11-08 剥离账号系统：不再对接 ClipWright Server）---
    # 仅作本机用途：插件 manifest 签名密钥与 Webhook secret 加密的兜底派生来源。
    # 环境变量：CLIPWRIGHT_LOCAL_SECRET（兼容旧名 CLIPWRIGHT_ACCOUNT_JWT_SECRET）。
    local_secret: str = Field(
        default="",
        validation_alias=AliasChoices("CLIPWRIGHT_LOCAL_SECRET", "CLIPWRIGHT_ACCOUNT_JWT_SECRET"),
    )

    # --- Webhook（P8: secret 落盘加密密钥；缺省时从 local_secret 派生）---
    webhook_secret_key: str = ""   # Fernet key (base64 urlsafe, 32 bytes)；空则用 local_secret 派生

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
    web_search_provider: Literal[
        "deepseek", "bocha", "baidu", "tavily", "serper", "bing", "local",
    ] = Field(
        default="deepseek",
        validation_alias=AliasChoices("WEB_SEARCH_PROVIDER", "CLIPWRIGHT_WEB_SEARCH_PROVIDER"),
    )
    web_search_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("WEB_SEARCH_API_KEY", "CLIPWRIGHT_WEB_SEARCH_API_KEY"),
    )
    # DeepSeek 自研搜索/抓取（2026-10-31 用户指定）：
    #   POST https://api.deepseek.com/v1/tools/search  {query, count, need_content}
    #   POST https://api.deepseek.com/v1/tools/fetch   {url} → {title, content(markdown)}
    # key 缺省复用主 LLM 的 DeepSeek key（同一平台账号即可用）。
    web_search_deepseek_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("DEEPSEEK_API_KEY", "WEB_SEARCH_DEEPSEEK_API_KEY",
                                      "CLIPWRIGHT_WEB_SEARCH_DEEPSEEK_API_KEY"),
    )
    # 各 provider 专属 key（缺省回落到 web_search_api_key）
    web_search_tavily_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("WEB_SEARCH_TAVILY_API_KEY",
                                      "CLIPWRIGHT_WEB_SEARCH_TAVILY_API_KEY"),
    )
    web_search_serper_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("WEB_SEARCH_SERPER_API_KEY",
                                      "CLIPWRIGHT_WEB_SEARCH_SERPER_API_KEY"),
    )
    web_search_bing_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("WEB_SEARCH_BING_API_KEY",
                                      "CLIPWRIGHT_WEB_SEARCH_BING_API_KEY"),
    )
    # 本地抓取 provider（DuckDuckGo / Bing HTML）：不需要任何 key。
    # 关掉它就回到"必须配付费 key 才能联网"的旧行为。
    web_search_allow_local: bool = Field(
        default=True,
        validation_alias=AliasChoices("WEB_SEARCH_ALLOW_LOCAL",
                                      "CLIPWRIGHT_WEB_SEARCH_ALLOW_LOCAL"),
    )
    # 联网素材化（PRD web-search-fetch M2）：动画/素材阶段把 web 搜索到的
    # 图片/视频落进 materials/web/ 并写来源许可旁车；关掉只保留文字事实参考。
    web_assets_ingest: bool = Field(
        default=True,
        validation_alias=AliasChoices("WEB_ASSETS_INGEST", "CLIPWRIGHT_WEB_ASSETS_INGEST"),
    )
    # 「哪些场景需要外部事实数据」由谁判断（2026-11-02，用户指出硬编码正则不合适）：
    # llm=模型判断（一次批量调用）｜heuristic=旧正则（离线回退）｜off=一律不联网取事实
    animation_fact_judge: str = Field(
        default="llm",
        validation_alias=AliasChoices(
            "ANIMATION_FACT_JUDGE", "CLIPWRIGHT_ANIMATION_FACT_JUDGE"),
    )
    # 域名治理（PRD web-search-fetch M3）：白名单非空 = 只放行其中域名；
    # 黑名单永远优先。逗号/分号/空白分隔，子域按后缀匹配。
    web_assets_allowed_domains: str = Field(
        default="",
        validation_alias=AliasChoices("WEB_ASSETS_ALLOWED_DOMAINS",
                                      "CLIPWRIGHT_WEB_ASSETS_ALLOWED_DOMAINS"),
    )
    web_assets_denied_domains: str = Field(
        default="",
        validation_alias=AliasChoices("WEB_ASSETS_DENIED_DOMAINS",
                                      "CLIPWRIGHT_WEB_ASSETS_DENIED_DOMAINS"),
    )
    # robots.txt 治理：默认遵守（拒绝时如实回报原因，不静默跳过）
    web_assets_respect_robots: bool = Field(
        default=True,
        validation_alias=AliasChoices("WEB_ASSETS_RESPECT_ROBOTS",
                                      "CLIPWRIGHT_WEB_ASSETS_RESPECT_ROBOTS"),
    )
    # 缓存 TTL：同一 URL 在 TTL 内复用已落盘文件，不重复下载；0 = 关闭缓存
    web_assets_cache_ttl_hours: float = Field(
        default=168.0,
        validation_alias=AliasChoices("WEB_ASSETS_CACHE_TTL_HOURS",
                                      "CLIPWRIGHT_WEB_ASSETS_CACHE_TTL_HOURS"),
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
    # B7-T7.5：同上（历史值 sentence_transformer 与门面 id local 等价，见 provider_settings_guard）
    rag_embed_provider: str = "sentence_transformer"
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
    # B7-T7.5：同上（第三方 TTS 插件可声明任意 id）
    tts_default_provider: str = "qwen-tts"
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
