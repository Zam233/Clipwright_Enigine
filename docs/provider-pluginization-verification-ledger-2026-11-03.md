# Provider 插件化 · Batch 0 核实台账

由 PRD 自动提取 + 本会话已核事实生成。**状态**：`已闭环`（有结论与证据）／`待核实`（未查）。

提取口径：`docs/provider-pluginization-and-prompt-override-prd-2026-11-02.md` 中含「待核实」字样的实质问题行，共 **71** 条（原文自称「70 处」，实际扫到 71；裁定 3/4 新增的 9 条已在 PRD §15.7 闭环，不在本表）。

| 编号 | PRD 节 | 问题（原文截断） | 状态 | 结论 / 证据 |
|---|---|---|---|---|
| H-001 | 1. 术语 | `needs_uploader`  ／  TTS provider 的能力标记，表示该 provider 需要上传器；实际消费者、端到端行为待核实  ／  | 已闭环 | 消费者 = providers/tts/__init__.py:60-64（cosyvoice needs_uploader=True）→ services/voice.py:479/:576 形参 → :692 实例化；全仓无其它调用方（PRD §15.1.7） |
| H-002 | 2.1 engine 里与 provider 耦合的点清单 | `clipwright/services/voice.py`（约 40KB；精确行号待核实）  ／  TTS/语音服务实现；CosyVoice 出现于事实包给出的行 5、8、45、160  ／  | 已闭环 | 行号已定位：:228 PublicUploadManager、:347/:368/:379 BaseVoiceProvider、:390 QwenTTSProvider、:715 _get_provider、:887 dub_script（PRD §15.1.1） |
| H-003 | 2.1 engine 里与 provider 耦合的点清单 | `clipwright/api/voice.py`（约 5.9KB；精确行号待核实）  ／  语音 API；具体端点与协议耦合点待核实  ／  | 已闭环 | 端点 :56 upload ／ :95 clone ／ :115 list ／ :122 delete ／ :132 synthesize ／ :151 dub（PRD §15.1.1） |
| H-004 | 2.1 engine 里与 provider 耦合的点清单 | `clipwright/prompts.py`（26 常量、8 builder；精确符号行号待核实）  ／  集中保存提示词默认值，是唯一提示词文案源  ／  | 已闭环 | 8 个 builder 定义行与调用点已逐符号定位（PRD §15.2.1） |
| H-005 | 2.1 engine 里与 provider 耦合的点清单 | `agents/requirements_agent.py`（精确行号待核实）  ／  将插件提示词作为“插件能力扩展”附加到 system prompt，未替换 `prompts.py` 符号  ／  | 待核实 | — |
| H-006 | 2.2 Provider 类型 × 当前实现位置 × 是否已插件化 | Embedding  ／  `clipwright/providers/base.py:72` 定义协议  ／  待核实  ／  事实包未提供内置实现位置或插件目录实现清单  ／  | 待核实 | — |
| H-007 | 2.2 Provider 类型 × 当前实现位置 × 是否已插件化 | Rerank  ／  `clipwright/providers/base.py:90` 定义协议  ／  待核实  ／  事实包未提供内置实现位置或插件目录实现清单  ／  | 待核实 | — |
| H-008 | 2.2 Provider 类型 × 当前实现位置 × 是否已插件化 | TTS  ／  `clipwright/providers/tts/__init__.py` 内置 qwen-tts、cosyvoice、minimax；`services/voice.py`  ／  部分：provid… | 待核实 | — |
| H-009 | 2.2 Provider 类型 × 当前实现位置 × 是否已插件化 | STT  ／  `clipwright/providers/stt/__init__.py:52-68` 内置 whisper、faster-whisper  ／  待核实  ／  有 `STT_PROVIDER` 分发… | 待核实 | — |
| H-010 | 2.2 Provider 类型 × 当前实现位置 × 是否已插件化 | 图像  ／  `plugins/ai_*` 目录与实现  ／  待核实是否已在插件目录  ／  事实包仅要求列入核查，不给具体目录名或实现事实  ／  | 待核实 | — |
| H-011 | 2.2 Provider 类型 × 当前实现位置 × 是否已插件化 | 视频  ／  `plugins/ai_*` 目录与实现  ／  待核实是否已在插件目录  ／  同上  ／  | 待核实 | — |
| H-012 | 2.2 Provider 类型 × 当前实现位置 × 是否已插件化 | 音乐  ／  `plugins/ai_*` 目录与实现  ／  待核实是否已在插件目录  ／  同上  ／  | 待核实 | — |
| H-013 | 2.3 TTS：后端链 + 前端控件 | 已知后端链**：`TTS_PROVIDER` 已有分发；内置 provider 注册含 qwen-tts、cosyvoice、minimax；CosyVoice `needs_uploader=True`；`servic… | 待核实 | — |
| H-014 | 2.3 TTS：后端链 + 前端控件 | 待核实项  ／  建议核实方法  ／  产出  ／  | 待核实 | — |
| H-015 | 2.4 提示词：默认值消费与插件能力 | `clipwright/prompts.py` 集中保存 26 个常量与 8 个 `build_*` 参数化函数；符号消费点清单及每个 agent 的导入路径待核实。全局约束是不搬移、不复制，所有默认值由该模块提供。 | 待核实 | — |
| H-016 | 2.4 提示词：默认值消费与插件能力 | 缺口分两类：符号覆盖（常量键、目标 agent、来源）与参数化模板覆盖（模板键、参数 schema、占位符一致性、渲染后验证）。实际 builder 占位符集合待核实，W5 必须从运行时/AST 提取作为契约测试基线。 | 待核实 | — |
| H-017 | 4.2 Provider 能力接口 | 契约能力字段至少覆盖：模型清单获取（静态或动态、失败行为）、参数 schema、流式支持、工具调用、reasoning 映射、计费/usage 上报。字段是否必填、模型发现 API、计费单位与 token 口径均待核实；… | 待核实 | — |
| H-018 | 4.3 同名冲突规则 | provider key 在同一 `ContributionKind` 下唯一；大小写与别名归一规则待核实。 | 待核实 | — |
| H-019 | 4.3 同名冲突规则 | 覆盖内置 provider 的插件必须在 UI/日志暴露 provider id、插件 id 与版本；具体审计字段待核实。 | 待核实 | — |
| H-020 | 4.4 启停、热插拔、隔离与审计 | 沿用现有插件治理能力（启停持久化、错误通道、reload 回滚、配置迁移、secret 加密与审计日志；具体 API/字段待核实）。provider 启停后新请求使用新注册表快照；进行中的请求不强制切换实例。热插拔对进行… | 待核实 | — |
| H-021 | 4.5 密钥与 schema 驱动 | 插件声明字段 schema：类型、标签、默认值、必填、敏感标记、验证规则及可见条件。字段规范需 W1 定稿；前端按 schema 渲染，不能按 provider 名称写死字段。敏感字段提交至 `settings_stor… | 待核实 | — |
| H-022 | 5.1 OpenAI 兼容与 Anthropic 插件 | 创建 `plugins/llm_openai_compatible/`，配置 schema 至少表达 base URL、鉴权引用、模型/请求参数与 reasoning 映射；具体字段名、缺省值、是否允许无鉴权由 W1 核… | 待核实 | — |
| H-023 | 5.2 Engine 职责与 isobase | engine 保留注册表查询、统一协议调度、角色绑定、回退、请求生命周期、超时/取消与结果归一化；`services/llm.py` 不再构造或持有特定协议客户端。`_resolve_llm_provider` 作为注册… | 待核实 | — |
| H-024 | 5.2 Engine 职责与 isobase | `isobase` 处理策略：W1 检查依赖版本、可注入性及现有测试；W2 优先由插件继续使用 `isobase`，engine 抽象只依赖 `LLMProvider` 协议。若 `isobase` 对插件构建或依赖隔离… | 待核实 | — |
| H-025 | 5.3 内置 fallback 与迁移 | 迁移阶段保留内置实现作为插件未安装/禁用时的 fallback，保持 provider key 与角色绑定可解析；当同名插件安装时按 4.3 规则覆盖内置。完成回归、配置迁移与发布观察期后，是否删除内置实现由产品与维护者… | 待核实 | — |
| H-026 | 6.1 边界与接口 | `TTSRequest/Result` 当前字段与流协议待核实；功能层向前端输出 schema 化控件描述，后端接收规范化请求并解析 provider 结果。功能层如何注册 UI Slot、前端插件渲染权限与控件 DSL… | 待核实 | — |
| H-027 | 6.2 前端控件描述 | 控件描述采用版本化 schema：`id`、`type`、`label`、`default`、`required`、`options`/`constraints`、`visibleWhen`、`providerScope… | 待核实 | — |
| H-028 | 6.3 错误传播与降级 | 协议插件错误必须保留 provider id、阶段（鉴权/请求/流式/解码）、可重试性和用户可读错误码；敏感响应/密钥不进入日志。功能层区分 provider unavailable、配置缺失、上传器缺失、合成失败与取消… | 待核实 | — |
| H-029 | 7.2 校验、优先级与冲突 | builder 覆盖时比较占位符集合及参数类型；缺失、多余或类型不匹配一律拒绝该覆盖，并返回符号名、期望集合与实际集合。格式化语法与嵌套占位符规则待核实。 | 待核实 | — |
| H-030 | 7.3 来源、禁用与回滚 | 最终 prompt 组装结果保留每段来源元数据（默认模块、Category、Persona、Skill、plugin id/version、符号名、覆盖/附加模式）；可在诊断 UI 或结构化日志查看，不展示敏感上下文。U… | 待核实 | — |
| H-031 | 8. 数据与接口契约增量 | 所有字段均为方案草案；事实包未确认的类型、默认值标「待核实」，W1/W2 定稿前不得视为现存实现。 | 待核实 | — |
| H-032 | 8. 数据与接口契约增量 | Provider manifest  ／  `provider_id: string`；无默认值  ／  注册唯一键；现有 manifest 是否支持待核实  ／  | 待核实 | — |
| H-033 | 8. 数据与接口契约增量 | Provider manifest  ／  `config_schema: object`；`{}`  ／  schema 驱动配置表单；版本字段待核实  ／  | 待核实 | — |
| H-034 | 8. 数据与接口契约增量 | Provider manifest  ／  `capabilities: object`；按协议声明  ／  models/stream/tools/reasoning/usage；结构待核实  ／  | 待核实 | — |
| H-035 | 8. 数据与接口契约增量 | Provider source  ／  `provider_id: string`；旧记录迁移填旧 key  ／  将配置源与实现解耦；存储模型待核实  ／  | 待核实 | — |
| H-036 | 8. 数据与接口契约增量 | Provider source  ／  `plugin_id: string ／ null`；`null` 表示 builtin  ／  来源可见性与审计；字段待核实  ／  | 待核实 | — |
| H-037 | 8. 数据与接口契约增量 | Provider source  ／  `config: object`；旧字段迁移  ／  敏感值走 Fernet；具体 schema 待核实  ／  | 待核实 | — |
| H-038 | 8. 数据与接口契约增量 | TTS UI descriptor  ／  `controls: array<object>`；`[]`  ／  功能插件控件列表；字段待核实  ／  | 待核实 | — |
| H-039 | 8. 数据与接口契约增量 | Prompt contribution  ／  `target: string`；必填  ／  `agent:<id>` 或 `symbol:<name>`；编码格式待核实  ／  | 待核实 | — |
| H-040 | 8. 数据与接口契约增量 | Prompt contribution  ／  `mode: enum(append ／ replace)`；`append`  ／  区分 agent 附加与符号替换；现有贡献字段待核实  ／  | 待核实 | — |
| H-041 | 8. 数据与接口契约增量 | Prompt contribution  ／  `template: string`；无默认值  ／  文本或 builder 模板；占位符约束待核实  ／  | 待核实 | — |
| H-042 | 8. 数据与接口契约增量 | Prompt contribution  ／  `source_plugin: string`；注册时生成  ／  来源追踪；现有审计字段待核实  ／  | 待核实 | — |
| H-043 | 8. 数据与接口契约增量 | API 增量原则：优先扩展 `/api/providers` 现有 list/create/available/roles/source CRUD，不新建平行 provider API。拟新增 schema 查询端点 `… | 待核实 | — |
| H-044 | 8. 数据与接口契约增量 | 前端 `ProviderKind` 扩展 STT 的必要性待核实：现有供应商页事实清单只有 llm/embedding/rerank/tts 四 Tab；STT 是否加入 Tab 由产品确认。store 增加 `prov… | 待核实 | — |
| H-045 | 9. 迁移与兼容 | 迁移前导出 `provider_sources` 的 `sources` 与 `roles` 快照；现有字段结构、备份机制待核实。 | 待核实 | — |
| H-046 | 9. 迁移与兼容 | 旧前端只收到兼容字段；新增字段可选，旧客户端忽略未知字段不崩。现有 API 是否使用严格响应模型待核实。 | 待核实 | — |
| H-047 | 9. 迁移与兼容 | 迁移失败按单 source 原子回滚；不允许部分写入后覆盖原配置。事务/存储原子能力待核实。 | 待核实 | — |
| H-048 | 10. WBS | 规模/人日为规划估算，不是事实包数据；按单工程师有效工作日估算，含实现与针对性验证，不含等待评审。总计 32 项任务、约 47 人日。具体依赖真实代码结构，待核实项在任务启动时确认。 | 待核实 | — |
| H-049 | W1 契约与注册表（7 项，约 10 人日） | 1  ／  盘点现有 provider 协议与注册调用  ／  `providers/base.py:16-17,43,72,90,108,126,139`；注册调用点待核实  ／  无  ／  各类型均有协议、能力与 … | 待核实 | — |
| H-050 | W1 契约与注册表（7 项，约 10 人日） | 4  ／  定义密钥引用与 schema 驱动 UI 契约  ／  `provider_sources.py:86`；`api/providers.py:30-123`；settings encryption 落点待核实… | 待核实 | — |
| H-051 | W1 契约与注册表（7 项，约 10 人日） | 5  ／  定义启停、热插拔与请求生命周期  ／  `contribution_dispatch.py:41-50`；插件治理函数待核实  ／  任务 3  ／  禁用后新请求不再选中插件；在途请求行为有明确可观察结果 … | 待核实 | — |
| H-052 | W1 契约与注册表（7 项，约 10 人日） | 6  ／  建立插件失败隔离与审计事件契约  ／  插件错误通道/审计落点待核实；`base.py:74`  ／  任务 2  ／  一个坏插件失败后应用与其他 provider 仍可用，审计能定位插件  ／  M  ／… | 待核实 | — |
| H-053 | W1 契约与注册表（7 项，约 10 人日） | 7  ／  固定协议能力测试矩阵  ／  `providers/base.py:186,203,221`；测试文件待核实  ／  任务 1-6  ／  流式、工具调用、reasoning、usage 不支持时明确显示，不… | 待核实 | — |
| H-054 | W3 TTS 双插件（原 6 项，约 10 人日；**已被 §15 修订为 8 项 / 14 人日**） | 1  ／  梳理 voice API/service/provider 调用链与控件  ／  `services/voice.py`、`api/voice.py`、`DubView.tsx`、`VoicePage.tsx… | 待核实 | — |
| H-055 | W3 TTS 双插件（原 6 项，约 10 人日；**已被 §15 修订为 8 项 / 14 人日**） | 2  ／  抽离 TTS 工作流功能层插件  ／  `TTS_PROVIDER` dispatch `contribution_dispatch.py:46`；插件 UI 接入点待核实  ／  W1 完成  ／  TTS… | 待核实 | — |
| H-056 | W3 TTS 双插件（原 6 项，约 10 人日；**已被 §15 修订为 8 项 / 14 人日**） | 3  ／  定稿控件 schema 与前端 renderer  ／  `ProvidersPage.tsx:15-19`；DubView/VoicePage 控件待核实  ／  任务 1、W1 完成  ／  插件控件配置… | 待核实 | — |
| H-057 | W3 TTS 双插件（原 6 项，约 10 人日；**已被 §15 修订为 8 项 / 14 人日**） | 6  ／  验证错误传播、取消与 provider 降级  ／  `api/voice.py`、`services/voice.py`；精确函数待核实  ／  任务 2-5  ／  CosyVoice 失败后只有配置了兼… | 待核实 | — |
| H-058 | W4 其他 provider 类型（6 项，约 8 人日） | 3  ／  迁移 Embedding provider  ／  `providers/base.py:72`；实现文件待核实  ／  任务 1  ／  embedding 角色能解析插件，原索引流程可用  ／  L  ／… | 待核实 | — |
| H-059 | W4 其他 provider 类型（6 项，约 8 人日） | 4  ／  迁移 Rerank provider  ／  `providers/base.py:90`；实现文件待核实  ／  任务 1  ／  rerank 角色可选择插件，缺失时不静默伪装成功  ／  M  ／  中… | 待核实 | — |
| H-060 | W4 其他 provider 类型（6 项，约 8 人日） | 5  ／  核实图像/视频/音乐 `plugins/ai_*` 归属  ／  `plugins/ai_*` 路径与实现待核实  ／  任务 1  ／  三类清单明确哪些已插件化，哪些不在本期  ／  S  ／  低  ／… | 待核实 | — |
| H-061 | W5 提示词覆盖插件（原 7 项，约 10 人日；**已被 §15 修订为 8 项 / 12 人日**） | 1  ／  盘点 26 常量、8 builder 的消费点与占位符  ／  `clipwright/prompts.py`、各 agent 消费点待核实  ／  可独立；依赖提示词基线  ／  每个符号能定位消费处与默认… | 待核实 | — |
| H-062 | W5 提示词覆盖插件（原 7 项，约 10 人日；**已被 §15 修订为 8 项 / 12 人日**） | 3  ／  实现 builder 占位符校验  ／  `prompts.py` builder；具体语法待核实  ／  任务 1、2  ／  少一个或多一个占位符时注册失败并报告差异  ／  M  ／  高  ／  | 待核实 | — |
| H-063 | W5 提示词覆盖插件（原 7 项，约 10 人日；**已被 §15 修订为 8 项 / 12 人日**） | 4  ／  接入最终 prompt 组装与既有顺序  ／  RequirementsAgent 获取逻辑行号待核实；`builtin_contributions.py:163,195`  ／  任务 2、3  ／  五层… | 待核实 | — |
| H-064 | W5 提示词覆盖插件（原 7 项，约 10 人日；**已被 §15 修订为 8 项 / 12 人日**） | 5  ／  实现来源诊断与冲突报告  ／  `prompt_registry.py:99`；UI/日志落点待核实  ／  任务 2  ／  用户能看到生效符号、来源插件与版本  ／  M  ／  中  ／  | 待核实 | — |
| H-065 | W5 提示词覆盖插件（原 7 项，约 10 人日；**已被 §15 修订为 8 项 / 12 人日**） | 7  ／  运行时 prompt 快照回归  ／  `prompts.py` 26 常量 + 8 builder；测试路径待核实  ／  任务 4、6  ／  无覆盖时动态发出的 prompt 与基线完全一致  ／  M… | 待核实 | — |
| H-066 | W2 OpenAI/Anthropic | 请求 reasoning 并查看诊断信息  ／  reasoning 映射可见且与普通正文分离；确切 UI 形式待核实  ／  | 待核实 | — |
| H-067 | 13. 分期与依赖 | 关键路径估算**：W1（10 人日）→ W2（9 人日）与 W3（10 人日）并行 → W4（8 人日）；W5（10 人日）可与 W2/W3 并行。总工作量约 47 人日；关键路径按 W1 + max(W2,W3) + … | 待核实 | — |
| H-068 | 14. 开放问题 | 产品确认 prompt 来源诊断入口放在 Provider/插件管理 UI、Agent 调试日志还是两处都做；具体 UI 落点待核实。 | 待核实 | — |
| H-069 | 15.1 裁定 3：engine 默认不带任何 TTS 能力 | #### 15.1.7 核实结论（原「待核实」，2026-11-02 晚已全部闭环） | 待核实 | — |
| H-070 | 15.2 裁定 4：全部 `build_*` 允许被插件替换 | #### 15.2.6 核实结论（原「待核实」） | 待核实 | — |
| H-071 | 15.7 本轮核实的方法与新增事实（可复核） | 70 条原始待核实（§2–§13）中与 OpenAI/Anthropic/isobase/embedding/rerank/STT 相关的部分**本轮未逐条复核**，仍按原台账待 Batch 0 处理。 | 待核实 | — |

