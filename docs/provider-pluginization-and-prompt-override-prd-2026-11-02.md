# 所有 Provider 插件化与提示词插件覆盖工程 PRD

> 状态：工程方案基线；除“权威事实包”明确给出的事实外，未验证事项统一标为「待核实」。本文不以行号推断未提供的代码事实。

## 0. 结论摘要表

| 做什么 | 不做什么 | 谁验收 |
|---|---|---|
| 复用既有 `LLM_PROVIDER`、`TTS_PROVIDER`、`PROMPT` 贡献点，先稳定 provider 插件契约，再迁移 OpenAI 兼容实现并建设 TTS 双插件与提示词符号覆盖 | 不直接假定新增贡献种类；名单冻结，新增须契约评审。首期不重写全部服务、不制作 demo 插件 | 产品确认行为边界；后端负责人验收注册、调度、迁移；前端负责人验收 schema 驱动表单与 TTS 控件；测试负责人验收回归 |
| 创建 `plugins/llm_openai_compatible/` 与 `plugins/llm_anthropic/`，使 engine 不再拥有协议实现 | 不把 Anthropic / OpenAI 协议逻辑留作不可替换的 engine 后门 | 后端负责人；按 W2 验收 |
| TTS 分为工作流/界面功能插件与 CosyVoice 协议插件；**engine 默认不带任何 TTS 能力（含 `/dub` 编排）**，并由主 agent 在识别到需要配音时经**能力解析**调用插件（见 §15 裁定 3） | 不把 CosyVoice 请求协议与 TTS 页面交互混成一个 provider；也不在 engine 里保留「默认可用」的 TTS 后门 | 产品、前后端负责人；按 §15 修订后的 W3 验收 |
| 插件可按 agent、常量符号、**以及全部参数化 `build_*`** 覆盖提示词，并展示来源、校验占位符、支持禁用回滚（见 §15 裁定 4） | 不迁移或复制 `prompts.py` 文案；搬运必须逐字不变 | 后端负责人；按 §15 修订后的 W5 验收 |

**关键取舍（1）**：沿用现有贡献点，不新增 `ContributionKind`。理由：贡献名单明确冻结，且注册分发已有对应分支；若实现证据显示现有契约表达不了需求，再单独发起契约评审。

**关键取舍（2）**：插件同名覆盖内置，且规则定为“注册表已有同名实现时跳过内置注册”。理由：事实包已给出该注册语义，保持现有行为可避免迁移时改变解析结果。

**关键取舍（3）**：内置 provider 仅作为插件缺失时的兼容回退，不与同名插件并存竞争。理由：存量安装需可启动，插件存在时必须只有一个解析结果。

**关键取舍（4）**：TTS 功能层与协议层拆成两个插件，且 engine 不再自带任何默认 TTS 能力。理由：功能层承载「什么时候该配音」的能力发现、入口贡献与结果接入（前端创作页的 TTS 入口也由功能插件贡献），协议层只负责 CosyVoice 协议与音频传输边界；engine 保留编排等于保留一条绕过插件的后门，插件化就名不副实。详见 §15 裁定 3。

**关键取舍（5）**：提示词覆盖以符号为稳定键，agent 级附加段继续支持但不等同于符号替换。理由：现有注册器能按 agent 取贡献，但当前消费逻辑只附加文本，无法替换 `prompts.py` 的常量或 builder。

**关键取舍（6）**：provider 表单由插件 schema 驱动，禁止按 provider 名称写死字段。理由：设置页动态选择已合并注册表 provider，字段也须保持同一扩展方式。

**关键取舍（7）**：能力缺失时明确返回不可用，不直连服务兜底。理由：不变量 #19 禁止在工具不可用时直连服务后门；插件化不改变该边界。

## 1. 术语

| 术语 | 本文定义 |
|---|---|
| provider | 通过统一协议提供 LLM、TTS、STT、Embedding、Rerank 等服务实现的贡献项 |
| adapter | 把外部协议或 SDK 调用映射为 engine provider 协议的插件内部实现；不得把业务协议耦合回 engine |
| contribution | 插件向注册表提交的扩展项；当前相关种类包括 `LLM_PROVIDER`、`TTS_PROVIDER`、`STT_PROVIDER`、`PROMPT` |
| capability | Agent 可请求的语义能力名；工具自行声明能力与权限档位。`capability_matrix.TIER_MAP` 必须保持空 |
| `needs_uploader` | TTS provider 的能力标记，表示该 provider 需要上传器；实际消费者、端到端行为待核实 |
| TTS 功能层 | 配音工作流编排、交互与控件描述，不实现特定供应商协议 |
| TTS 协议层 | CosyVoice 鉴权、请求、流式与音频格式等协议适配；不拥有全局 TTS 工作流 UI |
| prompt override | 插件在运行时按既定优先级替换某一提示词符号或 builder 模板的行为；须记录来源并可撤销 |

## 2. 现状与差距

### 2.1 engine 里与 provider 耦合的点清单

以下行号均来自权威事实包；未在事实包中给出行号的内容不补造行号。

| 文件:行号 | 在做什么 |
|---|---|
| `clipwright/plugins/contribution.py:22-41` | 定义冻结的 17 类贡献点，包含 provider 与 prompt 等相关种类 |
| `clipwright/plugins/base.py:74` | `ContributionRegistry.register(plugin_id, items, policy)` 注册贡献项 |
| `clipwright/plugins/contribution_dispatch.py:41,46,49,50` | 处理 PROMPT、LLM_PROVIDER、TTS_PROVIDER、STT_PROVIDER 分发 |
| `clipwright/plugins/contribution_dispatch.py:141` | 分发 PROMPT 贡献 |
| `clipwright/plugins/contribution_dispatch.py:204` | 按 `kind.value` 分支分发 |
| `clipwright/providers/llm/__init__.py:25,28-30` | 注册表已有 `anthropic` 时跳过内置注册；否则注册 `anthropic`、`openai`、`ollama` |
| `clipwright/providers/base.py:16-17,43,72,90,108,126,139` | 定义能力描述及 LLM、Embedding、Rerank、TTS、STT、Renderer 协议 |
| `clipwright/providers/base.py:186,203,221` | 提供 LLM 能力、消息归一化、system 参数适配辅助函数 |
| `clipwright/providers/tts/__init__.py:17,39,52-67` | TTS 别名与内置注册；构造 qwen-tts、cosyvoice、minimax provider |
| `clipwright/providers/tts/__init__.py:32,64` | provider 具备 `needs_uploader` 字段，CosyVoice 标记为需要上传器 |
| `clipwright/providers/stt/__init__.py:52-68` | 注册 whisper 与 faster-whisper 内置实现 |
| `clipwright/services/llm.py:4,19` | 服务说明支持 Anthropic 与 OpenAI 兼容协议；导入 `isobase` 的 AnthropicMessages/OpenAIChat |
| `clipwright/services/llm.py:39,54,63` | 解析角色绑定与 LLM provider，最终通过注册表 resolve |
| `clipwright/services/llm.py:90-107` | 透传 reasoning 并保存最近一次 reasoning |
| `clipwright/services/provider_sources.py:27,34,86` | 定义角色与期望贡献类型，并对密钥打码 |
| `clipwright/api/providers.py:26,30,36,45,81,87,102,111,123` | 提供 provider 源 CRUD、可用列表与角色绑定 API |
| `clipwright/api/settings.py:57,62-68` | 将动态 provider 注册表结果合并进设置下拉选项，避免 Literal choices 遗漏插件 provider |
| `clipwright/services/voice.py`（约 40KB；精确行号待核实） | TTS/语音服务实现；CosyVoice 出现于事实包给出的行 5、8、45、160 |
| `clipwright/api/voice.py`（约 5.9KB；精确行号待核实） | 语音 API；具体端点与协议耦合点待核实 |
| `plugins/voice_ext/plugin.yaml:12` | 现有插件贡献 `tool extended_tts`，不是 TTS_PROVIDER |
| `clipwright/prompts.py`（26 常量、8 builder；精确符号行号待核实） | 集中保存提示词默认值，是唯一提示词文案源 |
| `clipwright/plugins/prompt_registry.py:38,99` | 提示词注册与 `get_for_agent` 查询 |
| `clipwright/plugins/builtin_contributions.py:163,195` | 收集 PROMPT 贡献 |
| `agents/requirements_agent.py`（精确行号待核实） | 将插件提示词作为“插件能力扩展”附加到 system prompt，未替换 `prompts.py` 符号 |

### 2.2 Provider 类型 × 当前实现位置 × 是否已插件化

“已插件化”仅描述是否有插件实现，不能仅因贡献契约存在就判定已有插件实现。

| 类型 | 当前实现位置（事实包） | 是否已插件化 | 差距/说明 |
|---|---|---|---|
| LLM | `clipwright/providers/llm/` 内置注册；`services/llm.py` 使用 `isobase` 协议实现 | 部分：契约与覆盖缝已存在，当前事实包未提供 OpenAI/Anthropic provider 插件 | 新建 `llm_openai_compatible`、`llm_anthropic`；engine 侧退回调度与协议边界 |
| Embedding | `clipwright/providers/base.py:72` 定义协议 | 待核实 | 事实包未提供内置实现位置或插件目录实现清单 |
| Rerank | `clipwright/providers/base.py:90` 定义协议 | 待核实 | 事实包未提供内置实现位置或插件目录实现清单 |
| TTS | `clipwright/providers/tts/__init__.py` 内置 qwen-tts、cosyvoice、minimax；`services/voice.py` | 部分：provider 契约有分发；`voice_ext` 仅贡献工具 | 建设功能层插件与 CosyVoice 协议插件；其余 provider 插件状态待核实 |
| STT | `clipwright/providers/stt/__init__.py:52-68` 内置 whisper、faster-whisper | 待核实 | 有 `STT_PROVIDER` 分发，但现有插件实现清单待核实 |
| 图像 | `plugins/ai_*` 目录与实现 | 待核实是否已在插件目录 | 事实包仅要求列入核查，不给具体目录名或实现事实 |
| 视频 | `plugins/ai_*` 目录与实现 | 待核实是否已在插件目录 | 同上 |
| 音乐 | `plugins/ai_*` 目录与实现 | 待核实是否已在插件目录 | 同上 |

### 2.3 TTS：后端链 + 前端控件

**已知后端链**：`TTS_PROVIDER` 已有分发；内置 provider 注册含 qwen-tts、cosyvoice、minimax；CosyVoice `needs_uploader=True`；`services/voice.py` 与 `api/voice.py` 是当前服务/API；`plugins/voice_ext` 通过 `tool extended_tts` 提供相关能力，而非 provider。准确的请求调用链、协议边界和路由函数待核实。

**已知前端面**：`ProvidersPage.tsx:15-19` 有 `llm / embedding / rerank / tts` 四类 Tab；`DubView.tsx`、`VoicePage.tsx` 涉及 TTS 界面。**本轮已实测**：`DubView.tsx` 181 行 / 2 个控件，`VoicePage.tsx` 431 行 / 3 个控件——所以 TTS 的前端面是薄的，「功能层插件承载大量前端交互」的说法不成立（原 §0 关键取舍 4 已按此改正）。控件矩阵仍需人工复核条件渲染与复用组件。裁定 3 要求「开始创作」页的 TTS 入口也由插件贡献，见 §15.1.4。

| 待核实项 | 建议核实方法 | 产出 |
|---|---|---|
| TTS 前端 input/select/Slider 等控件的准确数量与分类 | 对 `DubView.tsx`、`VoicePage.tsx` 做 AST/组件调用清点，再逐个页面人工复核条件渲染与复用组件 | 控件矩阵：页面、组件、字段、是否随 provider 变化、校验规则 |
| TTS API 到服务到 provider 的真实调用链及错误映射 | 从 `api/voice.py` 路由逐层追踪调用至 `services/voice.py` 和 provider resolve，补调用图与测试覆盖 | 路由—服务—协议—音频结果映射表 |
| `needs_uploader` 的完整消费方及 UI 行为 | 全局搜索字段读写，跟踪上传器请求与对应界面状态 | 字段契约及上传器验收用例 |
| CosyVoice 流式、音频格式和鉴权现状 | 检查真实请求构造、响应解析、依赖与测试；以供应商协议文档核对 | 协议能力清单，不以本文推断协议细节 |

