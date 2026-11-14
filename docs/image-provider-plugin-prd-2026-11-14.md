# AI 生图 Provider 化 PRD（第一期：Engine 契约 + Provider Tab + OpenAI gpt-images 插件）

- 版本：v1.0（2026-11-14）
- 分支：`feat/zam_aigen_plugin`（Engine `J:\Clipwright` / 前端 `J:\Clipweight-Client`）
- 状态：**待评审，未实施**
- 前置文档：`J:\Clipweight-Client\docs\image-provider-prd-2026-11-14.md`（2026-11-14 提交 `6205f48`，覆盖更完整的「生成工具 + 工作区素材 + 定点插入」；本文是其**第一期切片**，只做 Provider 层与插件接入）
- 关联：[docs/plugin_authoring.md](plugin_authoring.md)、[docs/plugin-registration-runtime-optimization-prd-2026-11-13.md](plugin-registration-runtime-optimization-prd-2026-11-13.md)

## 实施状态（2026-11-14）

第一期为**已实施**（分支 `feat/zam_aigen_plugin`）：

| 里程碑 | 状态 | 落点 |
|---|---|---|
| M1 契约 | ✅ | `ContributionKind.IMAGE_PROVIDER`、`providers/base.py::ImageProvider`、`provider_decl` 种类与能力键、dispatch 注册期校验 |
| M2 配置源/API | ✅ | `provider_sources` 的 `image` kind/role；`/available` 的 `image_provider` 与 `role_kinds.image`；`/schemas?kind=image`（返回键 `image_provider`）；`/test` 调 `probe()` |
| M3 前端 tab | ✅ | `ProviderKind/ProviderRole` 增 `image`；`TABS` 增「AI 生图」；`KIND_ROLES/ROLE_LABELS/KIND_PROVIDER_KEY` 同步 |
| M4 插件 `image_providers` | ✅ | `plugins/image_providers`（`openai_compatible`） |
| M5 插件 `image_gpt_images` | ✅ | `plugins/image_gpt_images`（`openai_gpt_image`） |
| M6 下架与清理 | ✅ | 三插件目录删除；`api/plugin.py` legacy 映射、`prompts.py`、`capability_matrix`、`requirements_service`、`generated_source`（迁 `services/generated_assets.py`）与旧测试同步清理 |

测试：`tests/clipwright/test_image_provider_plugin.py`（契约/API/插件 5 例）；后端
`-k "plugin or provider or tool or capability"` 826 passed；前端 `tsc --noEmit` 与
`ProvidersPage.test.tsx` 40 passed。第二期（`image_generate` 工具/落盘/账本/素材检索）未实施。

---

## 0. 结论先行

用户要的是三件事：

1. **下架** `ai_image_gen` / `ai_music_gen` / `ai_video_gen` 三个把厂商协议写死在插件里的生成插件；
2. 让「AI 生图」成为**一类 Provider**：后台「供应商」页多一个 **AI 生图 tab**，源、密钥、模型、启停都走既有 Provider 配置体系；
3. 提供**一个真正能接 OpenAI gpt-images 的插件**（`POST {base_url}/images/generations`，`gpt-image-1`）。

第一期只做 **Provider 层**：Engine 定义契约与配置源、前端多一个 tab、插件实现厂商协议。
**生成工具（`image_generate`）与工作区素材可见性属于第二期**，沿用前置 PRD §3.3/§3.4，不在本文实施范围（见 §8 风险 R2：下架旧插件后到第二期落地前，主 Agent 侧暂时没有"生成图片"工具）。

一句话边界：**Engine 只提供"插入生图 provider"的能力与宿主职责；gpt-images 的请求形状、鉴权、响应解析全部在插件里。**

---

## 1. 现状与证据（2026-11-14 实测）

### 1.1 已有基础（可直接复用）