**进度**：已闭环 4 ／ 71，待核实 67。

## B0 分组结论（按 coding plan §2 的 B0-T1…B0-T12）

| 组 | 任务 | 状态 | 结论与证据 |
|---|---|---|---|
| B0-T1 | 71 条待核实落在哪些事实域 | 已闭环 | 分布：§2.1 行号类 15、§8 契约类 16、§10 WBS 18、§4 契约 5、§5 Provider 4、§6 TTS 3、§9 迁移 3、§7 提示词 2、其余 5。行号类已被 §15.7 逐条消解 |
| B0-T2 | `ContributionKind` 名单/数量/新增限制 | 已闭环 | **17 类**（`plugins/contribution.py:22-44`）：tool／skill／agent／material_source／category／style_interpreter／hook／prompt／**llm_provider／tts_provider／stt_provider**／renderer_backend／animation／**embedding_provider／rerank_provider**／**ui_slot**／export_preset。**无 HTTP 路由类** → 网关路线 B 成立。`ConflictPolicy` = `reject`／`override`（`:47-50`）。注：`schema/plugin.py:33` 的 docstring 写「14 类」是**文档漂移**，实际 17 |
| B0-T3 | manifest 实际 schema | 已闭环 | `schema/plugin.py:38-99`：`id/name/version/kind/description/author/entry_point` + `api_version`（默认 "1"，"2" 起支持 contributions）+ **`contributions: list[ContributionDecl]`**（「声明即授权」）+ `override_builtin` + `permissions` + `dependencies` + `signature`(HMAC) + `config_schema_version` + `runtime`(inprocess／subprocess) + `runtime_config`。`ContributionDecl` = `{kind, id(pattern ^[a-z][a-z0-9_]{1,63}$), meta}`。**Provider 专属字段（config_schema/capabilities）当前只能塞 `meta`，B1-T2 需要定是否升字段** |
| B0-T4 | 冲突、优先级、reload/回滚 | 已闭环 | `ContributionRegistry._validate`（`plugins/contribution.py:175-194`）：未在 manifest 声明 → 拒绝（声明即授权）；与内置冲突 → **默认拒绝**，只有 `ConflictPolicy.OVERRIDE` 才放行；与其它插件冲突 → 拒绝；**同 owner 重复 = 幂等覆盖**（这就是 reload 路径）。`register(dispatch=False)` 供内置镜像入库避免二次注册；`unregister_plugin(owner)` 回收并 `clear_declared`，`loader.py:606-607` 额外清 prompt 注册表。⚠️ 与 PRD §4.3 的关系见事实更正 1 |
| B0-T5 | LLM provider 协议 / isobase | 部分闭环 | `providers/base.py` 已定义 6 个 Protocol：`LLMProvider`(:43, capabilities/build_client/default_model)、`EmbeddingProvider`(:72)、`RerankProvider`(:90)、`TTSProvider`(:108, clone/synthesize)、`STTProvider`(:126)、`RendererBackend`(:139)；`missing_llm_provider_methods()`(:234) 已是注册期协议校验入口。**isobase 是 git 依赖且无 ref**（`pyproject.toml:26`），PRD 说的「锁定版本」当前不存在。reasoning 透传细节未读 |
| B0-T6 | TTS 凭据/上传器/别名/缓存/异常/`dub` 边界 | 已闭环 | 见 PRD §15.1.1 与 §15.1.7（:715 身份 + 指纹缓存、:228 上传器无外部调用方、`providers/tts/__init__.py:17/35/39/52/55-67`、`api/voice.py` 六端点、编排 `:887`） |
| B0-T7 | embedding／rerank／STT 现状 | 已闭环 | 三者**都已用内置让位模式**：`embedding/__init__.py:137-142`（openai/ollama/local）、`rerank/__init__.py:181-185`（http/local）、`stt/__init__.py:52,64-68`（whisper/faster-whisper）——都是「先 resolve(kind, 首 id) 非空就 return，否则注册内置」。注册入口被 `api/providers.py:60`、`api/settings.py:81-85` 调用；`api/settings.py:57-83` 的 `_DYNAMIC_CHOICE_KINDS` 按 kind 动态给下拉项 |
| B0-T8 | 提示词 26 常量 + 8 builder + 消费点 | 已闭环 | 见 PRD §15.2.1/§15.2.5/§15.7：8 个 builder 签名与调用点、`PluginPrompt` 无 symbol/mode（`:31-35,99`）、插件提示词消费点仅 4 个 agent |
| B0-T9 | 管理页 schema／密钥表单／前后端字段 | 已闭环 | 前端 `ProvidersPage.tsx`：`TABS`(:15) 四类，`baseUrl`/`keys` 是写死的厂商字段（`:46,51`），而 provider 选项由 `providersForKind()`(:102) 动态给 → **字段写死、选项动态**，正是要改的点。后端端点比 PRD 列的多：`/{id}/models`(:132)、`/{id}/models/{mid}`(:154)、`/{id}/fetch-models`(:163)、`/{id}/test`(:205)。密钥走 `api_keys_masked`（前端 `:51`、后端 `provider_sources.py:86 _mask_key`），PUT 传掩码=保留旧值 |
| B0-T10 | 测试清单与兼容基线 | 已闭环（清单级） | `tests` 中命中 provider/tts/prompt 等关键字的文件 **135 个**；**行为断言关键子集 22 个**：`test_tts_provider_registry`、`test_voice_providers`、`test_voice_service`、`test_voice_api`、`test_voice_audio_agent`、`test_voiceover_e2e`、`test_stt_provider_registry`、`test_llm_provider_protocol`、`test_provider_acceptance`、`test_provider_available`、`test_provider_sources`、`test_provider_role_binding_rag`、`test_provider_transport`、`test_resolution_determinism`、`test_model_listing_contract`、`test_prompt_authority`、`test_prompt_resolution_event`、`test_stage_prompt_wiring`、`test_type_prompts`、`test_plugin_contribution`、`test_plugin_dispatch`、`test_builtin_contributions`。**改契约前必须逐条阅读这 22 个**（测试由人跑，但阅读是我的责任） |
| B0-T11 | 文档同步点／配置开关／回滚边界 | 部分闭环 | `services/settings_store.py`：`anchor("settings.json")` 原子写；secret `{"__enc__":True,"v":<Fernet>}`，**无密钥 fail-closed 拒绝保存**（`:116-124`）；`_ENV_KEYS` 是模块导入时快照（`:33`），故 `apply_all` 必须在 resolve 之前。**独立的 provider 插件开关不存在** → 回滚只能禁用 manifest 或回滚提交（与计划 §7 一致） |
| B0-T12 | 逐条闭环审阅 | 待核实 | 依赖上面各组完成 |