### 2.4 提示词：默认值消费与插件能力

- `clipwright/prompts.py` 集中保存 26 个常量与 8 个 `build_*` 参数化函数；符号消费点清单及每个 agent 的导入路径待核实。全局约束是不搬移、不复制，所有默认值由该模块提供。
- 已有 `PROMPT` 贡献注册、收集与按 agent 查询；现有贡献者包括 kinetic_typography、logic_animations、lottie_animations、sandbox_demo。
- 当前 agent 把插件提示词作为附加段拼入 system prompt，例如 RequirementsAgent 的“插件能力扩展”；插件尚不能按符号覆盖常量，也不能替换 builder 模板。
- 缺口分两类：符号覆盖（常量键、目标 agent、来源）与参数化模板覆盖（模板键、参数 schema、占位符一致性、渲染后验证）。实际 builder 占位符集合待核实，W5 必须从运行时/AST 提取作为契约测试基线。

## 3. 目标与非目标

**目标**

1. LLM provider 协议实现从 engine 移至插件，engine 保留注册、调度、回退与角色绑定。
2. 以既有 provider 协议和贡献种类承载 OpenAI 兼容、Anthropic、TTS 与其他 provider 插件。
3. TTS 功能层与 CosyVoice 协议层解耦，前端控件由插件描述驱动。
4. 插件按提示词符号覆盖常量或 builder，保留 `prompts.py` 默认值与既定覆盖顺序。
5. provider 配置表单 schema 驱动，敏感字段经既有加密通道保存，界面不暴露密钥明文。
6. 存量 provider sources 与角色绑定迁移时不丢失，旧前端能处理新增字段。

**非目标**

1. 不新增第 18 个贡献点，除非契约评审明确批准。
2. 不重写 `services/voice.py` 全部业务，不在 W3 顺带重做配音产品流程。
3. 不重写或润色 `prompts.py` 文案；既有搬运逐字不变。
4. 不在 agent 中引入 provider 名称判断或工具名硬编码；Agent 仍只使用语义能力名。
5. 不实现图像、视频、音乐 provider 的新协议，W4 先盘点并迁移可复用实现，范围待 W1 清点确认。
6. 不提供 demo 插件。
7. 不允许 provider 或 prompt 插件绕过权限策略、密钥加密或审计通道。
8. 不允许能力缺失时直连服务作为后门；不可用状态必须可观察。

## 4. 插件契约扩展（本 PRD 地基）

### 4.1 贡献种类决策

复用 `LLM_PROVIDER`、`TTS_PROVIDER`、`STT_PROVIDER` 与 `PROMPT`。当前已有注册与分发分支。`ContributionKind` 名单冻结；如果 W1 契约验证发现 provider schema、TTS UI 描述或 prompt 符号覆盖无法由现有贡献 payload 表达，再提交独立契约评审，未经评审不得加 kind。

### 4.2 Provider 能力接口

统一注册元数据与运行实例分离。建议接口签名级草案如下，字段类型与兼容策略在 W1 以现有 `ProviderCapabilities` 核实后定稿：

```python
class ProviderManifest(Protocol):
    provider_id: str
    kind: ContributionKind
    config_schema: dict[str, object]
    capabilities: ProviderCapabilities
    def create(self, config: Mapping[str, object]) -> ProviderProtocol: ...
```

契约能力字段至少覆盖：模型清单获取（静态或动态、失败行为）、参数 schema、流式支持、工具调用、reasoning 映射、计费/usage 上报。字段是否必填、模型发现 API、计费单位与 token 口径均待核实；W1 必须逐项给出类型、默认值、版本兼容策略。协议不支持的能力显式标为 false/unsupported，不伪造成功。

### 4.3 同名冲突规则

1. provider key 在同一 `ContributionKind` 下唯一；大小写与别名归一规则待核实。
2. 注册表有同名插件实现时，跳过同名内置注册；插件贡献解析为唯一实现。这是对现有“注册表非空则跳过内置”的明确化。
3. 同一 key 多个插件同时注册时拒绝后注册项，写入冲突审计事件并使冲突插件该贡献不可用；不采用不确定的加载顺序优先级。
4. 插件被禁用/卸载后，重新注册兼容内置实现；若无内置实现则 provider 显示 unavailable，不得直连服务。
5. 覆盖内置 provider 的插件必须在 UI/日志暴露 provider id、插件 id 与版本；具体审计字段待核实。

### 4.4 启停、热插拔、隔离与审计

沿用现有插件治理能力（启停持久化、错误通道、reload 回滚、配置迁移、secret 加密与审计日志；具体 API/字段待核实）。provider 启停后新请求使用新注册表快照；进行中的请求不强制切换实例。热插拔对进行中流的取消/排空策略待核实，W1 明确超时和资源释放语义。插件加载、schema 校验、实例创建、请求错误分别隔离，错误不得使其他 provider 或应用启动失败。审计至少覆盖注册/冲突/启停/配置更新/密钥轮换/调用失败，精确事件结构待核实。

### 4.5 密钥与 schema 驱动

插件声明字段 schema：类型、标签、默认值、必填、敏感标记、验证规则及可见条件。字段规范需 W1 定稿；前端按 schema 渲染，不能按 provider 名称写死字段。敏感字段提交至 `settings_store` 已有 Fernet 加密通道；GET 响应只返回是否已配置/掩码，不返回明文。前端字段组件能力、schema 版本兼容与校验表达式待核实。

## 5. Provider 插件化方案

### 5.1 OpenAI 兼容与 Anthropic 插件

**落地进度（2026-11-02，Batch 2）**：`plugins/llm_openai_compatible/` 与 `plugins/llm_anthropic/` 已建立并接管注册与声明（`config_schema` / `capabilities`），实测三个 id 的归属都从 `__builtin__` 切到插件；`/api/providers/available` 无回归。**协议适配类暂留 engine**（`providers/llm/*.py`，仅 68/55 行且只依赖 `providers.base` + 传输层）：一次性搬类会打断既有测试对 `clipwright.services.llm.OpenAIChat` 的打桩链，按 §5.3「迁移阶段保留内置实现作为 fallback」分两步走 —— 搬类属于 W2 收尾项，不是遗漏。

**顺序陷阱（实测）**：lifespan 先注册内置（`main.py:161-199`）再加载插件（`:249`），所以插件接管必须依赖 `override_builtin: true`（由 `plugins/base.py:92` 切成 `ConflictPolicy.OVERRIDE`）；反向顺序则由 `providers/builtin_registry.py` 的**逐 id 让位**兜住。两条机制缺一不可，详见台账 Batch 2 一节。

创建 `plugins/llm_openai_compatible/`，配置 schema 至少表达 base URL、鉴权引用、模型/请求参数与 reasoning 映射；具体字段名、缺省值、是否允许无鉴权由 W1 核实后确定。插件对接既有 LLMProvider 协议，处理 OpenAI Chat 请求/响应，并把流式、工具调用与 reasoning 映射为统一结果。reasoning 格式差异、供应商扩展头与错误码映射待核实。

创建 `plugins/llm_anthropic/`，映射 Anthropic Messages 协议与统一 LLMProvider；system 参数、工具调用、流式事件与 reasoning 映射沿用协议测试，不在 engine 留协议分支。

既有内置 `openai`、`anthropic`、`ollama` 是否全部由插件迁出：W2 首先迁 OpenAI 兼容与 Anthropic；Ollama 迁移归 W4，避免超出明确需求。插件 id 与旧 provider key 的映射表在 W2 迁移前核实。

### 5.2 Engine 职责与 isobase

engine 保留注册表查询、统一协议调度、角色绑定、回退、请求生命周期、超时/取消与结果归一化；`services/llm.py` 不再构造或持有特定协议客户端。`_resolve_llm_provider` 作为注册表解析入口保留，具体函数边界与调用方待核实。

`isobase` 处理策略：W1 检查依赖版本、可注入性及现有测试；W2 优先由插件继续使用 `isobase`，engine 抽象只依赖 `LLMProvider` 协议。若 `isobase` 对插件构建或依赖隔离不兼容，则插件内增加适配层并评估依赖迁移；不得在未验证前承诺删除或升级。兼容性与版本锁定待核实。

### 5.3 内置 fallback 与迁移

迁移阶段保留内置实现作为插件未安装/禁用时的 fallback，保持 provider key 与角色绑定可解析；当同名插件安装时按 4.3 规则覆盖内置。完成回归、配置迁移与发布观察期后，是否删除内置实现由产品与维护者按安装覆盖率决定，当前不删除。旧前端忽略未知 schema 字段仍可读已有 provider 记录；API 字段兼容性待核实。

## 6. TTS 双插件方案

> **本章已被 §15 裁定 3 修订**：原方案默认「engine 保留 TTS 编排、只把 provider 协议插件化」，现改为「engine 不带任何默认 TTS 能力（含 `/dub` 编排）」，TTS 是主 agent 经能力解析发现并调用的插件能力，前端创作页入口由功能插件贡献。下文 6.1–6.3 的接口与控件细节仍然有效，凡与裁定冲突处以 §15 为准。

### 6.1 边界与接口

| 插件 | 负责 | 不负责 |
|---|---|---|
| TTS 功能支持插件 | 工作流编排、功能入口、表单/控件描述、任务状态与音频结果接入；依赖 `TTS_PROVIDER` 选择协议实现 | 不内嵌 CosyVoice 鉴权与请求格式，不直连 `services/voice.py` 绕过 provider |
| CosyVoice 协议插件 | CosyVoice 鉴权、请求构造、流式响应解析、音频格式转换/元数据声明、`needs_uploader` 行为契约 | 不拥有通用 TTS 工作流与整套配音页面 |

签名级接口草案：

```python
class TTSProvider(Protocol):
    capabilities: ProviderCapabilities
    needs_uploader: bool
    async def synthesize(self, request: TTSRequest) -> TTSResult: ...
```

`TTSRequest/Result` 当前字段与流协议待核实；功能层向前端输出 schema 化控件描述，后端接收规范化请求并解析 provider 结果。功能层如何注册 UI Slot、前端插件渲染权限与控件 DSL 待核实；不新增贡献种类，先评估现有 UI_SLOT 与 TTS_PROVIDER 组合能否承载，不能则提交契约评审。

### 6.2 前端控件描述

控件描述采用版本化 schema：`id`、`type`、`label`、`default`、`required`、`options`/`constraints`、`visibleWhen`、`providerScope`。真实字段名、支持控件种类、表达式安全边界待核实。前端映射到已有 input/select/checkbox/slider 控件；插件不得提交可执行脚本或任意 HTML。具体现有组件能力与可复用表单 renderer 待核实。

### 6.3 错误传播与降级

协议插件错误必须保留 provider id、阶段（鉴权/请求/流式/解码）、可重试性和用户可读错误码；敏感响应/密钥不进入日志。功能层区分 provider unavailable、配置缺失、上传器缺失、合成失败与取消。CosyVoice 不可用时仅回退到角色已配置且能力匹配的其他 TTS provider；无可用 provider 时明确报错，不调用服务层后门。错误码与自动重试次数待核实，默认不自动重试非幂等请求。

## 7. 提示词覆盖插件方案

> **本章已被 §15 裁定 4 修订**：原方案只覆盖 3 个低风险 `build_*`，现改为**全部 8 个 `build_*` 与 26 个常量符号都可被插件替换**，并且**不提供任何 demo 插件**——只交付能力入口与校验。下文 7.1–7.3 的粒度、校验与回滚规则继续有效。

### 7.1 覆盖粒度