| 机制 | 现状 |
|---|---|
| 贡献门面 | `plugins/contribution.py` 的 `ContributionKind` 共 **19 类**（18 类 + `clip_kind`），不含图像生成 |
| Provider 声明 | `plugins/provider_decl.py` 的 `PROVIDER_KINDS` = llm / tts / stt / embedding / rerank / renderer_backend；`config_schema` 与 `capabilities` 已结构化，前端表单已 schema 驱动 |
| Provider 源 | `services/provider_sources.py`：`SOURCE_KINDS = ("llm","embedding","rerank","tts")`、`ROLES = ("main","flash","vision","embed","rerank","tts")`，支持 base_url / `api_keys`（Fernet 加密）/ models / 配置覆盖 |
| Provider API | `api/providers.py`：`GET /available` 按 4 类聚合 + `provider_meta`；`GET /schemas?kind=`；`GET /capabilities`；`POST /{source_id}/test`（llm/embedding/rerank/tts 分支） |
| 前端供应商页 | `src/pages/admin/ProvidersPage.tsx`：`TABS` 硬编码 4 类（LLM / Embedding / Rerank / TTS）；类型 `ProviderKind = 'llm' | 'embedding' | 'rerank' | 'tts'`（`src/services/api/providers.ts`） |
| 插件能力 | 插件可贡献 tool/skill/hook/prompt/slot/material_source 等；provider 类贡献走 `ContributionRegistry` + `provider_decl` |

### 1.2 缺口（本 PRD 要补的）

| # | 缺口 | 证据 |
|---|---|---|
| G1 | 没有 `image_provider` 贡献种类 | `ContributionKind` 19 类无图像；`PROVIDER_KINDS` 无图像 |
| G2 | 配置源与角色不含图像 | `provider_sources.py:30 SOURCE_KINDS`、`:27 ROLES`、`:34 ROLE_EXPECTED_KINDS` |
| G3 | `/api/providers/available` 只聚合 4 类，`role_kinds` 写死 6 个角色 | `api/providers.py:45-95` |
| G4 | 前端 tab 与类型写死 4 类 | `providers.ts:4/201/209/216`、`ProvidersPage.tsx:17-22` |
| G5 | 没有图像 provider 协议 | `providers/base.py` 只有 LLM/TTS/STT/embedding/rerank/renderer |
| G6 | `POST /{source_id}/test` 对 `image` 没有分支（会落到"不支持"） | `api/providers.py:293+` |
| G7 | 三个生成插件把厂商分支写在自己源码里（`provider in {volcengine,dalle,flux,local}`），换网关必须改插件 | `plugins/ai_image_gen/main.py:102-119`、`plugins/ai_video_gen/main.py`、`plugins/ai_music_gen/main.py` |
| G8 | Engine 有多处对三个旧插件的硬编码引用（下架必须清理） | `api/plugin.py:972-974`、`plugins/generated_source.py:3/96`、`prompts.py:49`、`services/capability_matrix.py:79-81`、`services/requirements_service.py:352-354`、`tests/clipwright/test_ai_generation_integration.py`、`docs/development.md:116-128`、`docs/api_reference.md:538-540` |

### 1.3 前置 PRD 与本期的关系

前置文档（`6205f48`）已经论证了完整方向，但范围包含：

- `image_generate` / `timeline_insert_image` 两个 Engine 工具；
- 生成物落工作区 `materials/image/`、账本、素材检索合并；
- 提示词改为能力口径。

本期**只落地 Provider 契约与插件**，不实现上述工具与素材链路；前置文档继续作为第二期依据（其 §3.1/§3.2 与本文一致，本文按当前代码行号校准）。

---

## 2. 职责边界（硬约束）

| 职责 | 归属 | 说明 |
|---|---|---|
| `image_provider` 契约（`default_model` / `capabilities` / `generate` / `probe` 的形状与错误语义） | **Engine** | 与 `LLMProvider` 同级；不含任何厂商知识 |
| 贡献种类、注册表、启停/归属、`config_schema` 下发 | **Engine** | 复用既有 provider 机制 |
| Provider 源与角色（base_url / api_key / model / 默认模型 / 加密 / 掩码） | **Engine** | `provider_sources` 扩 kind/role |
| 前后端表单与 tab | **Engine API + 前端** | 前端只按 `kind=image` 与 `provider_meta` 渲染，**不写死厂商** |
| 尺寸/数量/参考图的能力协商与报错 | **Engine** | 按 provider 声明的 capabilities 判定，不静默改参数 |
| 生成产物落盘、命名、去重、账本（第二期） | **Engine** | 工作区是宿主资产 |
| gpt-images 的 URL、鉴权头、请求体、`size` 翻译、`b64_json`/`url` 两种响应解析、可重试判定 | **插件** | 只存在于新插件里 |
| 提示词内容 | **模型（主 Agent）** | Engine 不写提示词 |