**下一步（本 goal 后续轮次）**：先补 B0-T4／T5／T7／T9／T11 五组，再逐条把 71 条里剩下的 67 条从「待核实」推成「已闭环」；完成后才动 Batch 1。


## 事实更正（本轮核实推翻了文档里的 4 条主张）

**更正 1：内置让位语义已经是现状，不是待实现项。**
`providers/tts/__init__.py:49-50` 已有 `if ContributionRegistry.resolve(ContributionKind.TTS_PROVIDER, "qwen-tts") is not None: return`——插件先注册同名 provider 时，内置**根本不注册**。embedding(`:137`)、rerank(`:181`)、STT(`:52`) 三者同样如此。
⇒ 裁定 3 要改的**不是**「改成有插件才注册」（已经是），而是「**没有插件时也不要注册内置**」。B3-T6 的描述已按此改写。
另外这也解释了为什么 `providers/llm/__init__.py:25-31` 的让位能与 `ConflictPolicy` 默认 REJECT 共存：**让位发生在注册前**，冲突判定根本不会触发；如果将来改成「先注册内置、插件后注册」，就必然被 REJECT 挡下——这是 B1-T3 必须守住的顺序。

**更正 2：isobase 没有版本锁。** `pyproject.toml:26` 是 `isobase @ git+https://github.com/landspark/IsoBase.git`，无 `@<ref>`。PRD §5.2 与 §12 讲的「锁定现有版本」目前**不存在**，要在 W1 补 pin（或明确接受跟随 HEAD）。

