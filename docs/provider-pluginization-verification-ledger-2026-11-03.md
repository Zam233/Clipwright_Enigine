# Provider 插件化 · Batch 0 核实台账

由 PRD 自动提取 + 本会话已核事实生成。**状态**：`已闭环`（有结论与证据）／`待核实`（未查）。

提取口径：`docs/provider-pluginization-and-prompt-override-prd-2026-11-02.md` 中含「待核实」字样的实质问题行，共 **71** 条（原文自称「70 处」，实际扫到 71；裁定 3/4 新增的 9 条已在 PRD §15.7 闭环，不在本表）。

> ⚠️ **本表的状态列是首轮提取时的快照**（只有第一轮 4 条被标为已闭环）。**最新结论以文末
> 「Batch 0-T12 逐条闭环」一节为准**——那里 71 条全部给了状态与证据（53 闭环 / 5 被取代 /
> 4 误命中 / 9 仍开）。本表保留原始提取文本，便于核对「问题原文」没有被改写。

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

**进度**：71 条逐条审阅完成 —— **已闭环 53**（含第一轮 4 条）、**已被裁定取代 5**、**非条目 4**、**仍开 9**（全部卡在「需人跑测试」或「产品待定」，见文末 Batch 0-T12 一节）。

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
| B3-T6 摘 engine 编排与内置注册 | **✅ 已完成（2026-11-03 执行）** | 执行前：核对清单 + 两次临时 worktree 演练（见清单 §6）。执行内容：删 `VoiceService.dub_script()`、删 `clipwright/skill/dub.py`、`skill/builtin.py` 去掉该技能注册、删 `ensure_tts_providers_registered()` 与 `main.py`/`api/providers.py`/`api/settings.py` 三处调用点、`split_text` 整体搬进 `plugins/tts_feature/split_text.py`（`dub.py` 改本地导入）。连带：5 个测试文件按删除后语义改写（`test_voice_data` 改导入、`test_voice_skill` → 新的 `test_tts_feature_dub`、`test_voice_service` 删 3 个 dub 用例、`test_tts_provider_registry` 改成"插件提供 + 内置函数已不存在"、`test_voice_api` 打桩技能；另修 `test_capability_matrix` skill≥3、`test_provider_available` 用真实 app、`test_provider_role_binding_rag` 装插件、`test_voiceover_e2e` 打桩能力解析），新增 `scripts/verify_tts_teardown.py`。**验证**：teardown 脚本通过、自检 13/13、全量 pytest 失败集合与删除前完全相同（44 个既存失败） |
| B3-T5 `create.dub` slot 迁 03 步骤 | **已完成** | 宿主：`slots.ts` 名单 14→15；`HomePage` 渲染 `<Slot name="create.dub" optionSources={voices} onValuesChange={setPluginDub} />`；内置配音控件在有贡献时整块让位。**契约补齐**：`UISelect.optionsFrom`（宿主白名单数据源，目前 `voices`）+ `PluginLayoutRenderer.onChange`/`Slot.onValuesChange`（插件控件值回传宿主，用 ref 避免内联箭头函数导致死循环）+ `Slot.optionSources`。插件：`plugins/tts_feature` 新增 `ui_slot: create_dub_entry`（select voice_id optionsFrom=voices + checkbox auto_dub），manifest 已声明（声明即授权）。宿主把插件值回填 `voicePick`/`autoDub`（引擎侧字段不变），`stepDone.dub` 有插件值时以插件为准。实测 `/api/plugin/contributions?slot=create.dub` 返回 1 条含 `optionsFrom`。<br>**2026-11-02 追加**：`create.dub` 补 **`file` 控件**（`UIFile`，值=`File` 对象）覆盖上传配音入口，上传执行/回执仍归宿主（`pickAudio`），插件不碰上传 API/路径/凭据；内置上传入口只在**无贡献**时回落。实测 `/api/plugin/contributions?slot=create.dub` 的 widgets 变成 `select voice_id` / `checkbox auto_dub` / `file upload_voice(accept=.wav,.mp3,.m4a,.mp4)` / `text` 说明 |
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




## Batch 4 进度（embedding / rerank / STT）

| 任务 | 状态 | 说明 |
|---|---|---|
| B4-T1 Embedding Provider 插件 | 已完成 | `plugins/embedding_providers/`：声明 `openai` / `ollama` / `local`，带 `config_schema`（openai: api_key(secret)+base_url；ollama: base_url）与 `capabilities.models`。实测归属全部切到插件 |
| B4-T2 Rerank Provider 插件 | 已完成 | `plugins/rerank_providers/`：`http`（base_url + api_key(secret)）/ `local`。实测归属切到插件 |
| B4-T3 STT Provider 插件 | 已完成 | `plugins/stt_providers/`：`whisper` / `faster-whisper`；`SttBackendEntry.call` 由插件提供、`service` 由调用方注入，行为与 engine 内置的两份 `_call_*` 一致 |
| B4-T4 前后端一致性 | 部分完成 | 后端 `/api/providers/available` 与 `/api/providers/schemas` 已覆盖三类；**前端仍无 STT tab**（PRD §14 开放问题第 3 条：STT 是否进 Tab 由产品定），未擅自加 |

### 关键发现：`ContributionDecl.id` 的 pattern 挡住了真实 provider id

`faster-whisper` 声明失败：`schema/plugin.py` 的 id pattern 是 `^[a-z][a-z0-9_]{1,63}$`，
**不允许连字符**。而仓库里既有 provider id 本来就带连字符（`faster-whisper`、`qwen-tts`）——
内置注册走 `ContributionKind.STT_PROVIDER, "faster-whisper"` 时不经过 manifest 校验，所以这个问题
一直没暴露；一旦要「插件声明同名 id」就必然撞上。