推论（明确不做）：

- Engine 里**不得**出现 `if provider == "openai_gpt_image"` / `volcengine` / `dalle` 分支；
- Engine **不得**内置图像生成的 HTTP 调用；
- 插件**不得**自己决定产物路径、不得直接写时间线（第二期工具落地后同样适用）。

---

## 3. 设计

### 3.1 Engine：`image_provider` 契约（第 20 类贡献）

1. `plugins/contribution.py`：新增 `ContributionKind.IMAGE_PROVIDER = "image_provider"`（第 20 类，契约变更须评审；本文即评审材料）。
2. `plugins/provider_decl.py`：
   - `PROVIDER_KINDS` 增 `"image_provider"`；
   - `PROVIDER_CAPABILITY_KEYS` 增图像键：`sizes`、`max_n`、`reference_image`、`output_formats`、`max_prompt_chars`、`concurrency`、`cost_per_image_usd`、`size_dialect`；
   - 未知键仍只记录（不报错），沿用现状。
3. `providers/base.py`：新增协议（Engine 只定义、不实现）：

```python
@dataclass
class ImageProviderCapabilities:
    sizes: tuple[str, ...] | Literal["any"] = "any"
    max_n: int = 1
    reference_image: bool = False
    output_formats: tuple[str, ...] = ("png", "jpeg")
    max_prompt_chars: int = 4000
    concurrency: int = 1
    cost_per_image_usd: float | None = None
    size_dialect: str = "wxh"          # wxh | openai_size | auto

class ImageProvider(Protocol):
    name: str
    def default_model(self) -> str: ...
    def capabilities(self, model: str = "") -> ImageProviderCapabilities: ...
    async def generate(self, request: ImageGenerationRequest) -> list[GeneratedImage]: ...
    async def probe(self) -> bool: ...   # 可选
```

`ImageGenerationRequest`：`prompt / model / size / n / reference_image(bytes|None) / seed / extra`。
`GeneratedImage`：`data: bytes | None`、`url: str`、`mime: str`；两者至少一个。

4. `plugins/contribution_dispatch.py`：provider 校验分支补 `image_provider`，缺 `generate`/`default_model` → 该贡献被拒并给可读原因（与 `llm_provider` 同款，不连累同插件其它贡献）。
5. `plugins/override_policy.py`：若表里维护可覆盖种类，补 `image_provider`（当前无内置实现可覆盖，行为不变）。

### 3.2 Engine：配置源与角色

- `services/provider_sources.py`：
  - `SOURCE_KINDS` 增 `"image"`；
  - `ROLES` 增 `"image"`；
  - `ROLE_EXPECTED_KINDS["image"] = ("image",)`（角色只允许绑定图像源）。
- `api/providers.py`：
  - `GET /available`：聚合循环加 `ContributionKind.IMAGE_PROVIDER`，输出键 `image_provider`；`provider_meta` 自动带 `name`/`env_key`（沿用白名单键）；
  - `GET /available` 的 `role_kinds` 增 `"image": "image"`；
  - `GET /schemas?kind=image` 走既有 `schemas_for_kind("image_provider")`；
  - `POST /{source_id}/test`：`kind == "image"` 时调用 provider 的 `probe()`（未实现则返回 `{ok:false, detail:"该 provider 未声明 probe"}`，**不假装成功**；可选：`probe` 内部做一次最小生成，由插件决定，Engine 不硬编码）。
- 多源并存：同一时间可挂多个图像源；角色 `image` 绑定默认 `(source_id, model)`；未绑定时按"启用的源里第一个可用"解析并在结果里回传 `source_id/model`（第二期工具使用；本期 test 端点用显式 source）。

### 3.3 前端：新增「AI 生图」Provider Tab