1. agent 级：沿用 `get_for_agent(agent_id)` 的附加贡献语义；若声明为完整替换，须使用明确模式字段，不能把附加误作替换。
2. 单条常量：插件以 `prompt_symbol` 精确指向 `prompts.py` 导出的符号，提交完整替代文本。
3. `build_*` 参数化模板：插件覆盖模板正文/渲染器契约，参数名与类型必须与默认 builder 一致；禁止插件接收任意执行代码。
4. 符号清单需从 `prompts.py` 自动发现并在注册时校验；26 常量与 8 builder 是当前基线，后续变化以运行时清单为准。

### 7.2 校验、优先级与冲突

- builder 覆盖时比较占位符集合及参数类型；缺失、多余或类型不匹配一律拒绝该覆盖，并返回符号名、期望集合与实际集合。格式化语法与嵌套占位符规则待核实。
- 同一符号多个插件覆盖时不按加载顺序竞争：默认拒绝冲突并报告插件 id；用户显式选择唯一生效插件后才接受。是否支持管理员优先级字段由产品拍板。
- 最终覆盖顺序严格为 `prompts.py < Category < Persona < Skill < 插件`。同层多个插件冲突时按上述拒绝规则，不引入隐式优先级。
  > **本条的「五层顺序」措辞已被 §15.2.5 更正**：代码里 Category / Persona / Skill **没有文本覆盖通道**，它们只通过 builder 的关键字参数（`category_hint` / `persona_style` / `brief_hint`）注入参数；真正存在的是「`prompts.py` 默认 + 插件 agent 级 append（按 priority）+ 裁定 4 新增的符号级 replace」。
- `prompts.py` 始终是默认值与唯一文案基线；插件覆盖作为运行时下游层，不读取业务配置写回 prompts 模块。

### 7.3 来源、禁用与回滚

最终 prompt 组装结果保留每段来源元数据（默认模块、Category、Persona、Skill、plugin id/version、符号名、覆盖/附加模式）；可在诊断 UI 或结构化日志查看，不展示敏感上下文。UI 落点待核实。禁用插件后立即采用下一优先层/`prompts.py` 默认值；回滚记录插件版本与符号，保留审计。提示词内容审查、差异预览与回滚权限待核实。

## 8. 数据与接口契约增量

所有字段均为方案草案；事实包未确认的类型、默认值标「待核实」，W1/W2 定稿前不得视为现存实现。

| 对象 | 字段（类型；默认值） | 用途/状态 |
|---|---|---|
| Provider manifest | `provider_id: string`；无默认值 | 注册唯一键；现有 manifest 是否支持待核实 |
| Provider manifest | `kind: ContributionKind`；无默认值 | 复用既有贡献种类 |
| Provider manifest | `config_schema: object`；`{}` | **已落地（B1-T2）**：`ContributionDecl.config_schema` 一级字段（`schema/plugin.py`）。`{字段名: {type, label, default, required, options, secret}}`；`secret=true` 由服务端加密 + 掩码回显。校验器 `plugins/provider_decl.py:validate_declaration()` 只做结构校验，不猜业务语义 |
| Provider manifest | `capabilities: object`；按协议声明 | **已落地（B1-T2）**：`ContributionDecl.capabilities` 一级字段。已知键 `models/stream/tools/reasoning/usage/needs_uploader`（`PROVIDER_CAPABILITY_KEYS`）；**未知键不拒绝**，记录在 `unknown_capability_keys` 里以便后续收敛 |
| Provider source | `provider_id: string`；旧记录迁移填旧 key | 将配置源与实现解耦；存储模型待核实 |
| Provider source | `plugin_id: string|null`；`null` 表示 builtin | 来源可见性与审计；字段待核实 |
| Provider source | `config: object`；旧字段迁移 | 敏感值走 Fernet；具体 schema 待核实 |
| TTS UI descriptor | `schema_version: integer`；`1` | 控件 DSL 版本；需契约评审/实现验证 |
| TTS UI descriptor | `controls: array<object>`；`[]` | 功能插件控件列表；字段待核实 |
| Prompt contribution | `target: string`；必填 | `agent:<id>` 或 `symbol:<name>`；编码格式待核实 |
| Prompt contribution | `mode: enum(append|replace)`；`append` | 区分 agent 附加与符号替换；现有贡献字段待核实 |
| Prompt contribution | `template: string`；无默认值 | 文本或 builder 模板；占位符约束待核实 |
| Prompt contribution | `source_plugin: string`；注册时生成 | 来源追踪；现有审计字段待核实 |
| Prompt contribution（裁定 4 新增） | `builder_signature: object`；注册时由 `inspect.signature` 自动填 | builder 覆盖的签名契约；不一致即拒绝 |
| Prompt contribution（裁定 4 新增） | `dry_render_samples: array<object>`；`[{},{}]` 两组固定样例 | 双次 dry-render 校验「参数是否真被用上」 |
| Capability gateway（裁定 3 新增，**路线已定 B**） | 端点 `POST /api/capability/{name}`；`name` = 能力名；body `{params}`；响应 `ToolExecResult` | 插件对外 HTTP 入口的唯一通道（§15.1.5）；状态码 404/422/503/200 见契约表；不新增 `API_ROUTE` 贡献种类 |
| TTS capability | 能力名 **`dub`**（既有，`SKILL_CAPABILITY_ALIASES["dub"]=("dub_script",)`）；由功能插件贡献同名技能 | 解析不到时 agent 如实标注「未配音」，不是异常；**不新造** `tts_dub` / `tts_synthesize`（见 §15.1.3） |
| UI slot contribution（裁定 3 新增，**第六轮定名**） | `slot: "create.dub"`（名单 14 → 15）；沿用既有 `SlotContribution` 字段：`id` / `title` / `order`(默认 100) / `icon`(白名单) / `visibleWhen` / `layout.widgets` | 「开始创作」向导 `03 配音与字幕` 步骤的内容由功能插件贡献；host 已在 `usePluginSlots.ts`，本次只加 slot 名与 HomePage 挂载点（落地清单见 §15.9）；**不新增贡献种类、不新增 slot 字段** |

API 增量原则：优先扩展 `/api/providers` 现有 list/create/available/roles/source CRUD，不新建平行 provider API。**已新增** `GET /api/providers/schemas[?kind=]`（B1-T5，下发插件声明的 `config_schema`/`capabilities`，是 schema 驱动表单的唯一数据源；内置 provider 不在此列）。**已新增** `POST /api/capability/{name}`（裁定 3 路线 B，见 §15.1.5）。拟新增 schema 查询端点 `GET /api/providers/schemas`、prompt 符号清单/来源诊断端点、以及裁定 3 的通用能力网关 `POST /api/capability/{name}`；只有既有端点不能表达时才新增。端点权限、响应结构与版本兼容待核实。

前端 `ProviderKind` 扩展 STT 的必要性待核实：现有供应商页事实清单只有 llm/embedding/rerank/tts 四 Tab；STT 是否加入 Tab 由产品确认。store 增加 `providerSchemas`、source metadata；组件 props 传 `schema`、`values`、`onChange`、`secretConfigured`。这些具体名称是方案字段，不代表当前代码已有实现；store 文件与组件落点待核实。

## 9. 迁移与兼容

1. 迁移前导出 `provider_sources` 的 `sources` 与 `roles` 快照；现有字段结构、备份机制待核实。
2. 保持旧 provider key、source id 与角色绑定可解析；新旧 `provider_id` 映射显式存储，不以展示名推断。
3. 密钥不明文导出、不写日志、不重置；迁移通过 `settings_store` 已有 Fernet 加密通道读写，迁移前后用掩码/配置状态核验，禁止用明文比对日志。
4. 新插件未安装时继续使用内置 fallback；插件安装并注册成功才覆盖同名实现；加载失败不删除存量 source。
5. 旧前端只收到兼容字段；新增字段可选，旧客户端忽略未知字段不崩。现有 API 是否使用严格响应模型待核实。
6. 迁移失败按单 source 原子回滚；不允许部分写入后覆盖原配置。事务/存储原子能力待核实。
7. OpenAI/Anthropic 源迁移由 W2 执行；TTS 源迁移由 W3 执行；其他类型由 W4 清点后分批迁移。

## 10. WBS

规模/人日为规划估算，不是事实包数据；按单工程师有效工作日估算，含实现与针对性验证，不含等待评审。总计 32 项任务、约 47 人日。具体依赖真实代码结构，待核实项在任务启动时确认。

> **本节 32 项 / 47 人日已被 §15 裁定 3/4 修订**：W3 增至 8 项、W5 增至 8 项，任务级口径总计 **42 项 / 51.5 人日**（编码计划 §10 与 §15.4 一致）。凡本节与 §15.4 冲突处，以 §15.4 为准。

### W1 契约与注册表（7 项，约 10 人日）

| # | 目标 | 涉及文件与函数（仅事实包行号） | 前置依赖 | 人工验收点（现象级） | 规模 | 风险 |
|---|---|---|---|---|---|---|
| 1 | 盘点现有 provider 协议与注册调用 | `providers/base.py:16-17,43,72,90,108,126,139`；注册调用点待核实 | 无 | 各类型均有协议、能力与 resolve 清单，缺口明确 | M | 中 |
| 2 | 明确 manifest/schema 版本与字段 | `contribution.py:22-41`；`base.py:74` | 任务 1 | 插件作者能仅凭 schema 配置一个测试 provider | L | 高 |
| 3 | 固化同名冲突及内置跳过规则 | `providers/llm/__init__.py:25,28-30`；dispatch `:204` | 任务 1 | 同名插件唯一生效；重复插件得到可见冲突，不影响其他 provider | M | 高 |
| 4 | 定义密钥引用与 schema 驱动 UI 契约 | `provider_sources.py:86`；`api/providers.py:30-123`；settings encryption 落点待核实 | 任务 2 | 保存后再打开只显示已配置/掩码，不返回密钥明文 | M | 高 |
| 5 | 定义启停、热插拔与请求生命周期 | `contribution_dispatch.py:41-50`；插件治理函数待核实 | 任务 3 | 禁用后新请求不再选中插件；在途请求行为有明确可观察结果 | M | 高 |
| 6 | 建立插件失败隔离与审计事件契约 | 插件错误通道/审计落点待核实；`base.py:74` | 任务 2 | 一个坏插件失败后应用与其他 provider 仍可用，审计能定位插件 | M | 高 |
| 7 | 固定协议能力测试矩阵 | `providers/base.py:186,203,221`；测试文件待核实 | 任务 1-6 | 流式、工具调用、reasoning、usage 不支持时明确显示，不伪造 | M | 中 |

### W2 OpenAI 兼容插件（6 项，约 9 人日）

| # | 目标 | 涉及文件与函数（仅事实包行号） | 前置依赖 | 人工验收点（现象级） | 规模 | 风险 |
|---|---|---|---|---|---|---|
| 1 | 抽出 `services/llm.py` 协议客户端耦合 | `services/llm.py:4,19,54,63` | W1 完成 | engine 服务不再直接构造特定协议客户端 | L | 高 |
| 2 | 实现 OpenAI-compatible 插件配置与 adapter | 新建 `plugins/llm_openai_compatible/`；协议 `providers/base.py:43` | W1 完成 | 配置 base URL/鉴权后可完成一次对话 | L | 高 |
| 3 | 映射 streaming、tools、reasoning 与 usage | `services/llm.py:90-107`；`providers/base.py:186-221` | 任务 2 | 支持项正常工作，不支持项可见失败或降级 | L | 高 |
| 4 | 迁移 Anthropic 到独立插件 | 新建 `plugins/llm_anthropic/`；`services/llm.py:19,54` | W1 完成 | 切换插件实现后 Anthropic 会话仍可用，engine 无协议专属分支 | L | 高 |
| 5 | 兼容旧 sources 与 role bindings | `provider_sources.py:27,34`；`api/providers.py:81-123` | 任务 2、4 | 升级前配置与角色无需重新输入且可正常调用 | M | 高 |
| 6 | 驱动供应商表单并做旧前端兼容 | `ProvidersPage.tsx:15-19`；客户端 API `providers.ts` | W1 完成 | 新字段由插件 schema 出现；旧客户端仍能查看 provider 列表 | M | 中 |