处理：把 pattern 放宽为 `^[a-z][a-z0-9_-]{1,63}$`。id 只做门面键（`kind:id`）与冲突判定，
不参与文件路径，放开连字符安全；已在 `schema/plugin.py` 注释里写明原因与暴露路径。

> 遗留：`tts_provider` 家族还有 `qwen-tts` / `minimax` 两个内置未被插件接管（本轮只声明了
> `cosyvoice`）。现在能声明了，等 W3 收尾一起搬，或按需再补一个 tts 协议插件。

## Batch 5 进度（提示词符号覆盖）

| 任务 | 状态 | 说明 |
|---|---|---|
| B5-T1 符号清单（26 常量 + 8 builder + 签名） | 已完成 | `plugins/prompt_overrides.py::all_symbols()` 运行时从 `prompts.py` 自省生成；builder 带 `inspect.signature`。**不硬编码清单**，`prompts.py` 加符号会自动出现 |
| B5-T2 常量/符号 replace + append 显式区分 | 已完成 | PROMPT 贡献支持 `meta.mode = append|replace`（缺省 append 保持存量 4 个贡献者行为）；replace 时 `meta.symbol` 指向符号，object 为字符串（常量）或可调用对象（builder） |
| B5-T3 builder 硬契约（签名/占位符/双 dry-render/静态纯度） | 已完成 | 见下「四关落地」 |
| B5-T4 消费点接入解析层 | **已完成（builder 8/8 + 常量 26/26）** | builder 8 处 `safe_builder`；常量全部接线：7 个整段提示词由 agent `resolve_constant`、11 个 builder 内部常量由 `prompts.py::_c`、3 个 MG 约束由 `generator.py::_mg_constraint`、3 个 `LOOP_*` 由 `agent_loop.py::_p`、**最后 2 个 `PROMPT_*_HEADER` 由 4 个文件里的 `_hdr(name)`**（原来它们是内联副本，不是死导出）。诊断接口：**26 wired / 0 unwired** |
| B5-T5 快照零差异 + 失败隔离 | 部分完成 | 默认等价性已断言；`safe_builder` 在插件实现运行期抛异常时回落默认并记录 `OVERRIDE_ERRORS`；完整快照比对需人工跑 |
| B5-T7 来源标识与诊断接口 | 已完成 | `GET /api/plugin/prompt-symbols`（admin 门禁）返回 constants/builders/errors；每条含 `wired` 与 `override{plugin_id, kind, source}`；**不返回提示词正文**，只给长度 |
| B5-T8 不建 demo 插件 | 已遵守 | 本轮没有产出任何 `plugins/prompt_*` 目录；负例用进程内对象完成 |

### 四关落地（PRD §15.2.2 / §15.2.7）

1. **签名**：比 `(参数名, 种类, 默认值)` 形状，**不比注解** —— `prompts.py` 用
   `from __future__ import annotations`，注解是字符串 `'int'`，插件里写的是类型对象 `int`，
   直接比 `inspect.signature` 会把所有合规覆盖误杀（本轮实测踩到）。
2. **占位符**：输出不得残留 `{xxx}`。
3. **双 dry-render**：按默认 builder 签名生成两组不同样例参数，输出必须不同 —— 专门拦
   「参数声明了却不用」（把运行时数字写死）。实测：返回固定字符串的实现被拒。
4. **静态纯度**：`ast` 白名单 + 黑名单。禁 import（除 `from __future__ import annotations`）、
   禁 `eval/exec/compile/__import__/open/input/globals/locals/vars/setattr/delattr/getattr`、
   源码出现 `os/sys/subprocess/socket/requests/httpx/pathlib/shutil/tempfile/random/time/
   datetime/uuid/asyncio` 即拒、禁 `async/await/yield/装饰器/global`、AST 节点 ≤ 600、
   嵌套 ≤ 8、`return` ≤ 8、源码 ≤ 32 KB、输出 ≤ `max(3× 默认, 6000)` 字符。

### 本轮踩的两个坑

1. **签名比较把注解也算进去了**：见上第 1 条。表现是「所有合规覆盖都被拒，理由写着签名不一致，
   但两边看起来一模一样」。
2. **`inspect.getsource` 对嵌套/局部函数保留缩进**，直接 `ast.parse` 报
   `unexpected indent`。修法：`textwrap.dedent()` 后再解析（插件把 builder 写在工厂函数里很常见）。

### 待办

- **19 个内部常量接线**：它们由 prompts.py 内部组合（如 MATERIAL_QUERY_BATCH_HEAD + … + TAIL），要在插件层覆盖需把组合从「导入期拼好」改成「运行时解析」，属独立一批改造；WIRED_CONSTANTS 已如实标注现状。
  `resolve_constant("X")` 才能让覆盖真正生效。数量多、分散在 7 个 agent + kernel/loop，
  需要一批机械改造（可按「先接被插件最可能覆盖的那几个」分批）。
- B5-T5 的完整快照比对（`prompts.py` 26+8 默认输出零差异）由人工跑。


### 纠错：3 个 LOOP 常量并不「已漂移」，是上一轮的比对方法错了

上一轮我写过「`agent_loop` 的内联副本与 `prompts.py` 常量已漂移（相似度 0.71~0.82）」——
**这个结论是错的**。当时的做法是拿常量去和「源文件里以该常量开头的一段窗口」做
`difflib` 比对，窗口里混进了周边代码，所以相似度天然偏低。