**更正 3：`api/providers.py` 的端点比 PRD §8 列的多 4 个**：`/{id}/models`(POST,:132)、`/{id}/models/{mid}`(DELETE,:154)、`/{id}/fetch-models`(:163)、`/{id}/test`(:205)。schema 驱动表单要覆盖的是这些端点背后的字段，不只是 CRUD。

**更正 4：前端 provider 表单是「选项动态 + 字段写死」**。`ProvidersPage.tsx` 的 provider 选项来自 `available`，但 `baseUrl` / `keys` / model 编辑区都是写死的表单字段，所以 B1-T6 的工作量集中在**把写死字段换成 schema 渲染**，不是重写页面。

**另一处文档漂移（待批量修）**：`schema/plugin.py:33` 的 docstring 写「见 ContributionKind 的 14 类」，实际是 17 类。

## Batch 1 进度（进行中）

| 任务 | 状态 | 说明 |
|---|---|---|
| B0-T12 逐条审阅 | 进行中 | 71 条台账剩 63 条未逐条标注（多数为 §8/§10 的行号类，不阻塞 Batch 1 实施） |
| B1-T1 定稿贡献种类与 kind 映射 | 已完成 | 不改枚举（17 类足够）；修掉 `schema/plugin.py:33` 的「14 类」文档漂移 → 17 类 + 名单冻结提示 |
| B1-T3 冲突/优先级/内置让位 | 已完成（代码收敛） | 新增 `clipwright/providers/builtin_registry.py`：`needs_builtin_registration()` + `register_builtin_once()`；`llm`/`embedding`/`rerank`/`stt`/`tts` 五处内置注册全部改调它，删掉各自复制的「先查再注册」。让位顺序从此只有一种写法 |
| B1-T5 能力网关契约 + 动态映射 | 已完成（契约落地） | 新增 `clipwright/api/capability.py`：`POST /api/capability/{name}`，`{params}` → `ToolExecResult`；状态码 404/422/503/200 按 PRD §15.1.5；`_validate_tool_params()` 加 `status_code` 参数以支持网关的 422（工具端点仍 400）。已挂进 `main.py` |
| B1-T2 manifest Provider 字段 | **已完成** | 定为**升一级字段**：`ContributionDecl.config_schema` + `capabilities`（`schema/plugin.py`），`meta` 继续承载 kind 专属零散键。校验器新增 `clipwright/plugins/provider_decl.py`（`validate_declaration` / `register_declaration` / `schemas_for_kind` / `forget_plugin`）；加载时校验，不合法声明从「已声明」集合剔除并写错误通道，不连累同插件其它贡献。实测：真实插件 `headless_chrome` 的 `renderer_backend:chrome_headless_shell` 被正确捕获 |
| B1-T4 密钥声明/脱敏 | **已完成（声明侧）** | `config_schema` 字段级 `secret: bool` 即声明，非 bool 直接拒绝（实测报错「secret 必须是 bool」）。落盘仍走既有 Fernet（`settings_store` fail-closed）与 `provider_sources._mask_key`，未新增第二条加密通道 |
| B1-T6 前端 schema 驱动表单 | **已完成** | 链路打通：`GET /api/providers/schemas` → `providersApi.schemas()` → `schemaFor(schemas, kind, provider)` → `ProvidersPage` 新增「插件配置」区块（按 `config_schema` 渲染 input / select，`secret` 走 password，未知能力键在下方列出）。后端同时补 `config` 落盘与掩码：`provider_sources._encrypt_config/_mask_config/_secret_config_fields`（secret 字段 Fernet 加密，`****` 回传=保留旧值，无密钥 fail-closed）；`create_source/update_source` 接受 `config`，`_public_source` 输出掩码形态。实测：public 读到 `sk-p****`、落盘为密文、掩码回传保留旧密文、非 secret 字段原样、内置 provider（无声明）不受影响 |