1. `src/services/api/providers.ts`：
   - `ProviderKind` 增 `'image'`；`ProviderRole` 增 `'image'`；
   - `KIND_PROVIDER_KEY.image = 'image_provider'`；
   - `KIND_ROLES.image = ['image']`；
   - `ROLE_LABELS.image = '图像模型'`；
   - `AvailableProviders` 类型补 `image_provider?: string[]`。
2. `src/pages/admin/ProvidersPage.tsx`：`TABS` 增 `{ kind: 'image', label: 'AI 生图', hint: '文生图 / 图生图；模型与网关在此配置' }`。
3. 表单字段仍由 `schemaFor(schemas, 'image', provider)` 驱动（`config_schema`），**不新增厂商字段**；`secret: true` 的 `api_key` 走既有掩码/加密。
4. 未安装任何图像 provider 时：下拉为空 + 明示「（未安装）」，与现有 `providersForKind` 行为一致；不提供点了必报错的选项。
5. 该 tab 的「测试连接」按钮走 `POST /{source_id}/test`，按 `{ok, detail}` 展示。

### 3.4 插件交付

> 用户要求「先做一个 AI 生图 provider 插件，再做 OpenAI gpt-images 插件」。按当前架构，tab 由 Engine 的 `image_provider` 种类决定，插件负责**实现 provider**。为保证职责清晰、避免重复 HTTP 代码，推荐拆成两个插件：

| 插件 | 贡献 | 职责 | 依赖 |
|---|---|---|---|
| `plugins/image_providers`（AI 生图 provider 插件） | `kind: image_provider`，id `openai_compatible` | 通用 OpenAI 兼容实现：`POST {base_url}/images/generations`，支持 `b64_json` / `url`；`config_schema`：`base_url`、`api_key`（secret）、`model`、`timeout_sec`、`size_dialect`；`capabilities`：`sizes` 可配、`max_n`、`reference_image`、`cost_per_image_usd`（可空） | 无 |
| `plugins/image_gpt_images`（OpenAI gpt-images 插件） | `kind: image_provider`，id `openai_gpt_image` | 针对 `gpt-image-1` 的专有参数与响应：`size` 档位（`1024x1024` / `1536x1024` / `1024x1536` / `auto`）、`quality`、`background`、`output_format`、`n`；默认 `model=gpt-image-1`；响应以 `b64_json` 为主 | 无（可复制少量公共 HTTP 辅助，或后续抽 `plugins/image_common` 包） |

两个插件都只做「协议 ↔ 厂商」翻译，不写盘、不登记账本、不改时间线。

**备选方案**：一个插件 `image_providers` 内提供两个 provider id（`openai_compatible` / `openai_gpt_image`），减少重复代码。若产品坚持"两个插件"，按上表拆；见 §7 Q1。

### 3.5 下架 `ai_*_gen` 与清理

| 项 | 动作 | 说明 |
|---|---|---|
| `plugins/ai_image_gen/` | 删除目录 | 由 `image_providers` / `image_gpt_images` 取代 |
| `plugins/ai_music_gen/`、`plugins/ai_video_gen/` | 删除目录 | 音乐/视频生成能力**暂时缺失**（R2），提示词同步去宣传 |
| `api/plugin.py:972-974` | 删除三条 legacy 映射 | 旧 ui.json 面板路由 |
| `plugins/generated_source.py` | 删除（其账本逻辑迁第二期 `services/generated_assets.py`） | 当前仅旧三插件使用 |
| `prompts.py:49` | 改为能力口径，不点名 `ai_*_generate` | 第二期工具落地后写 `image_generate` |
| `services/capability_matrix.py:79-81` | 删除三个名字 | `TIER_MAP` 保持为空 |
| `services/requirements_service.py:352-354` | 概览改为按能力可用性生成 | 不再宣传音乐/视频 |
| `tests/clipwright/test_ai_generation_integration.py` | 删除，另立新测试 | 现测试直接 import 旧插件 |
| `docs/ai-generation-integration-plan.md`、`docs/development.md:116-128`、`docs/api_reference.md:538-540`、`docs/plugin-shell-audit-2026-11-07.md` | 标注「已下架，见本 PRD」 | 不改写历史，只加状态 |
| 前端 `src/features/assets/PluginPanel.tsx:9` 的 `ai_image_gen` 图标映射 | 删除该项（可选） | 插件移除后映射无意义 |
| 历史生成物 `PluginData/assets/*`、`PluginData/plugins/ai_*/generated.json` | **保留只读** | 不删用户文件；第二期账本迁移时提示，不自动删 |

