# Provider 插件化与提示词覆盖 · 交付汇总（2026-11-02）

> 一页看清：**做到什么程度 / 怎么验 / 还剩什么 / 怎么回滚**。
> 权威细节在 [PRD](provider-pluginization-and-prompt-override-prd-2026-11-02.md)、
> [编码计划](provider-pluginization-coding-plan-2026-11-02.md)、
> [核实台账](provider-pluginization-verification-ledger-2026-11-03.md)。

## 1. 结论

PRD 的五个批次（Batch 1–5）与 Batch 0 核实，**代码侧全部落地**，剩 3 项**必须由人输入**才能推进
（详见 §4）。当前可在一条命令下自证：`py -3.14 scripts/verify_provider_pluginization.py` → **13/13 通过**。

**没有做、也不打算隐瞒的**：

- `python -m pytest tests -q` **未执行**（项目约定：测试由人跑）。因此「既有 1000+ 用例是否全绿」
  仍是未知，自检脚本只覆盖本次改造引入的运行期契约。
- `vitest` 未执行；前端只做了 `npx tsc --noEmit`。
- 协议适配类（OpenAI/Anthropic/CosyVoice/Qwen/MiniMax）**仍在 engine**，插件目前接管的是
  「注册 + 声明」；搬类属 W2/W3 收尾项（原因见 §5.1）。

## 2. 落地清单（按批次）

| 批次 | 交付物 | 关键提交（后端） |
|---|---|---|
| **B0 核实** | 台账 71 条逐条闭环（53 闭环 / 5 被裁定取代 / 4 误命中 / 2 仍开需人测 / 1 产品决策） | `e59b510` `741651c` `b00a3d6` |
| **B1 契约与注册表** | 内置让位收敛到 `providers/builtin_registry.py`；Provider 声明升一级字段（`config_schema`/`capabilities`）+ 校验器 `plugins/provider_decl.py`；`GET /api/providers/schemas`；`POST /api/capability/{name}` 通用能力网关；前端 schema 驱动表单 | `f3de7bd` `82a7e55` `30ea8f8` |
| **B2 LLM 插件** | `plugins/llm_openai_compatible/`（openai/ollama）、`plugins/llm_anthropic/`（anthropic）；`override_builtin` 接管 + 逐 id 让位 | `1d8f039` |
| **B3 TTS 双插件** | `plugins/tts_cosyvoice`、`plugins/tts_qwen`、`plugins/tts_minimax`（协议层）；`plugins/tts_feature`（`dub_script` 技能 + `create.dub` 界面）；`/api/voice/dub` 薄转发；`create.dub` slot（名单 14→15）+ 动态选项 `optionsFrom` + 控件值回传 + **`file` 上传配音控件** | `5f27973` `54c78a1` `56017c8` `08166da` |
| **B4 其他 provider** | `plugins/embedding_providers`、`plugins/rerank_providers`、`plugins/stt_providers`；`ContributionDecl.id` 放宽允许连字符 | `3abdf8d` |
| **B5 提示词覆盖** | `plugins/prompt_overrides.py`（符号自省 + 四关 + 双 dry-render + 静态纯度 + `safe_builder` 回落）；**26 常量 + 8 builder 全部接线**（`resolve_constant` / `prompts._c` / `_mg_constraint` / `_p` / `_hdr`）；`GET /api/plugin/prompt-symbols` + 插件管理页面板 | `2343737` `316d177` `54669a4` `60018ac` `2f08c2b` |

**当前 provider 归属（13 项自检实测）**

| 家族 | id → 归属 |
|---|---|
| llm | anthropic → `llm_anthropic`；openai / ollama → `llm_openai_compatible` |
| tts | cosyvoice → `tts_cosyvoice`；qwen-tts → `tts_qwen`；minimax → `tts_minimax` |
| embedding | openai / ollama / local → `embedding_providers` |
| rerank | http / local → `rerank_providers` |
| stt | whisper / faster-whisper → `stt_providers` |
| 技能 | `dub_script` → `tts_feature`（覆盖内置，禁用时自动恢复内置） |

## 3. 怎么验

### 3.1 一条命令（不需要 pytest）

```powershell
py -3.14 scripts/verify_provider_pluginization.py            # 全部 13 项
py -3.14 scripts/verify_provider_pluginization.py -k prompt  # 过滤
py -3.14 scripts/verify_provider_pluginization.py --trace    # 失败打印堆栈
```

13 项：`provider_owners`、`providers_available_and_schemas`、`providers_capabilities`、
`capability_gateway`、`prompt_symbols_wired`、`prompt_override_roundtrip`、
`prompt_runtime_path`、`prompt_builder_guards`、`dub_capability_boundary`、
`provider_plugin_fallback`、`skill_shadow_restore`、`all_families_fallback`、`tier_map_empty`。