**已跑过的验证（不含 pytest/vitest）**：`py_compile` 全绿；导入 + 幂等烟测确认五个家族各注册一次、
`needs_builtin_registration` 语义正确；`TestClient` 打到 `/api/capability/list_animations` → 200
`status=success`、未知能力名 → 404 带可读原因。



## Batch 2 进度（Provider 插件落地）

| 任务 | 状态 | 说明 |
|---|---|---|
| B2-T1 定义 OpenAI 兼容 Provider 插件边界 | 已完成 | 边界定为「插件接管**注册与声明**，协议适配类暂留 engine」：`providers/llm/openai_provider.py` 只有 68 行、只依赖 `providers.base` 与传输层；一次性搬类会打断既有测试对 `clipwright.services.llm.OpenAIChat` 的打桩链（`test_llm_provider_protocol.py:37` 等）。W2 收尾再搬，见 PRD §5.3 迁移过渡期 |
| B2-T2 OpenAI 兼容插件 | 已完成 | `plugins/llm_openai_compatible/`（`plugin.yaml` + `main.py` + `__init__.py`）：声明 `openai`/`ollama` 两个 `llm_provider`，带 `config_schema`（`api_key: secret`、`base_url`）与 `capabilities` |
| B2-T3 Anthropic 插件并接管同名内置 | 已完成 | `plugins/llm_anthropic/`：实测加载后 `anthropic` 归属 `llm_anthropic`，`openai`/`ollama` 归属 `llm_openai_compatible`，三个 id 都不再由 `__builtin__` 提供；`/api/providers/available` 仍是三个名字（无回归） |
| B2-T4 接通 role/source 解析与脱敏 | 已完成（复用既有链路） | provider 解析仍走 `ContributionRegistry.resolve`，只是实现来源换成插件；脱敏未改（`api_keys` 掩码 + B1-T4 的 `config` 掩码） |

