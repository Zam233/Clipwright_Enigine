# Provider 插件化与提示词覆盖 · 交付汇总（2026-11-02）

> 一页看清：**做到什么程度 / 怎么验 / 还剩什么 / 怎么回滚**。
> 权威细节在 [PRD](provider-pluginization-and-prompt-override-prd-2026-11-02.md)、
> [编码计划](provider-pluginization-coding-plan-2026-11-02.md)、
> [核实台账](provider-pluginization-verification-ledger-2026-11-03.md)。

## 1. 结论

PRD 的五个批次（Batch 1–5）与 Batch 0 核实，**代码侧全部落地**，**B3-T6（摘 engine 的 TTS 编排与内置注册）已于 2026-11-03 执行完毕**。
当前可两条命令自证：`py -3.14 scripts/verify_provider_pluginization.py` → **13/13**；
`py -3.14 scripts/verify_tts_teardown.py` → 「没有插件就没有 TTS 能力」+ 切句行为不变。

**没有做、也不打算隐瞒的**：

- `python -m pytest tests -q` 已在用户授权下由 agent 跑过：**4946 passed / 44 failed**。44 个失败
  **全是既存失败**（用改造前基线 worktree 同环境对照，失败集合完全相同），与本次改造无关；
  本次改造引入的 13 个陈旧用例已修（见 §3.2 与台账「pytest 实跑」）。
- `vitest` **未执行**；前端只做了 `npx tsc --noEmit`（退出码 0）。
- 协议适配类（OpenAI/Anthropic/CosyVoice/Qwen/MiniMax）**仍在 engine**，插件目前接管的是
  「注册 + 声明」；搬类属 W2/W3 收尾项（原因见 §5.1）。

## 2. 落地清单（按批次）

| 批次 | 交付物 | 关键提交（后端） |
|---|---|---|
| **B0 核实** | 台账 71 条逐条闭环（53 闭环 / 5 被裁定取代 / 4 误命中 / 2 仍开需人测 / 1 产品决策） | `e59b510` `741651c` `b00a3d6` |
| **B1 契约与注册表** | 内置让位收敛到 `providers/builtin_registry.py`；Provider 声明升一级字段（`config_schema`/`capabilities`）+ 校验器 `plugins/provider_decl.py`；`GET /api/providers/schemas`；`POST /api/capability/{name}` 通用能力网关；前端 schema 驱动表单 | `f3de7bd` `82a7e55` `30ea8f8` |
| **B2 LLM 插件** | `plugins/llm_openai_compatible/`（openai/ollama）、`plugins/llm_anthropic/`（anthropic）；`override_builtin` 接管 + 逐 id 让位 | `1d8f039` |
| **B3 TTS 双插件** | `plugins/tts_cosyvoice`、`plugins/tts_qwen`、`plugins/tts_minimax`（协议层）；`plugins/tts_feature`（`dub_script` 技能 + `create.dub` 界面 + `split_text`）；`/api/voice/dub` 薄转发；`create.dub` slot（名单 14→15）+ 动态选项 `optionsFrom` + 控件值回传 + **`file` 上传配音控件**；**B3-T6：engine 的 `dub_script()` / `skill/dub.py` / `ensure_tts_providers_registered()` 已删除** | `5f27973` `54c78a1` `56017c8` `08166da` `+ B3-T6 提交` |
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

### 3.2 pytest 已实跑（2026-11-03，用户授权后由 agent 执行）

```powershell
cd J:\Clipwright; py -3.14 -m pytest tests -q
# 4946 passed, 44 failed, 7 skipped, 1 xfailed  （547.89s）
```

结论要说得准确：**「本次改造没有新增失败」成立，「全量全绿」不成立**。

- 首跑 57 失败；用改造前基线（`741651c`，工作树 + 同 `.env`）跑同一批用例得 44 失败，
  差集 13 个 = 本次改造引入的**测试陈旧**（`_dispatch_register` 多了 `policy=`、
  `/api/voice/dub` 改薄转发、音色不存在改成中文 + 结构化错误），已在同一提交里修好，
  三个模块复跑 41 passed；
- 修完全量复跑 = 44 failed，与基线集合**完全相同**（把基线的 44 个 id 拿到当前代码跑：
  44 failed 一个不差）；