### 3.2 人工回归（**待执行**）

照着 **[人工验收剧本](provider-pluginization-manual-verification-2026-11-03.md)** 敲即可（含期望输出与「回报什么」）：

```powershell
cd J:\Clipwright;  python -m pytest tests -q
cd J:\Clipweight-Client; npx tsc --noEmit; npm run test
```

### 3.3 上手看的地方

- 模型提供商页：源列表显示「插件：xxx / 内置实现」；源设置里有能力徽标（含「思考强度 / 思考内容」）。
- 插件管理页 →「提示词符号」：26 常量 + 8 builder 的 `wired` 与覆盖来源；被拒覆盖的原因。
- 新建制作 → 03 配音与字幕：配音入口由插件贡献（音色 + 自动配音 + 上传配音）。

## 4. 剩下 3 项（都需要人，不是我偷懒）

| # | 事项 | 前置条件 | 谁 |
|---|---|---|---|
| 1 | **B3-T6 删除 engine 的 TTS 编排与内置注册** | `pytest` 通过（删除后插件未加载的环境会**真的没有**配音能力，这是裁定 3 的预期） | 人跑测试 → 我执行删除（清单已备好：[removal-checklist](provider-pluginization-b3t6-removal-checklist.md)；**删除演练已做**，见其 §6：删除本身没问题，但要连带改 3 项自检 + 5 个测试文件 + 3 处文档，这批会合并在同一个 commit） |
| 2 | 台账 H-065（真实 LLM 路径 prompt 快照）、H-057（TTS 取消/降级端到端） | 同上，需真实路径跑一遍 | 人跑 → 我补记录 |
| 3 | 前端是否新增 STT Tab | 纯产品决策（PRD §14 第 3 条） | 产品 |

## 5. 已知偏差与解释

### 5.1 协议适配类仍在 engine

`providers/llm/*_provider.py`（68/55 行）、`services/voice.py` 的 `QwenTTSProvider` /
`CosyVoiceProvider` / `MiniMaxProvider` 没搬进插件。插件接管的是**注册与声明**。
理由：这些类只依赖 `providers.base` 与传输层，搬移不会让插件化更彻底，却会打断既有测试的
打桩接缝（`tests/clipwright/test_llm_provider_protocol.py:38` monkeypatch
`clipwright.services.llm.OpenAIChat`）。属独立重构（PRD §5.2 的「迁移过渡期」）。

### 5.2 上传配音入口已归插件（2026-11-02 补）

`create.dub` 现在覆盖「音色 + 自动配音 + 上传配音」三项。上传走新增的 `file` 控件
（`UIFile`）：控件值 = `File` 对象，**上传执行与回执归宿主**（`HomePage.pickAudio`：
客户端时长探测 → `assetApi.upload` → 去重提示），插件拿不到上传 API / 工作区路径 /
凭据，所以 `UIFile` **没有** `action` / `endpoint` 字段。宿主只认「值里出现 `File`
对象」，不耦合插件私有 key。

内置块（隐藏的 `<input type=file>` + 上传按钮）现在只在 `usePluginSlots('create.dub')`
**为空**时回落——这是过渡期写法，最终态是「无插件即无入口」，整块删除与 B3-T6 同批
（等人工跑测试）。上传中/失败/已上传的回执仍留在宿主侧（两种模式都显示），因为那是
宿主执行的回执，不是插件控件。

### 5.3 `role/source` 的插件归属是推导值

`source.plugin_id` 不落盘，每次读取时从门面 `owner_of()` 现算——落盘会与真实归属漂移。

## 6. 回滚

| 想退什么 | 怎么做 |
|---|---|
| 某个 provider 插件 | 禁用/删除该插件目录 → 下次 `ensure_*` 时**内置自动补位**（13 项自检已验证五类都成立） |
| 提示词覆盖 | 禁用覆盖插件 → `clear_plugin` 立即生效，回落到 `prompts.py` 默认值；默认值有 sha256 快照可对 |
| 整个改造 | 按 §2 的提交逐段 `git revert`（每个批次独立提交，删除动作会单独成一次提交以便精确回退） |

## 7. 交付完整性自评

- **代码**：五批次 + 核实全部落地，13 项运行期契约自检全绿。
- **文档**：PRD / 编码计划 / 核实台账 / 删除清单 / 本汇总；`api_reference`、`frontend-backend-parity`、
  `voice_tts`、`route_inventory` 已同步；`AGENTS.md` 新增 3 条不变量（#24 让位与网关、#25 `create.dub`、
  以及 #23 下补的 builder 四关）。
- **不敢声称的**：既有测试全绿（未跑）、前端 UI 实际观感（未点）、TTS 真实合成（无凭据）。