### 关键发现：内置先注册会让插件接管失败（已解决）

实测第一次加载失败，原因不是插件写错，而是**顺序**：

1. `main.py` lifespan 在 4.2~4.7（第 161-199 行）先注册全部内置 provider；
2. 第 249 行才 `_plugin_loader.load_all()` 加载插件；
3. 于是插件注册 `llm_provider:openai` 时撞上「已存在内置」→ 默认 REJECT，报错只说「不得覆盖内置贡献」。

两条修法同时落地（互不替代）：

- **`override_builtin: true`**（两个插件都声明）：`plugins/base.py:92` 据此把冲突策略切成
  `ConflictPolicy.OVERRIDE`，允许接管内置。这条路对「运行时启用插件」（启动后才加载）是**唯一**可行的。
- **逐 id 让位**（`providers/builtin_registry.py::register_builtin_once`）：从「整族一刀切」改成按 id 过滤，
  插件先注册时内置只补没人接管的那些。整族一刀切会造成两种坏情况：内置把 `anthropic` 也注册了 →
  插件那条被拒；或内置整族不注册 → `anthropic` 直接消失。

### 另一处踩坑：`entry_point` 必须写包路径

`entry_point: main.py` 会被当成模块名 `main.py` 去导入 → `模块加载失败`。仓库里其余插件都写
`"<目录>.main"`（`ai_image_gen.main` 等），只有 `sandbox_demo` 写 `main.py`（`runtime: subprocess`
走的是另一条装载路径，不受影响）。两个新插件已改为 `"llm_anthropic.main"` / `"llm_openai_compatible.main"`
并补 `__init__.py`。