本轮改成**直接把模块里的内联常量与 `prompts.py` 常量逐字节比**：

| 内联常量（改前） | 对应 `prompts.py` 常量 | 结果 |
|---|---|---|
| `agent_loop._NORMAL_SYSTEM` | `LOOP_NORMAL_SYSTEM` | **sha256 完全一致**（1957 字符） |
| `agent_loop._PLAN_ONLY_SYSTEM` | `LOOP_PLAN_ONLY_SYSTEM` | **sha256 完全一致**（492 字符） |
| `agent_loop._CLOSEOUT_BLOCK` | `LOOP_CLOSEOUT_BLOCK` | **sha256 完全一致**（405 字符） |

所以「换成读常量」是**行为中性**的，已按此执行：

- `agent_loop` 新增 `_p(name)`（延迟导入 `resolve_constant`，异常回落默认）；
- `_system_prompt()` 改为 `_p("LOOP_PLAN_ONLY_SYSTEM") / _p("LOOP_NORMAL_SYSTEM")` +
  `_p("LOOP_CLOSEOUT_BLOCK")`，**内联的三份长文本已删除**（不再有第二真相）；
- `_PLAN_ONLY_SYSTEM` / `_CLOSEOUT_BLOCK` / `_NORMAL_SYSTEM` 保留为**导入期快照别名**
  （供诊断与既有引用），实际拼 prompt 一律走 `_p()`；
- 回归断言：三个别名的 sha256 与改前**完全一致**；`_system_prompt()` 无覆盖时输出不变；
  覆盖 `LOOP_NORMAL_SYSTEM` 后 `_system_prompt()` 立刻变化，`clear_plugin` 后回落。

### 仍存在的两个「死导出」

`PROMPT_PLUGIN_EXT_HEADER` / `PROMPT_WEB_CONTEXT_HEADER` 在 `prompts.py` 里定义，
但**全仓没有任何消费方**。覆盖它们不会产生任何效果，因此 `wired=False`。
处理方式二选一（待定）：接进对应消费点，或从 `prompts.py` 删除（后者会减少「可覆盖符号」数量，
需产品确认）。

## Batch 0-T12 逐条闭环（第三轮审阅，2026-11-02）

对台账里剩余 67 条「待核实」逐条给结论。**分四类**：

| 类别 | 数量 | 含义 |
|---|---|---|
| 已闭环 | 49 | 已由 Batch 1–5 的实现或本会话核实给出结论与证据 |
| 已被裁定取代 | 5 | 前提被后续裁定改变，原问题不再适用 |
| 仍开 | 9 | 确实还没做（多为「需要人跑测试」或「产品待定」） |
| 非条目（提取误命中） | 4 | 提取时误命中的标题/表头行，不是问题 |