### W3 TTS 双插件（原 6 项，约 10 人日；**已被 §15 修订为 8 项 / 14 人日**）

> 本 WBS 缺三项（engine 摘除默认 TTS 编排与注册、能力发现与缺失一等状态、插件 HTTP 入口），详见 §15.1 与 §15.4。下表任务仍有效，但**验收以 §15.3 为准**。

| # | 目标 | 涉及文件与函数（仅事实包行号） | 前置依赖 | 人工验收点（现象级） | 规模 | 风险 |
|---|---|---|---|---|---|---|
| 1 | 梳理 voice API/service/provider 调用链与控件 | `services/voice.py`、`api/voice.py`、`DubView.tsx`、`VoicePage.tsx`；精确行号待核实 | W1 完成 | 控件和请求字段清单可逐项对应到后端 | M | 高 |
| 2 | 抽离 TTS 工作流功能层插件 | `TTS_PROVIDER` dispatch `contribution_dispatch.py:46`；插件 UI 接入点待核实 | W1 完成 | TTS 入口来自功能插件；无该插件时入口不可用且说明原因 | L | 高 |
| 3 | 定稿控件 schema 与前端 renderer | `ProvidersPage.tsx:15-19`；DubView/VoicePage 控件待核实 | 任务 1、W1 完成 | 插件控件配置变化后 UI 无需新增 provider 专属硬编码 | L | 高 |
| 4 | 抽离 CosyVoice 协议插件 | `providers/tts/__init__.py:39,52-67`；`services/voice.py:5,8,45,160` | W1 完成 | CosyVoice 插件可单独启停，其他 TTS provider 不受影响 | L | 高 |
| 5 | 定义 `needs_uploader` 与音频结果契约 | `providers/tts/__init__.py:32,64`；`providers/base.py:108` | 任务 1、4 | 需要上传器时 UI 明确出现对应入口；未提供上传器时给明确提示 | M | 高 |
| 6 | 验证错误传播、取消与 provider 降级 | `api/voice.py`、`services/voice.py`；精确函数待核实 | 任务 2-5 | CosyVoice 失败后只有配置了兼容 provider 才回退，否则显示原因 | M | 高 |

### W4 其他 provider 类型（6 项，约 8 人日）

| # | 目标 | 涉及文件与函数（仅事实包行号） | 前置依赖 | 人工验收点（现象级） | 规模 | 风险 |
|---|---|---|---|---|---|---|
| 1 | 清点 Embedding/Rerank/STT 实现与插件目录 | `providers/base.py:72,90,126`；STT `providers/stt/__init__.py:52-68` | W1 完成 | 每种实现都有 owner、协议、配置、插件状态清单 | M | 中 |
| 2 | 将 STT 内置实现迁移为插件并保留 fallback | `providers/stt/__init__.py:52-68`；STT dispatch `contribution_dispatch.py:50` | 任务 1 | 插件禁用与缺失时行为明确，其他 provider 不受影响 | M | 高 |
| 3 | 迁移 Embedding provider | `providers/base.py:72`；实现文件待核实 | 任务 1 | embedding 角色能解析插件，原索引流程可用 | L | 高 |
| 4 | 迁移 Rerank provider | `providers/base.py:90`；实现文件待核实 | 任务 1 | rerank 角色可选择插件，缺失时不静默伪装成功 | M | 中 |
| 5 | 核实图像/视频/音乐 `plugins/ai_*` 归属 | `plugins/ai_*` 路径与实现待核实 | 任务 1 | 三类清单明确哪些已插件化，哪些不在本期 | S | 低 |
| 6 | 同步 provider Tab 与动态 choices | `ProvidersPage.tsx:15-19`；`settings.py:57,62-68` | 任务 1-4 | UI 只展示可用类型与已注册实现，选择项不漏插件 provider | M | 中 |

### W5 提示词覆盖插件（原 7 项，约 10 人日；**已被 §15 修订为 8 项 / 12 人日**）

> 覆盖范围已由「部分 builder」扩到**全部 8 个 `build_*` + 26 常量**，并新增 builder 硬契约校验（签名 / 占位符 / 双 dry-render / 纯度）与「不提供 demo 插件、只交付能力入口」的交付要求，详见 §15.2 与 §15.4。**验收以 §15.4 的 11 条为准**。

| # | 目标 | 涉及文件与函数（仅事实包行号） | 前置依赖 | 人工验收点（现象级） | 规模 | 风险 |
|---|---|---|---|---|---|---|
| 1 | 盘点 26 常量、8 builder 的消费点与占位符 | `clipwright/prompts.py`、各 agent 消费点待核实 | 可独立；依赖提示词基线 | 每个符号能定位消费处与默认值，builder 参数可机器校验 | M | 中 |
| 2 | 扩展 PROMPT 贡献为符号目标 | `prompt_registry.py:38,99`；dispatch `:41,141` | 任务 1 | 插件能声明常量符号或 builder 目标，未知符号被拒绝 | M | 高 |
| 3 | 实现 builder 占位符校验 | `prompts.py` builder；具体语法待核实 | 任务 1、2 | 少一个或多一个占位符时注册失败并报告差异 | M | 高 |
| 4 | 接入最终 prompt 组装与既有顺序 | RequirementsAgent 获取逻辑行号待核实；`builtin_contributions.py:163,195` | 任务 2、3 | 五层顺序固定，默认值与既有输出保持一致 | L | 高 |
| 5 | 实现来源诊断与冲突报告 | `prompt_registry.py:99`；UI/日志落点待核实 | 任务 2 | 用户能看到生效符号、来源插件与版本 | M | 中 |
| 6 | 实现禁用回滚与无插件默认值 | `prompts.py`；`ContributionRegistry` `base.py:74` | 任务 4、5 | 禁用覆盖插件后恢复默认或下一层，业务不中断 | M | 高 |
| 7 | 运行时 prompt 快照回归 | `prompts.py` 26 常量 + 8 builder；测试路径待核实 | 任务 4、6 | 无覆盖时动态发出的 prompt 与基线完全一致 | M | 高 |

## 11. 人工验收清单

### W1 契约与注册表

| 操作 | 期望现象 |
|---|---|
| 安装两个声明同 provider key 的插件 | 后注册项被拒绝，冲突可见；应用仍能启动，其他 provider 可用 |
| 安装与内置同 key 的 provider 插件 | 插件唯一生效；禁用后兼容内置重新生效 |
| 保存包含密钥的 provider 配置，再打开编辑页 | 仅显示已配置/掩码，API 与日志无明文 |
| 禁用一个正在被使用的 provider 插件 | 新请求不再解析到该插件；在途请求按 W1 确认的生命周期完成或明确失败 |

### W2 OpenAI/Anthropic

| 操作 | 期望现象 |
|---|---|
| 创建 OpenAI-compatible 源并发起对话 | base URL 与鉴权生效，结果由插件返回 |
| 请求流式输出与工具调用 | 插件声明支持时正常工作；不支持时有明确错误，不静默丢失 |
| 请求 reasoning 并查看诊断信息 | reasoning 映射可见且与普通正文分离；确切 UI 形式待核实 |
| 移除 Anthropic 插件但保留旧源 | 内置 fallback 继续工作，角色绑定不丢 |

### W3 TTS

> 下表是原方案验收项；**以 §15 修订后的 W3 验收为准**（新增「engine 无默认 TTS 编排」「主 agent 能力发现」「前端创作页入口由插件贡献」「未装插件时明确不可用」四类断言）。

| 操作 | 期望现象 |
|---|---|
| 启用 TTS 功能插件 | 工作流入口与控件出现，控件由 schema 决定 |
| 禁用 CosyVoice 协议插件 | CosyVoice 不再可选，其他已配置 TTS provider 仍可使用 |
| 配置 `needs_uploader` provider 并开始配音 | 需要上传器时显示匹配入口；未提供上传器时给出明确阻断原因 |
| 触发协议错误或流中断 | 用户看到可理解错误，日志有阶段与 provider 来源且无密钥 |

### W4 其他 provider 类型

| 操作 | 期望现象 |
|---|---|
| 分别选择 STT、Embedding、Rerank provider | 角色与贡献类型匹配，配置项来自注册表 |
| 禁用对应插件再执行任务 | 能力显示 unavailable；不通过服务直连继续执行 |
| 查看图像/视频/音乐 provider 清单 | 每类是否由 `plugins/ai_*` 提供有核验结论，不以目录猜测实现状态 |

### W5 提示词覆盖

> 下表是原方案验收项；**以 §15 修订后的 W5 验收为准**（覆盖范围由 3 个 `build_*` 扩到全部 8 个 + 26 个常量符号）。

| 操作 | 期望现象 |
|---|---|
| 不安装 prompt 覆盖插件运行真实路径 | prompt 与 `prompts.py` 基线一致 |
| 插件覆盖一个常量符号 | 仅目标符号发生变化，诊断能显示插件 id/version |
| 插件提交占位符不匹配的 builder | 注册/启用失败并显示期望与实际占位符，不影响默认 builder |
| 同符号由两个插件覆盖 | 冲突明确，不能依赖加载顺序决定结果 |
| 禁用覆盖插件 | 输出恢复下一优先层，最终回退到 `prompts.py` |

## 12. 风险与回滚

| 风险 | 影响 | 控制与回滚 |
|---|---|---|
| 插件坏实现拖垮主链路 | 启动失败或所有 provider 请求失败 | 插件加载/调用隔离；失败时停用该贡献并恢复内置 fallback；保留错误审计 |
| 密钥泄漏 | 凭据暴露 | 只走 Fernet 通道；GET 掩码；日志与异常脱敏；发现泄露立即轮换凭据并回滚到上个加密配置 |
| 存量源或角色丢失 | 用户需重新配置、任务失败 | 迁移前快照；source 逐条原子迁移；旧 key 显式映射；失败回滚单条并保留原记录 |
| 提示词被错误覆盖 | 输出质量或安全约束变化 | 占位符校验、来源可见、插件版本审计；禁用即恢复默认/下一层；保留 prompt 快照比对 |
| 同名覆盖冲突 | 运行实现不确定 | 同 key 多插件拒绝；同名插件对 builtin 明确优先；冲突 UI 与日志可见 |
| `isobase` 升级不兼容 | 插件请求或流式行为回归 | 先锁定现有版本与协议测试；插件内隔离依赖；升级单独验证；失败回滚锁文件/插件版本 |
| TTS 控件 schema 不兼容 | 页面崩溃或配置无法编辑 | schema 版本校验、未知控件拒绝并显示错误；旧配置保留，不自动覆盖 |
| 热插拔与在途请求竞态 | 流中断或资源泄漏 | 新请求采用新快照，在途策略 W1 定稿；失败时恢复上一注册快照 |
| 未支持能力被错误宣称 | 工具调用/reasoning/usage 数据错误 | capability 明确声明，协议测试校验；不支持即暴露 unsupported，不伪造结果 |
| **通用能力网关变成新后门**（路线 B） | 有人为省事在网关里写具体能力名分支、或让它承载编排 | 守则：网关内不得出现任何具体 provider/能力名分支；`/api/voice/*` 只转发；按 404/422/503/200 四类状态码验收（PRD §15.1.5、编码计划守则 ⑥） |
| **新增 slot 漏改名单/文档**（第六轮裁定 C） | `create.dub` 只加在 `slots.ts` 或只加在文档，前端判非法丢贡献 / 评审失去依据 | 落地清单 §15.9 八项逐项打勾：`SlotName` 类型、`SLOT_NAMES`、D-0 名单表、HomePage 挂 host、`stepDone.dub`、`localStorage` 偏好迁移、空态、`frontend-backend-parity.md` |
| **`create.dub` 落地时残留旧硬编码**（裁定 C） | 插件控件与 HomePage 原硬编码块同时出现，用户看到两套配音表单 | HomePage `:1040-1195` 原控件块必须删除；人工在无插件/有插件两种状态下各看一遍 03 步骤 |