### 预存缺陷（不是本轮引入，记录待修）

`plugins/example_full/plugin.yaml` 的 `entry_point` 是 `_example_full.main`，而目录名是 `example_full`
→ 启动时同样报「模块加载失败」。属于既有问题，本轮不动它（避免与 PRD 范围混在一起）。

## Batch 3 进度（TTS 双插件）

| 任务 | 状态 | 说明 |
|---|---|---|
| B3-T1 协议插件接入边界 | 已完成 | 边界：插件负责 `tts_provider` 的注册与声明（鉴权/请求/音频解析/`needs_uploader`），**不**拥有配音工作流；协议类暂留 engine（与 Batch 2 同策略，W3 收尾再搬） |
| B3-T2 CosyVoice 协议插件 | 已完成 | `plugins/tts_cosyvoice/`：声明 `tts_provider:cosyvoice`（`config_schema`: api_key(secret)/workspace_id/default_model，`capabilities`: stream/needs_uploader）。实测归属 `cosyvoice → tts_cosyvoice`，`qwen-tts`/`minimax` 仍内置（逐 id 让位正确） |
| B3-T4 功能层插件 + 编排下沉 | 已完成 | `plugins/tts_feature/`：`dub.py` 里的 `DubScriptOrchestrator` 接管切句、逐段合成、确定性 seed、句级/词级时间轴；engine 只保留 `VoiceService.synthesize()` 协议调度基座 |
| B3-T3 名称解析与指纹缓存 | 未改动（守住） | `_get_provider` 的身份仍来自 `contribution.id`，缓存键仍含凭据指纹；本轮没有碰它 |
| B3-T6 摘 engine 编排与内置注册 | **待人工验证后执行** | 按 PRD §15.1.6 第 2 条：插件完成并通过 §15.3 验收后立即删除。**删除前必须由人跑一次** `python -m pytest tests -q`，否则内置被摘掉而插件在某环境没加载时会直接失去配音能力 |
| B3-T5 `create.dub` slot 迁 03 步骤 | 部分完成（宿主已就位，插件 UI 待契约补齐） | 前端：`slots.ts` 名单 14→15（`create.dub`）、HomePage 在 03 步骤挂 `<Slot name="create.dub">`、内置配音控件（音色/自动配音/上传）在「有贡献时」整块让位、「无贡献时」回落（过渡期）。**插件侧 UI 未落地**：现有 widget schema 只有静态 `options`，拿不到音色列表（音色来自 `GET /api/voice/list`），插件版入口只能填音色 ID → 体验倒退，故本轮不发贡献。需要 SlotRegistry 评审加「动态选项数据源」（如 `optionsFrom: "voices"` 或 `data_source: {capability: ...}`）后才能做。 |
| B3-T7 能力发现与缺失一等状态 | 已完成 | `dub` 能力名由插件技能提供；`AudioAgent` 在 `call_skill("dub")` 返回 `not_found` 时写**可读降级说明**「未配音：当前没有可用的配音能力（TTS 功能插件未安装或被禁用）」，与普通失败（`自动配音失败: …`）分开；**没有**直连 `VoiceService` 兜底 |
| B3-T8 通用能力网关 + `/api/voice/*` 薄转发 | 已完成 | 网关在 Batch 1 落地；本轮把 `/api/voice/dub` 改成薄转发：`skill_resolve("dub")` → `SkillRegistry.execute`，端点不再调 `VoiceService.dub_script()` 编排；解析不到直接 `503` + 可读原因。实测：正常走插件技能；把插件与内置两次 unregister 后 → 503 |