---

## 4. 数据、安全与错误语义

- **密钥**：`api_key` 在 `config_schema` 标 `secret: true`，走既有 Fernet 加密与掩码回显（`provider_sources` 与插件 secret 同机制）。
- **URL/SSRF**：插件下载 `url` 响应前必须 `security.assert_public_url`；或只使用 `b64_json`（gpt-image-1 默认）。Engine 在第二期落盘时仍会再校验一次。
- **尺寸**：Engine 只按 `capabilities().sizes` 白名单校验；`"any"` 时接受 `WxH` 并做 64..4096 与偶数校验；非法**报错并列可用值**，不静默改。
- **`n` / 参考图 / 格式**：超限或不支持一律报错，不静默截断/忽略。
- **费用**：`cost_per_image_usd` 未知时返回 `None`，调用方记"未知"，不记 0。
- **错误码**：`NOT_FOUND`（未安装/无可用源，带"去装插件/去配源"提示）、`INVALID_ARGUMENT`（尺寸/参数）、`DEPENDENCY_MISSING`（密钥/网络）、`ERROR`（厂商返回错误，带可读 message）。
- **无密钥不生成**：`probe()`/`generate()` 在未配置时直接返回可读错误，不发请求。

---

## 5. 验收标准

1. 启动后端后，`GET /api/providers/available` 的键包含 `image_provider`，且 `role_kinds.image == "image"`。
2. 安装 `image_providers` 后，`GET /api/providers/available` 的 `image_provider == ["openai_compatible"]`，`provider_meta.image_provider.openai_compatible.name` 可读。
3. 前端「供应商」页出现 **AI 生图** tab；未安装任何图像插件时下拉显示空 + 安装提示；安装后可选 `openai_compatible`，表单字段来自 `GET /api/providers/schemas?kind=image`（`api_key` 为密码框）。
4. 保存一个 `kind=image` 源后，`POST /api/providers/{source_id}/test` 返回 `{ok, detail}`（未配置密钥时 `ok=false` 且原因可读）。
5. 安装 `image_gpt_images` 后，`image_provider` 列表出现 `openai_gpt_image`，默认模型 `gpt-image-1`；用真实/桩网关可完成一次生成（由插件测试覆盖，不经 Engine）。
6. 三个 `ai_*_gen` 目录删除后：后端全量插件测试、`tsc --noEmit`、相关 vitest 通过；`git grep 'ai_image_gen\|ai_music_gen\|ai_video_gen'` 仅剩文档/历史与只读迁移说明。
7. Engine 代码中无厂商名分支：`git grep -n 'volcengine\|dalle\|flux\|suno\|gpt-image' -- clipwright/` 为空（测试与文档除外）。
8. 贡献注册校验：缺 `generate` 的 `image_provider` 注册被拒且错误可读（新增测试）。
9. 前端只通过 `kind=image` + `config_schema` 渲染，无厂商字段硬编码（代码审查 + 新增 provider 不需要改前端）。

---

## 6. 测试计划

| 层 | 文件（建议） | 关键用例 |
|---|---|---|
| 契约/注册 | `tests/clipwright/test_image_provider_contract.py`（新） | 合法 provider 出现在 `ContributionRegistry`；缺 `generate` 被拒；`config_schema` 校验；`capabilities` 未知键只记录 |
| API | `tests/clipwright/test_provider_image_api.py`（新） | `/available` 含 `image_provider`/`role_kinds.image`；`/schemas?kind=image`；`/test` 对 image 的三种结果（ok / 未配置 / provider 无 probe） |
| 前端 | `src/pages/admin/ProvidersPage.image.test.tsx`（新）或扩展现有测试 | tab 出现、按 kind 过滤源、schema 驱动字段、未安装提示 |
| 插件 | `plugins/image_providers/tests`（若仓库有插件测试惯例则放 `tests/clipwright/`） | `b64_json` 与 `url` 两种响应；`size` 透传；超时/401 → 可读错误；`assert_public_url` 拒绝内网 URL |
| 下架完整性 | 静态测试 | 旧插件目录不存在；grep 无悬空 import；`prompts.py` 不含旧工具名 |