## 13. 分期与依赖

| 阶段 | 内容 | 依赖/退出条件 |
|---|---|---|
| W1 契约与注册表 | 固化 schema、冲突、生命周期、密钥、隔离、审计与测试矩阵 | W2/W3 必须等待 W1 契约稳定并通过人工验收 |
| W2 OpenAI 兼容插件 | 迁移 OpenAI-compatible 与 Anthropic，实现来源兼容及 schema 表单 | 依赖 W1；通过流式、工具调用、reasoning 与迁移验收 |
| W3 TTS 双插件 | 功能层与 CosyVoice 协议层拆分，接入控件 schema | 依赖 W1；先完成控件盘点与调用链确认 |
| W4 其他 provider 类型 | 盘点并迁移 STT、Embedding、Rerank；核实图像/视频/音乐插件 | 依赖 W1；W2/W3 契约产物可复用，不要求 W2/W3 全部完成 |
| W5 提示词覆盖插件 | agent/符号/builder 覆盖、校验、来源与回滚 | 可独立于 W2/W3；依赖现有 `PROMPT` 契约和提示词消费点盘点 |

**关键路径估算**：W1（10 人日）→ W2（9 人日）与 W3（10 人日）并行 → W4（8 人日）；W5（10 人日）可与 W2/W3 并行。总工作量约 47 人日；关键路径按 W1 + max(W2,W3) + W4 约 27 人日，不含评审等待。估算依据是 WBS 任务数与 S/M/L 粗估；真实代码落点待核实后校准。

> **本节估算已被 §15 裁定 3/4 改高**：W3 与 W5 的验收范围都扩大了（engine 摘除默认 TTS、提示词覆盖扩到全部 `build_*`）。另外需要说明：本节是**粗估口径**，编码计划 §10 是**任务级口径**，两者在本轮修订前就已不一致（47 人日 vs 38.5 人日）。修订后一律以**编码计划的任务级口径为准**，见 §15.4。

## 14. 开放问题

1. 产品确认同名插件是否始终覆盖 builtin，还是允许管理员显式选择 builtin；本 PRD 按既有注册语义裁定插件优先。
2. 产品确认 prompt 同符号多插件冲突采用“拒绝冲突”还是提供管理员优先级；本 PRD 默认拒绝。
3. 产品确认 STT 是否进入供应商页 Tab；当前事实只列出 llm/embedding/rerank/tts 四类。
4. 产品确认 prompt 来源诊断入口放在 Provider/插件管理 UI、Agent 调试日志还是两处都做；具体 UI 落点待核实。
5. 维护者确认 `isobase` 当前锁定版本、插件依赖隔离方式及 Anthropic/OpenAI reasoning 兼容范围；事实包未提供这些信息。

## 15. 裁定更新（2026-11-02 晚）

本节是**后置裁定**，优先级高于 §6、§7、§8、§11、§13 的原方案。两条裁定由需求方直接给出，不再作为开放问题等待产品确认。

裁定原文：

> 3. engine 不默认带 TTS 能力，包括编排，是主 agent 发现用户需要 TTS 之后再调用特定的工具和能力实现 tts。请注意，前端开始创作的页面也有 tts 的入口，这个也需要由 tts 插件实现。
> 4. 所有的 `build_*` 也允许通过插件替换（不要创建 demo 插件，只提供能力入口）。

### 15.1 裁定 3：engine 默认不带任何 TTS 能力

#### 15.1.1 现状（代码事实）

| 事实 | 位置 | 含义 |
|---|---|---|
| TTS provider 抽象基类 `BaseVoiceProvider`（`clone` / `synthesize`） | `services/voice.py:347,368,379` | 协议层已有一层薄抽象 |
| 具体实现 `QwenTTSProvider` 与 engine 同居一个模块 | `services/voice.py:390` | 协议实现没有离开 engine |
| 整篇配音编排 `dub_script` | `services/voice.py:887` | **engine 拥有编排**，这是裁定 3 要拆掉的核心 |
| 切句 `split_text`；凭据 `TtsCredentials`（`merge_headers` / `client_kwargs`） | `services/voice.py:127,322,335,340` | 编排所需零件全在 engine |
| 公网文件上传 `PublicUploadManager` | `services/voice.py:228` | provider 声明 `needs_uploader` 时注入 |
| 内置 provider 别名与注册 `_TTS_ALIASES` / `normalize_tts_name` / `ensure_tts_providers_registered` / `_make` | `providers/tts/__init__.py:17,35,39,49,52` | 内置 `qwen-tts` / `cosyvoice`（`:60`，`:64 needs_uploader=True`）/ `minimax`（`:67`）在 engine 里**默认就绪**；`:49-50` **已有让位**（插件先注册同名则内置不注册），但**没有插件时仍会注册**——这正是裁定 3 要拆的 |
| 唯一身份解析 `_get_provider` | `services/voice.py:715` | provider 名 = `contribution.id`；缓存键含凭据指纹 |
| HTTP 入口：上传/克隆/列表/删除/合成/整篇 dub | `api/voice.py:56,95,115,122,132,151` | 业务编排挂在 engine 的 5 个端点上 |

结论：**当前 TTS 是 engine 的一等功能，不是插件能力**。因此「把 TTS provider 搬进插件」只是半程——只要 engine 还握着 `dub_script` 和默认注册，插件化就名不副实，而这条后门正是裁定 3 要堵的。

#### 15.1.2 目标：engine 只留基座，不留能力

**engine 保留（基座，与 TTS 业务无关）：**

1. 贡献注册与分发（`plugins/base.py:74`、`plugins/contribution_dispatch.py`）与 `TTS_PROVIDER` 种类本身（`plugins/contribution.py:34`）。
2. 能力解析：`call_tool` 在调用时刻解析，缺失返回 `ToolStatus.NOT_FOUND`；`capability_matrix.TIER_MAP` 保持为空（不变量 #12）。
3. 凭据加密存储与掩码：复用 `settings_store` Fernet 与 `services/provider_sources.py:86 _mask_key`；**禁止**插件自建明文凭据通道。
4. 通用「把本地文件变成第三方可拉取的公网 URL」小工具：现 `PublicUploadManager`（`services/voice.py:228`）降级为**通用服务**，因为它是 provider `needs_uploader` 的依赖，本身不属于 TTS 业务。
5. 通用落盘能力：配音产物必须落工作区 `materials/`（不变量 #22 资源库单一真相），不得另起目录。

**engine 必须失去：**

1. `dub_script` 编排（`services/voice.py:887`）→ 移入 TTS 功能插件。
2. 切句策略与字幕对齐策略（现 `split_text` `:127` 及 `/dub` 内部逻辑）→ 移入功能插件。
3. `ensure_tts_providers_registered()`（`providers/tts/__init__.py:39`）的**内置注册本身**：注意 `:49-50` **已经有让位语义**（`if ContributionRegistry.resolve(TTS_PROVIDER, "qwen-tts") is not None: return`，embedding `:137`／rerank `:181`／STT `:52` 同构），所以裁定 3 要做的**不是**「改成有插件才注册」（已经是），而是「**没有插件时也不注册**」——删除内置注册，或改成必须显式开启的 fallback。
4. `/api/voice/*` 上的业务编排 → 见 15.1.5 的入口归属。
5. **内置 `dub_script` 技能**（`skill/dub.py:14` 的 `DubScriptSkill`，在 `skill/builtin.py:193,199` 无条件注册）→ 移入功能插件，改由插件贡献同名技能。**这一项是本轮核实新发现的必经点**，见 15.7。

> 卸载内置默认注册（上表第 3 项）与内置技能（第 5 项）是**必要**条件而非可选项。只搬 provider、不搬编排与默认注册，等于裁定 3 没有落地。

#### 15.1.3 主 agent 如何「发现」需要 TTS

不改变的边界：agent 不写死工具名，只认语义能力名（不变量 #11/#12/#19）。

**核实更正（2026-11-02 晚，详见 15.7）**：能力名**不需要新造**——`dub` 这个能力名**已经存在**：

- `clipwright/tool/capabilities.py` 的 `SKILL_CAPABILITY_ALIASES` 里有 `"dub": ("dub_script",)`；
- `agents/audio_agent.py:445` **已经在调** `await self.call_skill("dub", voice_id=…, text=…, split_mode=…)`，注释原文就是「插件贡献同名/同能力的配音技能即可接管，技能卸载后立即消失」。

也就是说裁定 3 想要的「主 agent 发现 → 能力解析 → 插件提供」链路**已经有一半在跑了**，缺的是「后备实现仍然是 engine 的 builtin」。因此：

| 能力名 | 现状 | 裁定 3 之后 | 缺失时行为 |
|---|---|---|---|
| `dub`（**沿用，不新增**） | 解析到 builtin `dub_script` → `VoiceService.dub_script()`（engine） | 解析到**功能插件**贡献的同名技能；builtin 移除 | `SkillStatus` 不可用 → agent 如实说明「当前没有可用的配音能力」，并写进 notes |
| ~~`tts_dub` / `tts_synthesize`~~ | 本 PRD 早前自造的名字，**作废** | — | — |

> 不新增能力名的理由：能力名是 agent 与插件之间的稳定契约，新造名字会让已有的 `audio_agent` 调用点、`capability_prefer` 配置与 `explain()` 诊断全部失配；复用 `dub` 才是「不变量 #11：agent 不写死工具名」的正确用法。
>
> 单句合成（`POST /api/voice/synthesize`、前端音色页的合成按钮）**不需要**新增 agent 能力名——它是功能插件自己的 HTTP 入口（15.1.5），不是 agent 语义能力。

「发现」的实现要求：

1. **判断交给模型**：engine 里**不得**出现「这个项目要不要配音」的规则或正则；主 agent 从用户需求与结构稿自行判断，判断不出就 `ask_user`。
2. **清单来自注册表**：`tool_schemas()` / `skill_resolve()` 实时读注册表，插件装卸后下一次调用立即生效，不跨任务缓存。
3. **缺失是一等状态**：如实跳过 + 日志 + 在最终交付里标注「未配音」；不许静默跳过，更不许假装已配音。
4. **禁止后门**：能力不可用时不得直连 `services/voice.py` 或 `dashscope` 兜底（与不变量 #19 同一立场）。

#### 15.1.4 前端「开始创作」页的 TTS 入口也由插件贡献

事实校正（推翻本 PRD 早期的模糊说法）：TTS 的前端面本来就很薄——`src/features/assets/DubView.tsx`（181 行、2 个控件）、`src/pages/VoicePage.tsx`（431 行、3 个控件）。所以「入口由插件给」不等于「前端大改」：改动集中在**入口是否出现**，而不是重写页面。

1. 「开始创作」页上的 TTS 入口**由 TTS 功能插件通过 `UI_SLOT` 贡献**；未安装插件时该入口**不出现**（不是灰掉、不是点了 500）。
2. 插件 slot 贡献只能描述入口/表单/控件，**不得注入可执行脚本或任意 HTML**（沿用 §6.2 的 schema 版本化约束）。
3. 前端渲染未知控件类型必须**拒绝并给出可读错误**，不许白屏（同不变量 #13 的收口立场）。
4. 业务逻辑下沉后，前端保留的是**通用 slot host + 通用表单 renderer**；不允许为了「插件化」把厂商字段写死进页面（与 §4 的 schema 驱动原则一致）。

> **已核实（2026-11-02 晚）**：前端 **已有**通用 slot host —— `src/features/plugins/slots.ts`（14 个 **冻结** slot 名单 + `evalVisibleWhen` 受限表达式 + 图标白名单）、`usePluginSlots.ts`、`AppOverlayHost.tsx`、`usePluginShortcuts.ts`、`PluginBundleFrame`。**所以不需要从零落 host**。
>
> 但**没有「开始创作」页专用 slot**。名单是：`nav.primary` / `nav.settings` / `page` / `editor.left-panel` / `editor.right-panel` / `editor.bottom-panel` / `editor.toolbar` / `timeline.tool` / `timeline.context-menu` / `props.section` / `export.preset` / `settings.section` / `app.overlay` / `shortcut`。名单按 `slots.ts:4-5` 的注释是**冻结**的，新增要走 SlotRegistry 契约评审。

