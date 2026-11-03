# B3-T6 删除清单（等人工测试通过后执行）

> 依据：PRD §15.1.6 第 2 条「插件完成并通过 §15.3 验收后**立即删除**，不设观察期、不设门槛；
> **不保留**『临时打开内置实现』的开关」。本文件是**执行前的核对清单**，不是我擅自删除的结果。
>
> 为什么现在还不动：删完之后如果插件在某个环境没加载，配音能力会**整体消失**（这正是裁定 3
> 想要的效果），所以必须先用 `python -m pytest tests -q` 确认插件路径可用。这一步按项目约定
> 由人执行，我不跑测试。

## 0. 前置条件（缺一不可）

- [ ] `python -m pytest tests -q` 通过（人执行）
- [ ] 手工确认：装插件时 `call_skill("dub")` 解析到 `DubScriptOrchestrator`（插件实现），
      `/api/voice/dub` 走薄转发成功
- [ ] 手工确认：禁用 `tts_feature` 后，配音能力整体不可用且**有可读降级说明**
      （`未配音：当前没有可用的配音能力（TTS 功能插件未安装或被禁用）`），不出现 500

## 1. 删除：engine 的配音编排

| 位置 | 内容 | 删除后由谁承担 |
|---|---|---|
| `clipwright/services/voice.py:922` | `VoiceService.dub_script()` 整个方法 | `plugins/tts_feature/dub.py::DubScriptOrchestrator.execute()` |
| `clipwright/services/voice.py:936` | 方法体内的 `split_text(...)` 调用 | 同上 |
| `clipwright/skill/dub.py`（全文件 60 行） | builtin `DubScriptSkill` | 同上（插件贡献同名技能） |
| `clipwright/skill/builtin.py:193,199` | `from clipwright.skill.dub import DubScriptSkill` + `DubScriptSkill(),` | — |

**连带处理**：`split_text()`（`services/voice.py:127`）是切句策略。裁定 3 要求编排（含切句）
离开 engine，因此要把这个函数**整体搬进** `plugins/tts_feature/`（插件已 `import` 它，改成内部副本），
engine 侧删除。注意：`services/voice.py` 的模块 docstring 第 4 行也要同步删掉 `split_text` 的提及。

## 2. 删除：内置 TTS provider 的默认注册

| 位置 | 内容 | 说明 |
|---|---|---|
| `clipwright/providers/tts/__init__.py:39-75` | `ensure_tts_providers_registered()` 与 `_make()` | 函数删除；`resolve_tts_entry()` / `tts_provider_names()` 里的「懒注册」调用一并去掉 |
| `clipwright/main.py:~169` | `ensure_tts_providers_registered()` 调用 | 启动不再注册任何内置 TTS provider |
| `clipwright/api/providers.py:57,61` | import + 调用 | 保留 `tts_provider_names`（下拉仍要列出**插件**提供的 provider） |
| `clipwright/api/settings.py:81-82` | import + 调用 | 同上 |

**保留不动**（这些是基座，不是能力）：

- `providers/builtin_registry.register_builtin_once()`（让位机制本身）——**别的 provider 家族仍在用**
- `services/voice.py` 的 `BaseVoiceProvider` / `QwenTTSProvider` / `CosyVoiceProvider` / `MiniMaxProvider`
  等协议类（Batch 3 的分阶段策略：协议类暂留 engine，搬移是独立工作项）
- `PublicUploadManager`（已降级为通用服务）
- `providers/tts/__init__.py` 的 `TtsProviderEntry` / `normalize_tts_name` / `_TTS_ALIASES`

## 3. 删除后的预期状态（验收断言）

- [ ] 空环境启动：`ContributionRegistry.list(TTS_PROVIDER)` **为空**（没有任何 TTS provider）
- [ ] 空环境启动：`SkillRegistry.get("dub_script")` 为 `None`；`skill_resolve("dub")` 为 `None`
- [ ] 主 agent 请求配音 → `not_found`，AudioAgent 写「未配音：当前没有可用的配音能力…」
- [ ] `/api/voice/dub` → **503**「配音能力当前不可用」
- [ ] 装 `tts_feature` + `tts_cosyvoice` 后：`dub_script` 由插件提供、`cosyvoice` 由协议插件提供，
      导入→克隆→合成→dub 全链路可用
- [ ] `qwen-tts` / `minimax` 两个 id **消失**（本轮只把 cosyvoice 搬进插件）——这是**有意的行为变化**：
      若仍需它们，得先补一个 tts 协议插件接管这两个 id，否则删完用户会少两个可选 provider。
      ⚠️ **这是删除前必须先决定的一条**（见下）

## 4. 需要先拍板的一点

删除内置注册会让 `qwen-tts` / `minimax` 从下拉里消失（现在只有 `cosyvoice` 有插件）。
两条路：

1. **先补插件**：再建一个 `plugins/tts_qwen/`（或把 tts_cosyvoice 扩成多 id）接管 `qwen-tts`、
   `minimax`，然后再删内置 —— 用户无感，工作量约半天；
2. **直接删**：接受少两个 provider，等以后有需求再补插件 —— 工作量 0，但属**功能回退**。

我的建议是 **1**（先补再删），除非你确认这两个 provider 已不再使用。

## 5. 回滚方式

按 PRD §15.1.6 第 3 条：**不保留运行时开关**。要回到旧行为就回滚对应提交（`git revert`），
这也意味着删除动作要和插件提交**分开**（删除单独一个 commit，便于精确 revert）。