- 44 个既存失败的分布与建议见台账文末「pytest 实跑」一节（kernel ~19、子 agent/管线 ~11、
  web* ~9 与 `.env` 的 DeepSeek key 相关、其余零散）。

前端仍待你跑：`cd J:\Clipweight-Client; npx tsc --noEmit; npm run test`（`tsc` 我已跑过，退出码 0）。

### 3.3 上手看的地方

- 模型提供商页：源列表显示「插件：xxx / 内置实现」；源设置里有能力徽标（含「思考强度 / 思考内容」）。
- 插件管理页 →「提示词符号」：26 常量 + 8 builder 的 `wired` 与覆盖来源；被拒覆盖的原因。
- 新建制作 → 03 配音与字幕：配音入口由插件贡献（音色 + 自动配音 + 上传配音）。

## 4. 剩下 2 项 + 1 个遗留（都不阻塞本 PRD 的代码交付）

| # | 事项 | 前置条件 | 谁 |
|---|---|---|---|
| 1 | ~~B3-T6 删除 engine 的 TTS 编排与内置注册~~ | **已于 2026-11-03 执行**（`pytest` 由 agent 跑过；用户授权） | ✅ 完成，见 [removal-checklist](provider-pluginization-b3t6-removal-checklist.md) |
| 2 | 台账 H-057（TTS 取消的真机端到端） | 需要真机点一次停止（错误结构侧已由脚本覆盖） | 人 |
| 3 | 前端是否新增 STT Tab（H-044） | 纯产品决策（PRD §14 第 3 条） | 产品 |
| 遗留 | 44 个**既存** pytest 失败（kernel ~19 / 子 agent·管线 ~11 / web* ~9 与 `.env` 的 DeepSeek key 相关 / 其余零散） | 与本改造无关（改造前后失败集合完全相同）；修它们属另一件事 | 待你定要不要安排 |

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

- **代码**：五批次 + 核实全部落地，B3-T6 已执行；13 项运行期契约自检全绿。
- **文档**：PRD / 编码计划 / 核实台账 / 删除清单 / 本汇总；`api_reference`、`frontend-backend-parity`、
  `voice_tts`、`route_inventory`、`frontend-backend-parity §11`、`plugin_authoring §3.2`、`structure`、
  `development` 已同步；`AGENTS.md` 新增不变量（后端 #24 让位与网关、#25 控件值回传/执行归宿主；
  前端 #24 schema 表单、#25 `create.dub`），并同步「engine 已无 TTS 编排/内置注册」。
- **不敢声称的**：`vitest` 全绿（未跑）、前端 UI 实际观感（未点）、TTS 真实合成（无凭据）。

## 8. 收尾状态（2026-11-03）

**能自证的部分已全部自证**：

| 证据 | 命令 | 结果 |
|---|---|---|
| 运行期契约 13 项 | `py -3.14 scripts/verify_provider_pluginization.py` | **13/13 通过**（删除后再跑一次仍 13/13） |
| B3-T6 行为边界 | `py -3.14 scripts/verify_tts_teardown.py` | 通过：插件在 → 三 id + 技能齐全；摘掉 → 列表空 / `resolve` None / `/api/voice/dub` → 503 + 可读原因；切句不变 |
| 全量 pytest（删除后） | `py -3.14 -m pytest tests -q` | **44 failed / 4948 passed / 7 skipped / 1 xfailed**；44 个与删除前**同一批既存失败**，无一落在本次改造面 |
| 前端类型 | `cd J:\Clipweight-Client; npx tsc --noEmit` | 退出码 0 |
| 后端可导入 | `py -3.14 -c "import clipwright.main"` | 正常 |
| 提示词搬运 | 26 常量 + 8 builder 值 vs 搬运前 sha256 | 差异 0 |

**只剩两件必须由人输入**：

1. H-057 的真机取消验证（错误结构侧脚本已覆盖，取消只能人工点一次停止）；
2. H-044：前端是否新增 STT Tab（纯产品决策）。

另有两条**有意保留**：H-072（`requirements_service` 里的第二处模块级提示词，无覆盖需求，
记录不迁）、以及 44 个与本改造无关的**既存** pytest 失败（要不要清属另一件事）。