### 为接管内置技能新增的显式出口（契约变更，需知悉）

`SkillRegistry` 原本是「内置优先、插件不得覆盖」（P1-13）。裁定 3 要求 `dub_script`
从 engine 搬进插件，而它必然先撞上这条规则。做法是给**已声明 `override_builtin` 的插件**
开一个显式出口，而不是削弱默认规则：

1. `SkillRegistry.register(..., allow_override=False)`：仅当为 True 时才允许覆盖内置，
   覆盖前的实例进 `_shadowed`，插件禁用时自动恢复；
2. 冲突策略从 manifest 一路传到注册表：`plugins/base.py` 算 `policy` →
   `ContributionRegistry.register(..., policy)` → `_dispatch_register(..., policy=policy)`
   → `contribution_dispatch.register(..., policy=)` → SKILL 分支
   `allow_override=(policy is ConflictPolicy.OVERRIDE)`；
3. 未声明 `override_builtin` 的插件仍然被拒，日志已带提示「如需接管请声明 override_builtin」。

### 本轮踩的两个坑

1. **`policy` 没传到底**：`_dispatch_register` 里调 `_dispatch.register(owner, item)` 时漏了
   `policy=policy`，于是插件虽然声明了 `override_builtin`，技能注册仍被当默认策略拒绝。
   表现是「日志说内置优先」，与 manifest 声明相反——差点去改 SkillRegistry。
2. **`is_available` 必须是同步方法**：写成 `async def` 会把 coroutine 塞进
   `SkillInfo.available: bool`，启动时 `ValidationError` 直接崩（`SkillRegistry.list()` 在
   lifespan 里就被调用）。