**已裁定（2026-11-02 第六轮）：走 C —— 新增 slot `create.dub`。** 理由：A/B 只能做跳转，等于把配音挪出创作流程，与裁定 3 的原话（创作页有此入口）不符；D 会在 ProvidersPage 之外再长一套表单契约。C 的代价是**一次契约评审 + 一次名单变更**，一次性成本，换来入口与控件都在原位。可选方案对照（含「这块 UI 现在长什么样、host 挂在哪儿」的实测事实）保留在 §15.8 供追溯。

裁定后的 slot 契约（细节落点见 §15.8）：

| 项 | 定义 |
|---|---|
| slot 名 | `create.dub`（加入 `SlotName` 联合类型与 `SLOT_NAMES` 常量，**名单从 14 → 15**） |
| 挂载点 | `HomePage.tsx` 现行的 `03 配音与字幕` 步骤位置（原硬编码块 `:1040-1195` 移除，改由 host 渲染） |
| 贡献载荷 | `layout.widgets`（复用现有 `UILayout` 控件树：`string` / `select` / `checkbox` / `slider`，即现有 plugin UI 控件集），不允许脚本或任意 HTML |
| 可见性 | 支持 `visibleWhen` 受限表达式（不 eval），例如按 `context.videoMode` 决定是否显示「自动配音」 |
| 顺序 | `order` 缺省 100；内核元素保持 < 100（沿用单一排序来源） |
| 无插件时 | 03 步骤显示为「未安装配音插件」的**空态说明**或整块不渲染——二者选一，由产品在评审时定；**禁止**回落到任何 engine 内置配音表单 |
| 改名单的纪律 | 同步更新 `slots.ts` 的 `SlotName`/`SLOT_NAMES`/注释版本、`docs/pluginization-plan-2026-09-10.md §6 D-0` 的冻结名单表与 `docs/frontend-backend-parity.md` |

#### 15.1.5 插件如何对外提供 HTTP 入口（**已裁定：走通用能力网关**）

事实：`ContributionKind`（`plugins/contribution.py:22`）现有 17 种里**没有「HTTP 路由」**。所以「`/api/voice/*` 归插件」不是改个注册点就能完成，必须在三条路线里选一条。**2026-11-02 第四轮裁定：选 B（通用能力网关注册）**，A / C 不再评估。

| 路线 | 做法 | 代价 | 裁定 |
|---|---|---|---|
| A. 新增 `API_ROUTE` 贡献点 | 插件注册 router，loader 统一 mount | 破坏「贡献名单冻结、新增须契约评审」；还要扩权限/鉴权模型 | **否决** |
| **B. 通用能力网关** | engine 保留 `POST /api/capability/{name}`，内部 = 解析能力 + 执行 | 零新增贡献种类 | **采用** |
| C. 插件自带 router 由 loader 盲挂 | 插件拥有完整 URL 空间 | 权限/鉴权/审计边界难收 | **否决** |

**B 路线成本很低（本轮核实）**：`POST /api/tool/execute`（`api/tool.py:51`，body = `{name, params}`）**已经存在**，能按名字执行工具且缺失时返回 404（`:57`）；`capabilities.resolve_name()`（`tool/capabilities.py:161`）已能「先按能力解析、失败再当工具名直取」；路径参数白名单校验 `_validate_tool_params()`（`api/tool.py:17`）也现成。所以 B 是**薄封装**，不是新造一套。

##### 网关契约（W1 冻结，B3-T8 实现）

| 项 | 定义 |
|---|---|
| 端点 | `POST /api/capability/{name}`；`name` = **能力名**（如 `dub`），不是工具名 |
| 请求体 | `{"params": {...}}`；与 `/api/tool/execute` 的 `params` 同形 |
| 响应体 | 复用 `ToolExecResult`（`schema/tool.py`），与工具执行结果同构，前端无需两套解析 |
| 解析 | `capabilities.resolve_name(name)`（能力名优先，工具名兜底）→ 得实现 → `ToolRegistry.execute(resolved.name, **params)` |
| 权限与审计 | 复用 `_validate_tool_params()` 做路径白名单；按 `permission_tier` 记审计（`costly` / `dangerous` 档必须留痕） |
| 状态码 | `404` 能力名解析不到（插件未装/被禁用）；`422` 参数不合 schema 或路径不在白名单；`503` 已解析到实现但依赖缺失（凭据未配置 / `needs_uploader` 上传器不可用）；`200` 成功 |
| 禁止 | 网关**不承载任何业务编排**（编排在能力实现内部）；不得为某个能力写专属分支；不得绕过能力解析直取服务 |

过渡期：`/api/voice/*` 五个端点保留为**薄转发**（转调网关，不承载编排）；插件缺失时统一 `503` + 可读原因；新增前端一律走网关，不再新增 `/api/voice/*` 调用点。

#### 15.1.6 迁移与回滚

1. 过渡期允许「内置实现 + 插件」并存，但**同名仍是插件优先**（同 `providers/llm/__init__.py:25-31` 语义），且内置可整体关闭。
2. **删除时机（已裁定）**：插件完成并通过 §15.3 的 9 条验收后**立即删除**，不设观察期、不设安装覆盖率门槛。理由：本项目的 TTS 是「单实例、由本仓自己维护」的能力，不存在需要按装量灰度的问题；留一个「临时兜底」只会变成永久后门。删除范围含 `services/voice.py:887 dub_script`、`skill/dub.py` 的 builtin 注册（`skill/builtin.py:193,199`）、`providers/tts/__init__.py:39` 的无条件注册。
3. 回滚：禁用功能插件 → 配音能力整体 unavailable。这是**正确降级**，不是故障；**不保留**「临时打开内置实现」的开关（见第 2 条：删就是真删；要回到旧行为就回滚代码提交）。
4. 存量数据：已产出的音频/字幕不动；凭据记录按 `plugin_id=null` 标为 builtin 来源，迁移时不清空。

#### 15.1.7 核实结论（原「待核实」，2026-11-02 晚已全部闭环）

| # | 原问题 | 结论 | 证据 |
|---|---|---|---|
| 1 | 是否已有 agent 经能力名触发配音？ | **有**：`AudioAgent` 已在 `call_skill("dub", …)` | `agents/audio_agent.py:443-451`；`tool/capabilities.py` 的 `SKILL_CAPABILITY_ALIASES["dub"]=("dub_script",)` |
| 2 | `PublicUploadManager` 是否另有调用方？ | **没有**，只有 `services/voice.py` 内部 3 处 | `:228` 定义；`:479`、`:576` 形参；`:692` 实例化（全仓仅此四处） |
| 3 | `ensure_tts_providers_registered()` 与 `settings_store.apply_all()` 谁先？ | **未定论**（启动时序仍需 W3 实测；不变量 #19 记过同类坑） | 仅确认调用点在 `providers/tts/__init__.py:39`，实际时序未读启动流程 |
| 4 | `TTS_PROVIDER` 由谁消费？ | **已确认有分支**：在 provider 类 kind 集合里，facade-only + 协议校验入口 | `plugins/contribution_dispatch.py:45-52`（`:49` 为 `TTS_PROVIDER`）、`:55-71` |
| 5 | 前端是否已有 slot host？ | **有**，14 个冻结 slot；但**无创作页专用 slot** → 第六轮裁定**新增 `create.dub`**（见 15.1.4 / 15.9） | `src/features/plugins/slots.ts:14-45`、`usePluginSlots.ts:54,89`、`AppOverlayHost.tsx` |

### 15.2 裁定 4：全部 `build_*` 允许被插件替换

#### 15.2.1 现状（代码事实）

`clipwright/prompts.py`（不变量 #23）当前导出 **26 个常量 + 8 个参数化 builder**。以下签名与调用点**已于 2026-11-02 晚逐个核实**（`Select-String` 全仓扫 `build_*` 名）：

| # | builder | 签名 | 调用点 |
|---|---|---|---|
| 1 | `build_budget_note` | `(remaining: int, max_turns: int, used: int)` | `services/agent_loop.py:311` |
| 2 | `build_plan_translate_system` | `(brief_json: str)` | `agents/requirements_agent.py:185` |
| 3 | `build_bgm_match_system` | `(slot_keys: list[str])` | `agents/audio_agent.py:1184` |
| 4 | `build_anim_marker_system` | `(anim_guide: str, mg_info: str)` | `agents/structure_agent.py:1060` |
| 5 | `build_anim_marker_user` | `(scenes_count: int, scenes_text: str)` | `agents/structure_agent.py:1061` |
| 6 | `build_material_queries_batch_prompt` | `(scene_lines: list[str], *, category_hint: str = "", persona_style: str = "", brief_hint: str = "")` | `agents/material_agent.py:247` |
| 7 | `build_material_queries_prompt` | `(scene_title: str, keywords: list[str], description: str, *, persona_style: str = "", brief_hint: str = "")` | `agents/material_agent.py:293` |
| 8 | `build_material_english_prompt` | `(text: str)` | `agents/material_agent.py:342` |

> **两处更正**（原表凭猜测填的，已按代码改）：① `build_anim_marker_system` / `build_anim_marker_user` 的调用点是 **`structure_agent`**，不是 `edit_agent`；② 只有 1 个 builder 带「运行时数字」语义（`build_budget_note` 的 `remaining/max_turns/used`），§15.2.2 的「双 dry-render 输出必须不同」正是针对它这类形状，其余 builder 靠签名 + 占位符 + 纯度三关即可。
>
> 注意 6/7/8 带**关键字参数**（`persona_style` / `category_hint` / `brief_hint`）——这是 Category/Persona 影响提示词的**唯一**通道（见 15.2.5），不是文本覆盖。

#### 15.2.2 替换契约（builder 与常量强度不同）

常量替换 = 换一段文字；builder 替换 = 换一个**行为接口**，契约必须更硬：

| 契约项 | 要求 | 不满足时 |
|---|---|---|
| 符号名 | 必须精确等于 `prompts.py` 导出的名字 | 拒绝，不做模糊匹配 |
| 签名 | `inspect.signature` 的参数名/顺序/默认值须与默认 builder 一致 | 拒绝，报「期望 vs 实际」 |
| 返回值 | `str`、非空、长度 ≤ 上限（见 15.2.7） | 拒绝 |
| 占位符残留 | 返回文本不得残留未替换的 `{xxx}` 占位 | 拒绝 |
| **参数必须真的用上** | dry-render 用**两组不同样例参数**各跑一次，**两次输出必须不同**；输出相同即判定「参数声明了却没用」 | 拒绝 |
| 纯度 | 不读配置、不做 I/O、不调 LLM、不联网；同参数必须同输出（幂等） | 拒绝或降为不可用 |
| 失败隔离 | 插件 builder 抛异常时**回落默认 builder 并记录**，任务不得崩 | 必做 |

> 「两组样例参数输出必须不同」这条是对 §7.2 原方案（只比占位符集合）的**加强**。只比占位符挡不住「参数收下却不用」的假覆盖——8 个 builder 里带运行时数字的（预算/轮次这类）必须防这一手，否则插件可以把数字写死，主 agent 拿着错的预算提示继续跑。


#### 15.2.3 覆盖范围与优先级

- 覆盖范围：**26 常量 + 8 builder 全部可覆盖**，不再按风险分级只放一部分。
- 优先级不变（不变量 #23 / §7.2）：`prompts.py < Category < Persona < Skill < 插件`。
- 同符号多插件：默认拒绝冲突并列出插件 id（§7.2 既定）；是否给管理员优先级字段仍待产品确认。
- 每段最终文本必须带来源：`source=default|category:<id>|persona:<id>|skill:<id>|plugin:<id>@<version>`，诊断可见。

#### 15.2.4 不提供 demo 插件：本轮只交付「能力入口」