| 编号 | 状态 | 结论与证据 |
|---|---|---|
| H-005 | 已闭环 | `requirements_agent.py`：常量 import 在 :26，插件附加段在 :120-126；现已改用 `resolve_constant("CREATIVE_BRIEF_SYSTEM")`（Batch 5） |
| H-006 | 已闭环 | `providers/embedding/__init__.py:17-58` 三个内置实现（OpenAI/Ollama/Local）；Batch 4 由 `plugins/embedding_providers` 接管注册 |
| H-007 | 已闭环 | `providers/rerank/__init__.py:19-52`（Http/Local）；Batch 4 由 `plugins/rerank_providers` 接管 |
| H-008 | 已闭环 | `providers/tts/__init__.py:55-70` 内置 qwen-tts / cosyvoice / minimax；`:49` 已有让位，Batch 3 由 `plugins/tts_cosyvoice` 接管 cosyvoice |
| H-009 | 已闭环 | `providers/stt/__init__.py:27-41` 探针 + `:57-71` 注册；Batch 4 由 `plugins/stt_providers` 接管两个后端 |
| H-010 | 已闭环 | 图像能力**已在插件目录**：`plugins/ai_image_gen/`（plugin_load 日志可见），不是 provider 贡献而是能力插件 |
| H-011 | 已闭环 | 视频：`plugins/ai_video_gen/` |
| H-012 | 已闭环 | 音乐：`plugins/ai_music_gen/` |
| H-013 | 已闭环 | TTS 后端链已在 PRD §15.1.1 逐段定位（service :347/:390/:715/:887、providers/tts、api/voice 六端点） |
| H-014 | 非条目（提取误命中） | 这一行是 §2.3 的「待核实项／建议核实方法／产出」表头，不是问题 |
| H-015 | 已闭环 | 符号清单与消费点：`prompts.py` 26 常量 + 8 builder，8 个 builder 调用点已逐符号定位（PRD §15.2.1）；插件提示词消费点仅 4 个 agent |
| H-016 | 已闭环 | 缺口已由 Batch 5 实现：符号级 replace（`prompt_overrides`）+ 四关校验；builder 占位符集合从运行时签名/默认输出导出，不再「待核实」 |
| H-017 | 已闭环 | `providers/base.py:17 ProviderCapabilities` 就是能力字段载体；`missing_llm_provider_methods():234` 做注册期协议校验 |
| H-018 | 已闭环 | 唯一性：`ContributionDecl.id` 同 kind 内唯一（`schema/plugin.py`）；别名归一：`providers/tts/__init__.py:17,35`（`_TTS_ALIASES` / `normalize_tts_name`）；Batch 4 把 id pattern 放宽到允许连字符 |
| H-019 | 已闭环 | 审计：插件加载/错误进审计流（`audit: plugin_load/plugin_error`）；覆盖归因见 `GET /api/plugin/prompt-symbols` 的 `override{plugin_id,kind,source}` |
| H-020 | 已闭环 | 插件治理能力已存在：manifest 签名、权限、依赖、启停持久化、reload、配置迁移、secret 加密、审计（`clipwright/plugins/` 全量实现） |
| H-021 | 已闭环 | `ContributionDecl.config_schema`（B1-T2）字段规范：`type/label/default/required/options/secret/placeholder`；前端 `ProvidersPage` 按它渲染 |
| H-022 | 已闭环 | `plugins/llm_openai_compatible/` 已创建（Batch 2），manifest 里就是 base_url + api_key(secret) + capabilities |
| H-023 | **已闭环（契约已满足，含一处刻意的例外）** | 核实结论：`services/llm.py` **不再构造客户端**——`_construct_client()` 只做「解析 provider → 过滤传输参数 → 调 `impl.build_client()` → 缓存」，全仓搜不到 `OpenAIChat(` / `AnthropicMessages(` 的调用点，真正的构造发生在 `providers/llm/{openai,anthropic}_provider.py`（现由插件注册）。**刻意保留的例外**：`services/llm.py:19` 仍 import isobase 的两个类，provider 里用 `getattr(_llm_mod, "OpenAIChat")` 取值——这是**测试打桩接缝**（`tests/clipwright/test_llm_provider_protocol.py:38` monkeypatch 它）。把 import 搬进插件会打断既有测试，属独立重构，不在本 PRD 范围 |
| H-024 | 已闭环 | isobase 版本：`pyproject.toml:26` 是 `git+https://github.com/landspark/IsoBase.git`，**无 ref/无版本锁**（结论即「当前无锁」） |
| H-025 | 已闭环 | 迁移期 fallback 已落地：`providers/builtin_registry.register_builtin_once()` 逐 id 让位 + 插件侧 `override_builtin` 接管 |
| H-026 | 已闭环 | TTS 协议侧结构：`TtsProviderEntry{name,factory,default_model,needs_uploader}`（`providers/tts/__init__.py:28-32`）；结果侧 `VoiceResult{success,error,data}`（`services/voice.py`） |
| H-027 | 已闭环 | 控件 schema：`PluginLayoutRenderer` 支持 **14 类**控件（input/string/select/checkbox/slider/button/image/spinner/alert/text/textarea/row/column/group/**file**）；`select` 的 `optionsFrom` 已接宿主白名单数据源（目前 `voices`）；`file`（2026-11-02 补）把 `File` 经 `onChange` 回传宿主，**上传执行与回执归宿主**，插件不接触上传 API/路径/凭据 |
| H-028 | **已闭环** | TTS 协议错误分类已实现：`services/voice_errors.py::classify_error()` 输出 `{error_code, stage, provider_id, retryable, message}`；三处统一使用（`VoiceService.synthesize` 的 except、音色不存在、provider 解析失败）；`/api/voice/synthesize` 失败时 `detail` 返回该结构（不再是单一人话字符串）；错误文本剥掉 `sk-…` / `Bearer …` / `api_key=…`。实测：401→`auth_failed/auth/false`、429→`rate_limited/request/true`、超时→`provider_timeout`、余额→`quota_exceeded`、断连→`stream_broken`、上传器缺失→`uploader_missing`、音色不存在→`voice_not_found`、provider 解析失败→`provider_not_found` |
| H-029 | 已被裁定取代 | 原「只比占位符集合」已被 PRD §15.2.2 加强为四关（签名形状 / 占位符 / 双 dry-render / 静态纯度） |
| H-030 | 已闭环 | 来源元数据：`prompt_overrides` 每条覆盖带 `plugin_id/kind/source`，`GET /api/plugin/prompt-symbols` 可查 |
| H-031 | 已被裁定取代 | 「所有字段均为方案草案」已被实现取代：§8 主要字段已落地（见 H-032…H-042） |
| H-032 | **已定（不新增）** | **技术判定：不新增 `manifest.provider_id`**。理由：`ContributionDecl.id` 已经承担「同 kind 内唯一键」角色，provider 解析、冲突判定、声明白名单全用它；再加一个 `provider_id` 会出现两份真相（哪个为准？同 id 不同 provider_id 时算不算冲突？），且所有既有实现与测试都要改。若产品坚持独立命名，需要一次完整契约评审（改名而非加字段）。**待产品确认可推翻** |
| H-033 | 已闭环 | `config_schema` 已落地为一级字段（B1-T2） |
| H-034 | 已闭环 | `capabilities` 已落地为一级字段，已知键 `models/stream/tools/reasoning/usage/needs_uploader` |
| H-035 | 已闭环 | source 侧用 `provider` 字段存实现名（`provider_sources.create_source`），与「provider_id」语义等价；未改名 |
| H-036 | **已闭环** | provider source 现在带 `plugin_id`：`provider_sources._public_source()` 每次读取时从门面 `ContributionRegistry.owner_of(kind, provider)` **实时推导**（空串=内置）。**不落盘**——落盘会与真实归属漂移（插件装卸后旧值还在）。实测：现有 `default` 源（provider=openai）读出 `plugin_id=llm_openai_compatible`；前端源列表显示「插件：xxx / 内置实现」 |
| H-037 | 已闭环 | source `config` 已落地：`_encrypt_config`/`_mask_config`（Fernet + `****` 保留旧值 + 无密钥 fail-closed） |
| H-038 | 已闭环 | TTS 控件由插件 `ui_slot` 贡献（`create.dub`），`layout.widgets` 即控件列表 |
| H-039 | 已被裁定取代 | `target: agent:<id> | symbol:<name>` 未采用；改为 `meta.mode=append|replace` + `meta.symbol`（更显式） |
| H-040 | 已闭环 | `mode: append|replace` 已实现（`contribution_dispatch` PROMPT 分支），缺省 append 保持存量行为 |
| H-041 | 已闭环 | builder 覆盖直接提交可调用对象（而非模板字符串），因此没有 `template` 字段；常量覆盖提交字符串 |
| H-042 | 已闭环 | 来源追踪：`source=plugin:<id>` 由注册时生成（`register_*_override(..., source=)`） |
| H-043 | 已闭环 | API 增量已加：`GET /api/providers/schemas[?kind=]`、`POST /api/capability/{name}`、`GET /api/plugin/prompt-symbols` |
| H-044 | 仍开 | 前端仍无 STT Tab；是否新增由产品定（PRD §14 第 3 条），未擅自加 |
| H-045 | 已闭环 | `provider_sources` 落盘是 JSON + 原子写（`_write` 走 `.tmp` → `replace`），迁移前可整文件备份；未做自动快照 |
| H-046 | 已闭环 | API 响应不是严格模型（`dict[str, Any]`），新增字段对旧前端是「多出来的键」，不会崩 |
| H-047 | 已闭环 | 单条 source 更新的原子性由 `_LOCK` + 原子写保证；迁移脚本未实现（当前无迁移需求：旧字段原样可读） |
| H-048 | 已被裁定取代 | 32 项 / 47 人日已被编码计划 §10 的任务级口径取代（42 项 / 51.5 人日） |
| H-049 | 已闭环 | W1 任务 1「盘点协议与注册调用」：`providers/base.py` 6 个 Protocol 已逐个核对（台账 B0-T5） |
| H-050 | 已闭环 | 密钥引用与 schema UI：`config_schema.secret` + Fernet + 掩码回显（B1-T4） |
| H-051 | 已闭环 | 启停/热插拔：`ContributionRegistry.unregister_plugin` + `clear_plugin`；贡献回收按 kind 分发（`contribution_dispatch.unregister`） |
| H-052 | 已闭环 | 失败隔离：`error_bus` 记录 + 单条贡献失败不中断其余；实测（插件声明非法只丢该条） |
| H-053 | 已闭环 | 协议能力测试矩阵：`missing_llm_provider_methods()` 已做注册期校验；测试文件清单见 B0-T10 的 22 个关键子集 |
| H-054 | 已闭环 | voice API/service/provider 调用链已在 PRD §15.1.1 精确定位（含 6 个 API 端点） |
| H-055 | 已闭环 | TTS 功能层插件已建：`plugins/tts_feature/`（`dub_script` 技能接管） |
| H-056 | 已闭环 | 控件 schema 与 renderer：宿主侧 `Slot` + `PluginLayoutRenderer` 已复用；插件侧 UI 受动态选项缺口限制（§15.9 #9） |
| H-057 | 部分闭环 | 错误传播侧已做（见 H-028：provider id / 阶段 / 可重试 / 可读码，API 一并返回）；**取消与降级仍待端到端验证**（需人跑） |
| H-058 | 已闭环 | Embedding 迁移：`plugins/embedding_providers/`（Batch 4） |
| H-059 | 已闭环 | Rerank 迁移：`plugins/rerank_providers/`（Batch 4） |
| H-060 | 已闭环 | 图像/视频/音乐归属：`plugins/ai_image_gen|ai_video_gen|ai_music_gen` 三个独立插件（非 provider 贡献） |
| H-061 | 已闭环 | 26 常量 / 8 builder 消费点与占位符：已逐符号建立（PRD §15.2.1 + Batch 5 的 WIRED_CONSTANTS 24/26） |
| H-062 | 已闭环 | builder 占位符校验已实现（且加强了双 dry-render 与静态纯度） |
| H-063 | 已闭环 | prompt 组装接入：8 个 builder + 24 个常量已走解析层；`builtin_contributions.py:163,195` 的收集逻辑未改（append 路径保持） |
| H-064 | 已闭环 | 来源诊断：`GET /api/plugin/prompt-symbols`（UI 落点仍未定，见 H-068） |
| H-065 | **已闭环（2026-11-03 实跑 pytest）** | 运行时 prompt 侧三重证据：①26 常量 + 8 builder 值与搬运前 sha256 全等（差异 0）；②自检第 13 项 `prompt_runtime_path` 证明运行时装配点读解析层；③**用户授权后我从第 30 轮起实跑 `py -3.14 -m pytest tests -q`**：4946 passed / 44 failed / 7 skipped / 1 xfailed，44 个失败全部是**与提示词无关的既存失败**（在同环境的改造前基线 `741651c` 上一个不差地同样失败，见下方「pytest 实跑」一节），提示词相关用例全绿 |
| H-066 | **已闭环** | 能力协商有了诊断入口：`GET /api/providers/capabilities?kind=&provider=&model=`（admin 门禁）按**实现真实返回** `capabilities()`（含 `reasoning_effort` / `reasoning_content` / `reasoning_streaming` / `generation_params` 等），解析不到时返回 `capabilities=null + error`（不抛 404，诊断要能显示「没装」）。前端 `ProvidersPage` 的「源设置」里新增能力徽标行（流式 / 工具调用 / 视觉 / JSON 模式 / 思考强度 / 思考内容）+ 提供方插件 id。实测：openai/anthropic 都报 `reasoning_effort=True, reasoning_content=True`；tts provider 无这些字段；未知 provider 返回「未安装或不可用」 |
| H-067 | 已被裁定取代 | W1…W5 粗估已被 §15.4 任务级口径取代 |
| H-068 | **已闭环** | 诊断 UI 落点定为**插件管理页**（`PluginsPage` → `PluginsPanel`）新增「提示词符号」按钮 + 模态框：显示 26 常量 / 8 builder、已接线数、被覆盖数、逐符号状态（绿点=已接线、灰点=未接线、标签=覆盖它的 plugin id）、以及最近被拒绝的覆盖原因。数据源 `GET /api/plugin/prompt-symbols`（admin 门禁，不返回提示词正文） |
| H-069 | 非条目（提取误命中） | 这是 PRD §15.1.7 的标题行，不是问题 |
| H-070 | 非条目（提取误命中） | 这是 PRD §15.2.6 的标题行，不是问题 |
| H-071 | 非条目（提取误命中） | 这是 PRD §15.7「仍未核实」的条目，已在本节逐条处理 |
| H-072 | **仍开（记录为已知缺口，非缺陷）** | 「提示词只有一处」还有**第二处模块级常量**没搬：`services/requirements_service.py:396` 的 `CREATIVE_BRIEF_SYSTEM`（:2254 作为 `system_prompt` 用，属「直接出草案」路径）。它与 `prompts.py` 的同名符号是**两份不同提示词**（前者一次给出完整草案、字段表是旧 13 项；后者多轮对话 + 由 `CreativeBrief` 模型生成字段表），**不是漂移、也不该合并**。为什么仍开：它是「模块级提示词常量还在 `prompts.py` 之外」的唯一一处。迁移代价不小——它会成为第 27 个符号，需同步 `WIRED_CONSTANTS`、自检断言（26→27）、PRD §15.2.1 与本台账/汇总/api_reference/AGENTS.md 的「26 常量」口径。当前**没有任何插件需要覆盖它**，故记录不迁移；将来要迁就走同一套四关（签名/占位符/双 dry-render/静态纯度） |

**net 结论**：Batch 0 的实质性核实已完成。**仍开 1 条**（H-057：TTS 取消的真机端到端，错误结构侧已由脚本覆盖）；H-065 已闭环（pytest 于 2026-11-03 实跑，提示词相关全绿、44 个既存失败与本改造无关，见文末「pytest 实跑」）。另 H-044（前端是否新增 STT Tab）是**纯产品决策**；H-072（`requirements_service` 的第二处模块级提示词）是**已知缺口、有意不迁**（无覆盖需求，迁移会牵动「26 常量」全口径）。其余（H-023 拆 client、H-028 TTS 错误分类、H-032/H-036 字段、H-066 reasoning UI、H-068 诊断 UI）均已闭环或给出技术判定。

> **H-065 的进展（2026-11-02 追补）**：静态侧（26 常量 + 8 builder 值与搬运前 sha256 全等）已完成；本轮又把「运行时装配点」固化进自检脚本第 13 项 `prompt_runtime_path`——`agent_loop._system_prompt()` 三态（plan_only / 正常 / 收口）必须等于 `prompts.py` 的符号，注册覆盖后必须立即改变，导入期快照不得被改写。**剩下真正需要人的只有「既有 pytest 用例全绿」这一件事**（脚本里没有任何测试引用提示词符号，所以那部分只能靠人跑全套）。

## 验收自检脚本（2026-11-02）

`scripts/verify_provider_pluginization.py` 把本次改造的运行期契约固化成一条命令（不依赖 pytest，不改落盘数据）：

| # | 检查项 | 覆盖的契约 |
|---|---|---|
| 1 | `provider_owners` | 五类 provider、11 个 id 全部插件归属，内置不得持有 |
| 2 | `providers_available_and_schemas` | `/api/providers/available` 与 `/schemas`（含 plugin_id） |
| 3 | `providers_capabilities` | 能力来自实现（含 reasoning_*）；未安装 → 200+error 而非 404 |
| 4 | `capability_gateway` | `POST /api/capability/{name}` 的 200 / 404 |
| 5 | `prompt_symbols_wired` | 26 常量 + 8 builder 全 `wired`，不返回正文 |
| 6 | `prompt_override_roundtrip` | 常量/builder 覆盖→解析→回滚闭环，且覆盖真的影响 builder 输出 |
| 7 | `prompt_builder_guards` | 四关负例：import/eval/getattr/async、参数不用、未知符号、新占位符 |
| 8 | `dub_capability_boundary` | `dub_script` 归属 tts_feature；全部摘掉后 `/api/voice/dub` → 503。**删除无关**：内置还在时第一次回收会恢复内置（再摘一次），`skill/dub.py` 删除后一次到底——两种状态都过 |
| 9 | `tier_map_empty` | 不变量 #12：`capability_matrix.TIER_MAP` 必须为空 |
| 10 | `provider_plugin_fallback` | 卸载插件后内置**补位**（`unregister_plugin` → `_resolve_llm_provider` 仍能解析，归属回到 `__builtin__`） |
| 11 | `skill_shadow_restore` | 插件技能被回收后内置同名技能**恢复**，归属回到内置。**改用 `analyze_video_structure`**（不再拿 B3-T6 要删的 `dub_script` 当铁证，否则删除后必红） |
| 12 | `all_families_fallback` | **五类** provider 逐个：卸载插件 → id 消失 → `ensure_*` 后内置补位（归属回 `__builtin__`）→ 恢复插件归属；只测 llm 会漏掉其它家族漏补位。**TTS 例外**：`ensure_tts_providers_registered` 被删后（B3-T6）自动切换成「退场」期望（卸载即空、不许有暗桩），删除前后都成立 |
| 13 | `prompt_runtime_path` | **运行时装配点**真的读解析层：用假 task 直接调 `agent_loop._system_prompt()`（plan_only / 正常 / 收口三态），并验证注册覆盖后装配点立即改变、导入期快照 `_NORMAL_SYSTEM` 不被改写、回滚后恢复默认。前两项（`prompt_symbols_wired` / `prompt_override_roundtrip`）只证明符号登记与解析层本身，**证明不了真正拼 prompt 的函数走了覆盖层**——有人把 `_p(...)` 换回模块级常量时，那两项照样全绿 |

实测：**13/13 通过**（`py -3.14 scripts/verify_provider_pluginization.py`），逐项单独跑也无顺序依赖。**它补充 pytest，不替代**。

### 写这个脚本时发现并修掉的真 bug

`SkillRegistry.register` 在「同一 owner 重复注册」（插件 reload / 幂等重注册）时会把
`_shadowed[name]` 覆盖成**插件自己的旧实例** → 之后卸载插件，恢复出来仍是插件实现，
**内置实现永远回不来**。复现三步：启动插件接管（影子=内置）→ reload 再注册（影子被覆盖成插件）
→ 卸载（恢复出插件实例，内置缺失）。

修法：只在实现来源发生变化（`old_src != new_src`）时压栈；同 owner 重注册不动影子。
这正是「写脚本顺带查出产品缺陷」的例子——不写这个检查，reload 场景的退化不会被任何人发现。

## 2026-11-02 追补：`create.dub` 的 `file` 控件 + 一处文档损坏修复

> **人工动作已集中到一处**：所有「需要人」的门槛（pytest / vitest / build、B3-T6 三项确认、
> H-057 取消端到端、前端观感）整理成可直接照着敲的一页 →
> [`provider-pluginization-manual-verification-2026-11-03.md`](provider-pluginization-manual-verification-2026-11-03.md)，
> 含期望输出与「回报什么」清单。

### 1. 上传配音入口迁进插件（PRD §15.9 第 10 项）

之前 `create.dub` 只覆盖「音色 + 自动配音」，上传配音还在 `HomePage` 的内置块里。本轮补完：

| 落点 | 内容 |
|---|---|
| 前端控件契约 | `types.ts` 新增 `UIFile`（`type: 'file'`，字段 `key` / `label` / `accept` / `placeholder`）；**没有** `action` / `endpoint` |
| 渲染器 | `PluginLayoutRenderer` 新增 `case 'file'`（共 14 类控件）：值 = `File` 对象、显示已选文件名、可清除；同一个文件可再次选择 |
| 宿主 | `HomePage` 在插件值里发现 `File` 就交给既有 `pickAudio`（时长探测 → 上传 → 去重提示），并用 `pluginPickedFileRef` 保证同一对象只上传一次；**不耦合插件私有 key** |
| 插件 | `plugins/tts_feature` 的 `create_dub_entry` 加 `file: upload_voice`（`accept=.wav,.mp3,.m4a,.mp4`）+ 说明文案 |
| 回落 | 内置上传入口现在只在 `usePluginSlots('create.dub')` 为空时渲染；上传中/失败/已上传的**回执**移到宿主侧、两种模式都显示 |

**边界（写进 AGENTS.md #25）**：控件的**值**回传宿主，**执行**（上传/落盘/请求）也在宿主；
插件拿不到上传 API、工作区路径与凭据。实测 `GET /api/plugin/contributions?slot=create.dub`
返回 4 个 widget：`select voice_id(optionsFrom=voices)` / `checkbox auto_dub` /
`file upload_voice(accept=.wav,.mp3,.m4a,.mp4)` / `text`。

### 2. `AGENTS.md` 被上一次编辑写坏了（真缺陷，已修）

`3abdf8d`（Batch 4 提交）对 `AGENTS.md` 的编辑把「贡献 id 允许连字符」那条 bullet
**截断成半行**，并把文档的**前半部分（标题 → 核心不变量 1–12）整块复制进了这一行**，
表现为：`## 快速入口` / `## 一键启动` / `## 项目结构` / `## 核心不变量` 各出现两次，
中间夹着一行 `pattern 是 \`^[a-z][a-z0-9_-]{1,63}# Clipwright / 帧艺 — AI Agent 项目地图`。
`08166da` 又在同一区域把两条 bullet 粘成了一行。修法：以 `3abdf8d^` 的干净版本为基线
重写这一段（`git restore --source=3abdf8d^`），再按原意补齐两处内容 + 新增 #25；
修完自检：四个二级标题各 1 次、`# Clipwright` 只出现 1 次、非空行 230。
教训与不变量 #13 同源：**改文档也要 diff 自检**，`git diff --stat` 出现「删 94 增 19」
这种手笔就该回头看是不是误删/误插。

## pytest 实跑（2026-11-03，用户授权后由 agent 执行）

用户在第 30 轮明确授权「由我来跑 pytest」，于是把项目约定里那一步补上。命令与结果：

```powershell
cd J:\Clipwright; py -3.14 -m pytest tests -q
# 4946 passed, 44 failed, 7 skipped, 1 xfailed, 10832 warnings in 547.89s
```

### 1. 首跑 57 个失败 → 其中 13 个是**本次改造引入的测试陈旧**，已修

首跑是 `57 failed, 4932 passed`。为了不把"环境差异"误判成"回归"，用
`git worktree add --detach J:\Clipwright-baseline 'f3de7bd^'`（本次改造起点前一个提交
`741651c`）建基线工作树，**把主仓 `.env` 复制了进去**（否则 LLM/搜索相关用例会因为缺
`CLIPWRIGHT_LLM_*` 而"假通过"，我第一遍就是这么被骗的：无 `.env` 时基线只有 38 失败），
再跑同一批用例：`44 failed, 13 passed`。差集即真回归 = **13 个**，全部集中在三处：

| 失败 | 根因 | 处理 |
|---|---|---|
| `tests/clipwright/test_plugin_contribution.py`（11 个） | 测试里 `_isolate_dispatch` 把 `_dispatch_register` 换成 `lambda owner, item: None`，而 B1 起该函数带 `policy=`（声明即授权 → 覆盖策略的通道）→ `TypeError` | 假实现补 `policy=None`，并在 docstring 说明这是契约演进而非产品回归 |
| `tests/clipwright/test_voice_api.py::test_dub_200` | 测的是 B3-T8 之前的契约（打桩 `svc.dub_script`）；端点已改薄转发 | 改成打桩 `skill_resolve` + `SkillRegistry.execute`；顺带把 `test_dub_service_error` 也改造确定化，并**新增 503 用例**（无插件时能力缺失 → 503 + 可读原因） |
| `tests/clipwright/test_voice_service.py::test_synthesize_unknown_voice` | 断言英文 `not found`；H-028 改成中文可读文案 + 结构化原因 | 改为断言「音色不存在」且 `data["error"]["error_code"] == "voice_not_found"` |

修完复跑三个模块：**41 passed**；全量复跑：**44 failed, 4946 passed**。

### 2. 剩下的是**既存失败**，与本改造无关（已用基线证明），并已分批清理

把基线工作树的 44 个失败 id 拿到当前代码上重跑：**44 failed（一个不差地同样失败）**，
而当时全量也是 44 个 → 两边失败集合**相等**，即本次改造引入的新失败为 **0**。

**2026-11-03 追补：这 44 个已清掉 15 个**（`a806765` / `051c42d` + 前端 `42d2090`），
其中包含**一处真的跨仓漂移**（后端新增 `goal_update` / `soft_landing` 事件，前端
`EVENT_TYPE_KEYS` 还停在 29 项 → 事件会落进 `unknown` 分支）。修完全量：
**29 failed, 4965 passed, 7 skipped, 1 xfailed**（619s）。

> ⚠️ **一处口径更正**：本节早先按 pytest 的 `lastfailed` 缓存分桶写过「kernel ~19 /
> 子 agent·管线 ~11」，那是**错的**——缓存里含着更早几轮已修好或没在这次跑到的 id。
> 以干净全量跑的 `-rf` 输出为准，当前 29 个的真实分布如下表。

当前 29 个的分布（都不在本 PRD 范围）：

| 模块 | 数量 | 备注 |
|---|---|---|
| `test_degradation_receipts` / `test_degradation_to_master_agent` / `test_final_receipt_attempt` | 4+1+2 | 降级收据 / 最终收据（终止机制那一轮的遗留） |
| `test_golden_harness` / `test_golden_capture` | 3+1 | golden 回归夹具（`driver()` 少了 `dry_run` 参数一类的桩过期） |
| `test_requirements_stream_chat` / `test_requirements_brief_ready_state` / `test_legacy_plan_session` | 3+1+2 | 需求侧流式/会话状态 |
| `test_plugin_review_fixes` | 2 | 内置插件「不自定义 initialize / manifest 声明贡献」的规范检查 |
| `test_animation_agent_persona` / `test_edit_agent_llm` / `test_stream_system_prompt` / `test_progress_report` / `test_dead_extra_keys` / `test_video_trim_validation` / `test_task_project_sync` / `test_pv_engine_plugin` / `test_kernel_replay` / `test_task_api` / `test_tool_surface` | 各 1 | 零散，需逐个看栈 |

> 报告口径：**「pytest 全绿」不成立**（44 个既存失败）；**「本次改造没有新增失败」成立**（差集为空，有基线工作树可比）。

### 3. 对 B3-T6 前置条件的影响（**已按口径 A 执行，2026-11-03 完成**）

删除清单 §0 原本写的是「`pytest` 通过」。事实是**全量不可能立刻绿**（44 个既存失败与本改造
无关，修它们属于另一件事），所以按口径 **A** 执行：以「13 个相关模块全绿 + 自检 13/13 +
删除演练已验证」为准。

**删除后的复跑**（同一命令，删除完成后）：

```powershell
py -3.14 -m pytest tests -q
# 44 failed, 4948 passed, 7 skipped, 1 xfailed  （630.19s）
```

- 失败数仍是 **44**，且模块分布与删除前**完全一致**（kernel / degradation / golden / web* /
  event_contract / capability_matrix 等既存区域），**没有一个落在 TTS / voice / provider /
  voiceover / plugin-contribution 上**；
- 通过数从 4946 涨到 4948：删掉旧用例、补上插件侧等价覆盖（`test_tts_feature_dub.py` 7 个）
  与两个新反向断言（裸 app 无插件时 TTS 下拉为空、内置注册函数不得回来）；
- 另外两条命令也复跑过：`scripts/verify_provider_pluginization.py` **13/13**、
  `scripts/verify_tts_teardown.py` 通过（插件在 → 三 id + 技能齐全；摘掉 → 列表空、
  `resolve` 为 None、`/api/voice/dub` → 503 + 可读原因；切句行为不变）。