回归：`py -3.14 -m pytest tests -q -k "plugin or provider"`；前端 `npx tsc --noEmit` + 相关 vitest。

---

## 7. 风险与开放问题

- **R1（Frontend tab 是硬编码的）**：当前 `TABS` 写死 4 类，新增 image 必然改前端代码；这不算"厂商硬编码"，但新增 provider **种类**仍需发前端版本。若接受，本期按最小改动；若要求"后端加种类前端自动出现"，另立 tab 元数据 API（超出本期）。
- **R2（能力空窗）**：删除 `ai_*_gen` 后，主 Agent 没有生成图片/视频/音乐的工具；图像能力要等第二期 `image_generate` 落地。**需产品确认**：是否接受空窗，还是把第二期工具并入本期（则本 PRD 范围扩大到前置文档 §3.3/§3.4）。
- **R3（音乐/视频彻底缺失）**：两个插件下架后没有替代 provider；提示词/概览必须同步去宣传，避免"说了但不存在"。
- **R4（gpt-images 方言）**：`gpt-image-1` 的 `size` 只接受固定档位、`n` 上限与 `quality`/`background` 有专有取值；由 `image_gpt_images` 插件声明 `sizes` 与默认参数，Engine 不猜。
- **R5（计费）**：`cost_per_image_usd` 由插件声明，未知留空；本期不做账单对账。
- **Q1**：两个插件 vs 一个插件两个 provider id？（推荐前者以满足"两个插件"的原始要求；后者代码更少）
- **Q2**：`image` 角色是否需要在"工程文件/项目"里被引用，还是只做默认模型绑定？（本期只做源 + 角色绑定，不接生成链路）
- **Q3**：`probe()` 是否允许发起一次真实生成（会产生费用）？本文建议插件默认只做鉴权/模型列表探测，生成测试由显式按钮触发且提示费用。

---

## 8. 里程碑（建议拆 commit / PR）

| 里程碑 | 内容 | 验收 |
|---|---|---|
| M1 | Engine 契约：`IMAGE_PROVIDER` + `providers/base.py` 协议 + `provider_decl` + dispatch 校验 + 单测 | 缺方法被拒；合法 provider 可注册 |
| M2 | 配置源与 API：`provider_sources` kind/role、`/available`、`/schemas`、`/test` + 单测 | 验收 1、2、4 |
| M3 | 前端 tab 与表单：`ProviderKind/ROLES/TABS` + schema 驱动 + 测试 | 验收 3 |
| M4 | 插件 `image_providers`（openai_compatible） | 验收 2、5（桩网关） |
| M5 | 插件 `image_gpt_images`（openai_gpt_image / gpt-image-1） | 验收 5 |
| M6 | 下架 `ai_*_gen` 与全部引用清理、文档标注 | 验收 6、7 |

第二期（另 PRD/PR）：`image_generate` 工具、工作区落盘、账本、素材检索合并、`timeline_insert_image`（见前置 PRD §3.3/§3.4）。

---

## 9. 附录：gpt-image-1 请求/响应（插件侧，仅作实现参考）

- 端点：`POST {base_url}/images/generations`，`Authorization: Bearer <key>`。
- 请求：`{model: "gpt-image-1", prompt, size: "1024x1024" | "1536x1024" | "1024x1536" | "auto", n: 1..10, quality?: "low"|"medium"|"high"|"auto", background?: "transparent"|"opaque"|"auto", output_format?: "png"|"jpeg"|"webp"}`。
- 响应：`{data: [{b64_json?: string, url?: string, revised_prompt?: string}]}`；`b64_json` 需自行解码，`url` 需 `assert_public_url` 后再下载。
- 错误：HTTP 4xx/5xx 的 `{error: {message, type, code}}` → 插件转成可读中文错误，Engine 不改写厂商错误。