需求方明确不写 demo 插件。因此本轮交付物是**能力入口**，不是示例实现：

1. **符号清单接口**：列出 26+8 个可覆盖符号、签名、是否有插件覆盖、当前生效来源。
2. **契约文档**：把 15.2.2 那张表落成可查文档。
3. **校验器**：注册期 + 启用期执行签名/残留/双 dry-render/纯度检查，产出可读诊断。
4. **负例**（人工验收用例，**不落成插件目录**）：签名不匹配 / 占位符残留 / 参数不用 / 抛异常 / 非 `str` 五种，必须各自被拒绝或被回落。
5. **测试方式**：不新建 `plugins/prompt_demo/`；契约测试用**内联假贡献对象**完成，不产生可安装的示例插件。

> **存量插件处置（已裁定）**：`plugins/` 下**已经**有 4 个提示词贡献者——`kinetic_typography/main.py:112`、`logic_animations:66`、`lottie_animations:121`、`sandbox_demo:76`。「不新建 demo 插件」只约束**本轮新增产物**，不追溯清理既有资产：**`sandbox_demo` 保留**（它是 `runtime: subprocess` + 声明即授权 + 写盘被拒的唯一活体回归样本），`example_full` / `example_caption_plugin` / `subagent_demo` 本轮不动。这 4 个 `PROMPT` 贡献都是 `meta.agent` 级**附加**，在新校验下走 `mode=append` 路径，行为必须保持不变（验收见 15.4 第 11 条）。

#### 15.2.5 存量 PROMPT 贡献的兼容

1. 现有 4 个贡献者的语义是 `get_for_agent(agent_id)` 的**附加段**（`plugins/prompt_registry.py:38,99`），不是符号替换。
2. 新契约必须同时支持两种模式，并在 schema 里**显式区分**：`mode: append|replace`，缺省 `append` 保证向后兼容（对应 §7.1 第 1 条「不能把附加误作替换」）。
3. 内置贡献的收集逻辑（`plugins/builtin_contributions.py:163,195`）必须同步走新校验，否则新校验管不住内置贡献。
4. **已核实（2026-11-02 晚）**：`plugins/prompt_registry.py` 的 `PluginPrompt` 只有 `plugin_id / agent_name / prompt / priority / description`（`:31-35`），**没有 symbol、没有 mode**；`get_for_agent()`（`:99`）返回**按 priority 降序的纯文本列表**（附加语义）。Category / Persona / Skill **没有任何文本覆盖通道**——它们只通过 builder 的**关键字参数**影响提示词（`category_hint` / `persona_style` / `brief_hint`，见 15.2.1 表 6/7 行）。因此 §7.2 的「五层覆盖顺序」**must 改写**为：

| 层 | 真实能力 | 落地方式 |
|---|---|---|
| `prompts.py` | 默认文案（26 常量 + 8 builder） | 唯一基线 |
| Category / Persona / Skill | **参数注入**，不改文案 | builder 关键字参数 |
| 插件 | 今日：agent 级**附加** + priority 排序；裁定 4 后：新增**符号级替换**（常量与 builder） | `PluginPromptRegistry`（append）+ 新符号覆盖层（replace） |

> 这条更正很重要：如果按原文「五层顺序」去实现，会做出四层都不存在的覆盖通道。实际是「一层默认 + 一层附加 + 一层新增替换 + 参数注入」。

#### 15.2.6 核实结论（原「待核实」）

| # | 原问题 | 结论 |
|---|---|---|
| 1 | 8 个 builder 的签名与调用点 | **已全部核实**，见 15.2.1 表（并与原猜测的两处调用点不符，已更正） |
| 2 | `prompt_registry` 是否已有 append/replace？ | **没有**，只有 agent 级 append + priority（`prompt_registry.py:31-35,99`）→ 裁定 4 需要新增符号级 replace 通道 |
| 3 | 长度上限 / 纯度检查方式 | **已裁定（第五轮）：静态检查**，规则见 15.2.7 |
| 4 | Category/Persona/Skill 是否具备覆盖能力 | **不具备**，只注入参数（见 15.2.5 第 4 条） |

#### 15.2.7 长度上限与纯度检查方式（**已裁定：用静态检查**）

**裁定（2026-11-02 第五轮）：长度上限与纯度检查一律用静态检查（`ast` 白名单扫描），不启用运行期隔离沙箱。**

要把「静态能测什么」说清楚，否则会写出测不出来的规则：

| 对象 | 静态可测（注册期、不执行插件代码） | 只能 dry-render 后测（同进程调用，不是沙箱） |
|---|---|---|
| 常量覆盖 | 提交的就是文本 → 长度、占位符残留、是否含可执行片段 | — |
| builder 覆盖 | 源码结构：`import`、禁用调用、复杂度、嵌套深度、源码体积、`async`/`yield`/装饰器 | 实际返回值的长度、是否残留 `{xxx}`、两组样例输出是否不同、是否抛异常、幂等性 |

**静态检查规则（白名单策略，实现用标准库 `ast`，不引第三方依赖）：**

| 检查 | 规则 | 不通过 |
|---|---|---|
| 导入 | 只允许 `from __future__ import annotations`；**任何其它 `import` / `from … import` 一律拒绝** | 拒绝 |
| 禁用调用（黑名单，逐个 `ast.Call` 校验 `func` 名） | `eval` `exec` `compile` `__import__` `open` `input` `globals` `locals` `vars` `setattr` `delattr` `getattr`（动态取属性是绕过静态检查的头号手段） | 拒绝 |
| 禁用模块间接调用 | 源码中出现 `os` `sys` `subprocess` `socket` `requests` `httpx` `pathlib` `shutil` `tempfile` `random` `time` `datetime` `uuid` `asyncio` 任一名字 | 拒绝 |
| 并发/惰性 | `async def`、`await`、`yield`、装饰器 一律拒绝（builder 必须是同步纯函数） | 拒绝 |
| 复杂度 | AST 节点数 ≤ **600**；嵌套深度 ≤ **8**；`return` 语句 ≤ **8** | 拒绝 |
| 源码体积 | 单个覆盖的源码 ≤ **32 KB** | 拒绝 |
| 输出长度（dry-render 后） | `len(out)` ≤ `max(3 × 默认 builder 输出长度, 6000)` 字符 | 拒绝 |

> 上表的数字是**本 PRD 定的初值**，实施时可按真实 builder 基线校准，但「静态 + 白名单 + 黑名单调用」这个方法不再改。

**明确不做运行期沙箱及其理由**：builder 是在**宿主进程内**被调用的普通函数，真要隔离必须上进程/容器，成本与延迟都不值得；而静态白名单 + 禁用调用黑名单已能挡住「读配置 / 联网 / 写盘 / 动态取属性」这几类实际风险。**残余风险如实写**：静态检查挡不住刻意混淆（例如把危险逻辑塞进 `__dunder__` 或字符串里再 `format` 出去）——兜底手段是「只接受白名单语法 + 双 dry-render + 输出长度上限 + 出问题就禁用插件」；若后续真出现绕过案例，再评估升级为子进程执行（`sandbox_demo` 已证明 `runtime: subprocess` 这条路在本仓可行，届时可复用）。

### 15.3 修订后的 W3 验收（取代 §11 W3）

| # | 操作 | 期望现象 |
|---|---|---|
| 1 | 全新安装、**不装任何 TTS 插件**，让主 agent 做需要配音的项目 | agent 判断需要配音 → `call_skill("dub")` 解析不到实现 → 如实告知「无配音能力」并继续其余交付；不得静默跳过，不得直连服务兜底 |
| 2 | 同上，检查 engine 侧 | `ensure_tts_providers_registered()` 不再无条件执行；`dub_script` 不再由 engine 编排；builtin `dub_script` 技能**已移除**（`skill/builtin.py:193,199`） |
| 3 | 「开始创作」页 `03 配音与字幕` 步骤 | 无 TTS 插件时该步骤**无插件控件**（空态说明或整块不渲染，评审定的那种），**不得**出现任何 engine 内置配音表单；装插件后控件出现在**原位**（`create.dub` 由 `usePluginSlots` 渲染），未知控件类型拒绝并给可读错误 |
| 4 | 装功能插件 + CosyVoice 协议插件 | 入口出现；控件由 schema 决定；`needs_uploader` provider 能拿到公网 URL |
| 5 | 只装功能插件、不装协议插件 | provider 列表无 CosyVoice，其他已配置 provider 可用；不出现「半可用」状态 |
| 6 | 页面开着时禁用功能插件 | 在途请求明确失败/取消；入口消失；无 500、无白屏 |
| 7 | 协议错误或流中断 | 错误带 provider id 与阶段（鉴权/请求/流式/解码），日志无密钥 |
| 8 | **通用能力网关**（已裁定路线 B） | 装插件时 `POST /api/capability/dub` 成功且返回 `ToolExecResult`；不装插件时 `404` + 可读原因；凭据未配置时 `503`；参数不合 schema 或路径不在白名单时 `422`；`/api/voice/*` 过渡期为薄转发、**不含编排**；网关无任何针对 `dub` 的专属分支 |
| 9 | 更换 TTS 凭据 | 仍按 `contribution.id` + 凭据指纹换实例，不复用旧 provider（守规 ② 不变） |

### 15.4 修订后的 W5 验收与工作量

**W5 验收（取代 §11 W5）：**

| # | 操作 | 期望现象 |
|---|---|---|
| 1 | 无 prompt 插件跑真实路径 | 实际 prompt 与 `prompts.py` 基线一致（stub `LLMService.ask` 抓真实 prompt 比对） |
| 2 | 覆盖任一常量符号 | 仅该符号变化，来源可见 `plugin:<id>@<version>` |
| 3 | **逐个**覆盖 8 个 builder（8 次） | 签名一致才接受；输出里含真实入参值 |
| 4 | 提交签名不一致的 builder | 注册/启用失败并显示期望与实际签名 |
| 5 | 提交残留 `{xxx}` 占位的输出 | 拒绝 |
| 6 | 提交「参数收下但不用」的 builder（两组样例输出相同） | 拒绝 |
| 7 | builder 运行时抛异常 | 回落默认 builder + 记录，任务不崩 |
| 8 | 同符号两插件覆盖 | 冲突明确，不按加载顺序决定结果 |
| 9 | 禁用覆盖插件 | 回落下一优先层，最终回 `prompts.py` |
| 10 | 检查 `plugins/` 目录 | 无新增 demo 插件目录 |
| 11 | 现有 4 个 PROMPT 贡献者在新校验下 | append 模式行为不变，附加段不丢 |
| 12 | 提交带 **`import`** 的 builder（如 `import os`） | **静态检查**拒绝并指出违规节点（不看运行结果） |
| 13 | 提交调用黑名单函数（`getattr` / `open` / `eval` / `__import__`） | 拒绝，报出函数名与节点行号 |
| 14 | 提交超复杂度 builder（AST 节点 > 600 / 嵌套 > 8 / `return` > 8） | 拒绝 |
| 15 | 提交输出超长的 builder（`len(out)` > `max(3× 默认, 6000)`） | dry-render 后拒绝，报告默认长度与实际长度 |
| 16 | 合规 builder（纯字符串拼接 + `format`） | 通过；静态检查不应误杀正常写法 |

**工作量修订（任务级口径，取代编码计划 §10 旧值）：**

| 批次 | 修订前 | 修订后 | 增量原因 |
|---|---|---|---|
| Batch 3（TTS） | 5 项 / 7.0 人日 | **8 项 / 15.0 人日** | 新增：engine 摘除编排与默认注册、能力发现与缺失一等状态、**新增 `create.dub` slot 并迁移 03 步骤**（第六轮裁定，B3-T5 由 1.5 → 2.5）、插件 HTTP 入口（网关） |
| Batch 5（提示词） | 5 项 / 7.0 人日 | **8 项 / 12.0 人日** | 新增：覆盖全部 `build_*`、builder 硬契约 + 双 dry-render + **静态纯度检查**、来源诊断与「能力入口」交付（含符号清单接口与校验器） |
| **总量** | 36 项 / 38.5 人日 | **42 项 / 51.5 人日** | 净增 6 项 / 13.0 人日 |

**关键路径**：Batch 0（4.0）→ Batch 1（8.5）→ max(Batch 2 6.0, **Batch 3 15.0**) → Batch 4（6.0）= **约 33.5 人日**；2 人并行约 **29 个工作日**，另加约 3 日集成验收。原「2 人约 23 个工作日」的旧值作废。

> **口径说明**：本文 §13 的 47 人日是**粗估**，编码计划 §10 的 38.5 人日是**任务级**，两者在本轮修订前就已不一致。修订后**统一采用任务级口径（51.5 人日 / 42 项）**，§13 的粗估数字不再引用。

### 15.5 与既有不变量的关系

| 不变量 | 与本次裁定的关系 |
|---|---|
| #11 / #12（能力解析、`TIER_MAP` 保持为空） | 裁定 3 是它的直接推论：TTS 也必须走能力解析，否则 engine 仍留有写死接线 |
| #19（判断交给模型、能力交给插件、不许后门） | 15.1.3 的「主 agent 发现」与「禁止直连兜底」直接引用该条 |
| #22（资源库单一真相，预览/产物收口） | 配音产物必须落 `materials/`，不得另起目录 |
| #23（`prompts.py` 唯一来源、逐字搬运） | 裁定 4 不改变基线，只加下游覆盖层；「搬运逐字不变、快照差异 0」的规则继续有效 |
| 前端 slot 契约（`slots.ts` 名单冻结） | 裁定 C 是**第一次动冻结名单**（14 → 15）：必须走 SlotRegistry 评审并同步 `SlotName`/`SLOT_NAMES`/D-0 文档/对账表，见 15.9 八项清单 |

建议在 Batch 3 / Batch 5 落地时给 `AGENTS.md` 追加两条：#19 下补「TTS 能力由插件提供，engine 不默认具备（含编排与 builtin `dub_script` 技能）」；#23 下补「builder 覆盖需过签名/占位符/双 dry-render/纯度四关」。

### 15.6 本次修订新增的开放问题

**已裁定，移出开放问题：**

- ~~删除内置 TTS 编排的安装覆盖率门槛~~ → **已裁定**：插件完成并通过 §15.3 验收后**立即删除**，无观察期、无门槛（见 15.1.6 第 2 条）。
- ~~Category / Persona / Skill 是否真能覆盖提示词~~ → **已核实**：不能，只注入参数；§7.2 的「五层顺序」已按 15.2.5 改写。
- ~~插件 HTTP 入口选 A / B / C 哪条~~ → **已裁定（2026-11-02 第四轮）**：走 **B 通用能力网关**（`POST /api/capability/{name}`），A / C 否决；契约见 15.1.5 表。
- ~~`plugins/sandbox_demo` 保留还是移除~~ → **已裁定（2026-11-02 第四轮）**：**保留 `sandbox_demo`**。理由：它是「`runtime: subprocess` + 声明即授权 + 写盘被拒」的唯一活体回归样本，删掉等于丢掉子进程契约的验证手段；「不新建 demo 插件」管的是**本轮新增产物**，不追溯清理既有测试资产。`example_full` / `example_caption_plugin` / `subagent_demo` 本轮不动，另行评估。
- ~~builder 覆盖的长度上限与纯度检查方式~~ → **已裁定（2026-11-02 第五轮）**：**静态检查**（`ast` 白名单 + 黑名单调用），不启用运行期沙箱；长度上限与复杂度阈值见 15.2.7。
- ~~「开始创作」页的 TTS 入口挂哪个 slot~~ → **已裁定（2026-11-02 第六轮）：新增 slot `create.dub`**（名单 14 → 15）。代价是一次 SlotRegistry 契约评审 + 一次名单变更；落地清单见 15.9，对应编码计划 B3-T5。

**仍需拍板：无。** 本节两条开放问题（P1 契约、P2 契约）已全部裁定或核实，剩下的都是 Batch 0 的核实任务与实施细节，不再是产品决策。

### 15.7 本轮核实的方法与新增事实（可复核）

核实方式：全仓 `Select-String`（等价 `rg`）逐符号扫调用点 + 读实现，不靠推断。新增事实清单：

| 事实 | 位置 |
|---|---|
| 配音调用链是 **agent → `call_skill("dub")` → `DubScriptSkill` → `VoiceService.dub_script`**，三层全在 engine | `agents/audio_agent.py:443-451`；`skill/dub.py:14,20,38`；`services/voice.py:887` |
| `dub` 是**已有**能力名，别名指向 `dub_script` | `tool/capabilities.py` `SKILL_CAPABILITY_ALIASES` |
| builtin `dub_script` 技能**无条件注册** | `skill/builtin.py:193,199` |
| 插件提示词只有 agent 级 append + priority，**无 symbol/mode** | `plugins/prompt_registry.py:31-35,67-74,99` |
| 插件提示词**消费点只有 4 个 agent**（animation / material / requirements / structure），audio / edit / quality 不读 | `agents/animation_agent.py:2206`、`material_agent.py:301`、`requirements_agent.py:122,187`、`structure_agent.py:486` |
| 前端 slot 契约完整且**名单冻结**（14 个 + 图标白名单 + 受限 `visibleWhen`） | `src/features/plugins/slots.ts:14-45,116-178` |
| 已有按名执行工具的通用端点（含路径白名单校验） | `api/tool.py:39,51,62` |
| `resolve_name` 已实现「能力名优先、工具名兜底」 | `tool/capabilities.py:161-166` |
| `TTS_PROVIDER` 属 provider 类 kind（facade-only），仅 `LLM_PROVIDER` 有协议校验 | `plugins/contribution_dispatch.py:45-52,64-71` |
| `PublicUploadManager` 全仓仅 `voice.py` 内部使用 | `services/voice.py:228,479,576,692` |

**本轮仍未核实（保持诚实，不猜）**：

1. `ensure_tts_providers_registered()` 与 `settings_store.apply_all()` 的启动时序（15.1.7 第 3 条）。
2. `plugins/` 下各 demo/example 插件是否被测试或发行脚本引用（未做全仓引用扫描；已裁定保留，故不阻塞）。
3. 70 条原始待核实（§2–§13）中与 OpenAI/Anthropic/isobase/embedding/rerank/STT 相关的部分**本轮未逐条复核**，仍按原台账待 Batch 0 处理。

> 已闭环、不再列在此处：builder 长度上限与纯度检查方式（→ 15.2.7 静态检查，第五轮裁定）。

### 15.8 「创作页 TTS 入口」到底指哪块 UI（裁定 C 的实测依据）

裁定 3 说「前端开始创作的页面也有 TTS 入口」。落到代码，这句话指的是 **`src/pages/HomePage.tsx` 这个「新建制作」向导里的 `03 配音与字幕` 步骤**（`ConsoleStep no="03" title="配音与字幕"`，`HomePage.tsx:1040-1195`），当前是**硬编码在 HomePage 里**的：

| 现有元素 | 位置 | 说明 |
|---|---|---|
| 音色下拉 `voicePick` | `HomePage.tsx:1059-1061` | 选音色，存 `localStorage.clipwright_voice_prefs` |
| 「自动配音」开关 `autoDubPref` | `HomePage.tsx:1106-1113` | 是否用 TTS 生成旁白 |
| 上传配音文件 | `HomePage.tsx:1174` | 走上传，与自动配音互斥 |
| 配音/估算时长提示 | `HomePage.tsx:1184-1195`、`1305` | 步骤完成度判定 `stepDone.dub`（`:371`） |

而前端**已挂载的 slot host 只有 5 处**（实测）：

| slot | 挂载点 | 能否承载「03 配音步骤」内容 |
|---|---|---|
| `nav.primary` | `HomePage.tsx:1524` | ✗ 只是导航项（跳页面），不是步骤内容 |
| `page` | `PluginPage.tsx:20` | ✗ 独立页面，只能做「跳过去配音」 |
| `editor.left-panel` / `editor.right-panel` / `editor.bottom-panel` | `EditorLayout.tsx:66-68` | ✗ 创作页（HomePage）**没挂**这些 host，挂不上 |
| `props.section` / `editor.toolbar` / `settings.section` / `timeline.tool` / `export.preset` / `app.overlay` / `shortcut` / `nav.settings` | 本仓未见挂载点（或仅在编辑器） | ✗ 语义也不对（属性面板/工具栏/全局浮层） |

**「挂哪个 slot」的四选一对照（保留供追溯；已裁定选 C，见 15.1.4）：**

| 方案 | 做法 | 是否满足裁定 3 | 代价 |
|---|---|---|---|
| A. 复用 `nav.primary` | 创作页放一个「配音」导航项，点了跳插件页 | 弱满足（入口在，但不在 03 步骤里，流程被打断） | 最小，零新契约 |
| B. 复用 `page` | 插件提供独立配音页（`PluginPage` 壳） | 同上 | 小 |
| **C. 新增 slot `create.dub`（✅ 已裁定）** | HomePage 在 03 步骤位置挂 host，步骤内容由插件贡献 | **强满足**：入口与控件都在创作页原位 | 要过 SlotRegistry 契约评审（名单 14→15）+ HomePage 挂 host + 移除原硬编码块 |
| D. 不加 slot，改**schema 驱动表单** | 保留 03 步骤外壳，里面的控件由插件经 provider schema/controls 描述（同 ProvidersPage 的做法） | 强满足，且不动 slot 名单 | 要定义「创作页控件 schema」；本质是另一个小型契约，会与 ProvidersPage 形成两套表单契约 |

### 15.9 `create.dub` slot 落地清单（裁定 C 的可执行版）

改名单这件事的风险在于「漏改一处就静默失效」，所以把落点逐项列清（实施对应编码计划 **B3-T5**）：

| # | 落点 | 具体动作 | 漏改的后果 |
|---|---|---|---|
| 1 | `src/features/plugins/slots.ts` | `SlotName` 联合类型加 `'create.dub'`；`SLOT_NAMES` 数组加同名项；文件头注释的版本标记升一版 | 类型不认，贡献被判非法 slot 丢弃 |
| 2 | `docs/pluginization-plan-2026-09-10.md §6 D-0` | 冻结名单表加一行 `create.dub`（「开始创作」向导的配音步骤内容） | 文档与代码不一致，下次评审失去依据 |
| 3 | `src/pages/HomePage.tsx` | 在 `03 配音与字幕` 步骤位置改为 `usePluginSlots('create.dub')` 渲染；**移除**原硬编码控件块（`:1040-1195` 内的音色/开关/上传/时长提示） | 出现「插件入口 + 旧硬编码」双份 UI |
| 4 | 步骤完成度 `stepDone.dub`（`:371`） | 由插件贡献的控件值或能力可用性驱动，不能继续读被删掉的本地 state | 步骤永远「未完成」或永远「已完成」 |
| 5 | `localStorage.clipwright_voice_prefs`（`:1059-1061`、`:1113`） | 音色/自动配音偏好改由插件或 provider source 持有；迁移时保留读旧键 | 用户既有偏好丢失 |
| 6 | 无插件空态 | 03 步骤显示「未安装配音插件」说明（或整块不渲染，二选一，评审时定） | 空白步骤让用户以为坏了 |
| 7 | `docs/frontend-backend-parity.md` | 记录 `create.dub` 的贡献形状与客户端渲染入口 | 前后端对账表失真 |
| 8 | 契约评审 | 新增 slot 必须经 SlotRegistry 评审（名单冻结纪律），评审记录留档 | 下次任何人新增 slot 都会绕过流程 |

> slot 契约本身的字段（`slot` / `id` / `title` / `order` / `icon` / `visibleWhen` / `layout`）**沿用现有 `SlotContribution` 定义，不新增字段**；`layout.widgets` 复用现有 plugin UI 控件集（`string` / `select` / `checkbox` / `slider`），未知控件类型必须拒绝并给可读错误（不变量 #13）。



