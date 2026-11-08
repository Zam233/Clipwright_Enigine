# 插件外壳化审计（2026-11-07）

> 角色：规划专家（本文只做审计与裁定，不改代码、不跑 pytest/vitest、不 commit）
> 触发：用户 2026-11-07 发现 GenUI 走错方向 —— engine 里有一条 549 行的 `genui` 专属链路，
> 插件 `plugins/genui/` 只贡献一条提示词。要求「顺便排查其他插件有没有同样的问题」。
> 审计范围：`J:\Clipwright\plugins\` 下**全部 44 个**含 `plugin.yaml` 的插件
> （`.mimosa/` 是 DSH 钩子状态目录，不是插件，不计）。
> 配套：[GenUI 迁移 PRD](genui-plugin-migration-prd-2026-11-07.md)、
> [GenUI 迁移 coding plan](genui-plugin-migration-coding-plan-2026-11-07.md)、
> [插件化方案](pluginization-plan-2026-09-10.md)、[Provider 插件化交付汇总](provider-pluginization-delivery-summary-2026-11-02.md)、
> [核实台账](provider-pluginization-verification-ledger-2026-11-03.md)。

---

## 0. 一句话结论

**44 个插件里只有 26 个是真插件；13 个是「半壳」（插件登记，能力主体在 engine），5 个是「空壳」
（插件登记，主体在 engine 或根本没有消费方）。** 病灶不是一个 GenUI，而是 2026-11-02 那轮
provider 插件化**只搬了注册与声明、没搬协议实现**（交付汇总自己承认「协议适配类仍在 engine」，
见 `provider-pluginization-delivery-summary-2026-11-02.md:20-21`、`:97-103`），以及三条
钩子契约（`pre_render` / `animation_catalog_extend` / `diagram_renderer_extend`）的**入参键名与
插件写入的键名对不上**，导致 4 个插件的贡献自注册之日起从未生效。

**整改不能塞进 B0–B6**（已冻结，且 B3 的字段泛化与 provider 类无关）。本文给出独立的
**B7 · 协议类归位**（10.0 人日 / 6 任务）与 **B8 · 失效钩子与渲染器归位**（10.0 人日 / 7 任务），
已同步进 coding plan（全计划 30 任务 / 17.0 人日 → **43 任务 / 37.0 人日**）。
**genui 本体不重复分析**，直接引用 PRD §2.1/§2.2 与 coding plan B0–B6。

---

## 1. 判定标准与可复现方法

### 1.1 三类判定（与 PRD §1.2 的 D1–D4 同源，泛化到所有 kind）

| 判定 | 定义 | 反例（就是本条要抓的） |
|---|---|---|
| **真插件** | 能力主体（HTTP 请求、解析、渲染、编排、知识表）在插件目录内；engine 只有通用注册表 + 通用协议/渲染器 | — |
| **半壳** | 插件贡献的是「工厂 / 壳 / 数据」，关键实现类仍在 `clipwright/`（同 id、同名、同行为） | `tts_cosyvoice` 注册 `lambda: _voice.CosyVoiceProvider(...)` |
| **空壳** | 插件登记后**没有任何主体逻辑**：engine 里另有一份实现并真正被调用，或插件的贡献根本无人消费 | `cloud_render` 往上下文写一个全仓无人读的键 |

`未核实` 只用于「没有读过源码」的条目。本次 44 个插件的 manifest 与 entry 模块**全部读过**，
`未核实 = 0`；对只读了关键段落的实现类插件，在 §2 的证据列写明「已读行段」，不写"未核实"来含混。

### 1.2 六条可执行信号（S1–S6，逐条核实、不凭信号下结论）

| # | 信号 | 判据 |
|---|---|---|
| **S1** | engine 里有专属实现文件 | `clipwright/` 下存在与能力同名的模块/类，且插件侧只 import 它 |
| **S2** | 插件 import engine 实现 | 插件 `main.py` 的 `contributions()` 里出现 `from clipwright.services.* import <Impl>` |
| **S3** | 双实现 / 双注册 | 插件注册的 `(kind,id)` 在 engine 的 `ensure_*_registered()` 里**也注册了一遍** |
| **S4** | vendor 分支 / vendor 字面量 | engine 里 `if provider == "<插件id>"`，或 `Literal["<id>", ...]`、模型映射表 |
| **S5** | engine 反向知道插件目录/ID | engine 硬编码 `plugins/<id>/...` 路径或 `HEADLESS_SHELL_ID = "<插件id>"` |
| **S6** | 贡献无人消费（失效） | 钩子写入的键与 engine 读取的键不一致；或该 `(kind,id)` 全仓无消费点 |

**判定规则**：S1+S2（或 S3）同时成立 → `半壳`；只有 S1/S3 而插件侧零实现 → `空壳`；
S6 成立（贡献不生效）且实现不在插件 → `空壳`；S4/S5 单独成立但能力主体在插件 → 仍判 `真插件`，
在 §4 记为横向缺陷。

### 1.3 实际使用的命令（可直接复现）

```powershell
cd J:\Clipwright

# ① 全量清单（44 条）
Get-ChildItem plugins -Recurse -Filter plugin.yaml |
  ForEach-Object { $_.Directory.Name + "  " + $_.FullName }

# ② 每个插件的 contributions 实际返回（读入口模块的 kind 字面量）
Get-ChildItem plugins -Recurse -Include main.py,animations.py,presets.py |
  ForEach-Object { ... [regex]::Matches($c,'ContributionKind\.(\w+)') ... }

# ③ 反查 engine：逐个插件 id 找实现（本次逐条跑过 40 个 id）
$ids = 'ai_image_gen','ai_music_gen','ai_video_gen','bgm_library','cloud_render','custom_style',
        'diagram_style','embedding_providers','gaming_category','gif_sticker','headless_chrome',
        'kinetic_typography','llm_anthropic','llm_openai_compatible','local_material',
        'logic_animations','lottie_animations','lut_presets','mg_animations','my_animations',
        'news_category','pexels_material','pixabay_material','platform_export','pv_engine',
        'rerank_providers','shortform_category','stt_providers','subagent_demo','subtitle_translate',
        'text_animations','tts_cosyvoice','tts_feature','tts_minimax','tts_qwen','tutorial_category',
        'unsplash_material','voice_ext','web_media','whisper_stt'
foreach ($id in $ids) {
  $h = Get-ChildItem clipwright -Recurse -Include *.py,*.json |
       Select-String -Pattern ([regex]::Escape($id)) -SimpleMatch -CaseSensitive:$false
  if ($h) { "### $id : $($h.Count)"; $h | Select-Object -First 12 |
            ForEach-Object { "  $($_.Path.Replace('J:\Clipwright\','')):$($_.LineNumber): $($_.Line.Trim())" } }
}

# ④ engine 侧 vendor 字面量/分支（排除 providers/ —— 那里本来就是实现方）
Get-ChildItem clipwright -Recurse -Include *.py |
  Select-String -Pattern '"anthropic"|"ollama"|"cosyvoice"|"minimax"|"qwen-tts"|"openai"' |
  Where-Object { $_.Path -notmatch 'providers\\' -and $_.Path -notmatch '__pycache__' }

# ⑤ 钩子契约：写入键 vs 读取键
Select-String -Path clipwright\animation\catalog.py -Pattern 'get\("extensions"\)'
Select-String -Path clipwright\animation\diagram_svg.py -Pattern 'get\("renderers"\)'
Select-String -Path clipwright\api\render.py -Pattern 'execute_async\(HookPoint\.(PRE|POST)_RENDER'

# ⑥ 两套同义表（证明双实现的存在）
Select-String -Path clipwright\api\render.py -Pattern '^_EXPORT_PRESETS|^_PLATFORM_EXPORT_COUNTS'
Select-String -Path plugins\platform_export\main.py -Pattern 'PLATFORM_PRESETS'
```

### 1.4 方法与局限

- 「主体在 engine」必须**两侧同时给证据**：插件侧的 import/工厂行 + engine 侧的实现类行号。
- 只读源码、不运行 = 不核验「钩子被调用时的真实入参」。S6 的结论来自**静态对账**
  （写入键 vs 读取键）；B7/B8 实施前须用一条运行时断言复验（§3 每个整改项都给了命令/用例）。
- 「真插件」不评价功能好坏，只评价**能力归属**。例如 `voice_ext` 实现完整却根本没接入
  `tts_provider` 面（§3.15），归属对了、接线错了，另记。

---

## 2. 全量清单（44 / 44）

### 2.1 逐插件判定

| # | 插件 id | 声明 kind（manifest） | 判定 | 插件侧证据 | engine 侧证据 | 一句话理由 |
|---|---|---|---|---|---|---|
| 1 | `ai_image_gen` | tool, material_source | **真插件** | `plugins/ai_image_gen/main.py:56-119`（工具 + 四厂商实现，`:109` 分支也在插件内） | 仅 `services/requirements_service.py:346`、`services/capability_matrix.py:79` 的**能力名清单** | Seedream/DALL·E/Flux/SD 协议全在插件，engine 只列能力名 |
| 2 | `ai_music_gen` | tool, material_source | **真插件** | `main.py:36-60`（火山 V4 签名）、`:1-8` | `capability_matrix.py:80` | 协议与签名全在插件 |
| 3 | `ai_video_gen` | tool, material_source | **真插件** | `main.py:28-45`（Seedance 创建/轮询） | `capability_matrix.py:81` | 异步任务协议在插件 |
| 4 | `bgm_library` | material_source ×2 | **真插件** | `main.py:29-75`（Freesound）、`:81-116`（本地库） | engine 0 命中 | 两个素材源都在插件内实现 |
| 5 | `cloud_render` | hook | **空壳** | `main.py:29-41` 只往 `context["cloud_render"]` 写配置 | `api/render.py:603` 只传 `{"task_id"}`；全仓无 `cloud_render` 消费方；远程渲染真正实现见 `services/remote_render.py:59`、`providers/render/__init__.py:173` | 钩子无人读；云渲染能力其实在 engine 的 remote backend |
| 6 | `custom_style` | style_interpreter | **真插件** | `main.py:49-132`（`interpret()` 完整配色逻辑） | `services/style_interpreter.py:97-129` 通用调度；`plugins/contribution_dispatch.py:124-129` | engine 只做多注册与优先级 |
| 7 | `diagram_style` | hook | **真插件** | `presets.py:23-89`（5 套主题）、`:92-97` | `animation/diagram_svg.py:129-139` 执行 `DIAGRAM_STYLE_PRESET` 并取 `presets` | 配色知识在插件，engine 只消费 |
| 8 | `embedding_providers` | embedding_provider ×3 | **半壳** | `main.py:21-34` 直接 import 引擎类并注册 | `providers/embedding/__init__.py:17-56`（实现）、`:128-141`（同三 id 再注册） | 双注册；OpenAI/Ollama/local 实现在 engine |
| 9 | `example_caption_plugin` | tool, skill | **真插件** | `main.py:36-41`、`:49-70`（切分工具） | engine 0 命中 | 示例工具/技能实现在插件 |
| 10 | `example_full` | tool + ui_slot×4 + export_preset | **真插件** | `main.py:48-80`（声明式 layout，无 JS） | engine 0（前端 slot 消费） | 全贡献类型示例，实现在插件 |
| 11 | `gaming_category` | category | **真插件** | `main.py:17-46`（`translate_persona` 等 6 个方法） | engine 0；`category/registry.py:37-58` 通用表 | 类型知识（节奏/分镜/标注）全在插件 |
| 12 | `genui` | prompt | **空壳** | `main.py:73-81` 只有 1 条 `PROMPT` | `schema/genui.py:29`（13 类白名单）、`:345`（`sanitize_spec`），共 549 行；`services/requirements_service.py` 20 处（`:1185-1187`、`:1290-1292` 直接 import） | 本次案发对象；已在 PRD 立目标（B0–B6），§3.1 只引用不重复 |
| 13 | `gif_sticker` | material_source | **真插件** | `main.py:29-40`（Giphy 客户端） | engine 0 命中 | 实现在插件 |
| 14 | `headless_chrome` | renderer_backend, ui_slot | **半壳** | `main.py:36-48` 注册的是 `headless_browser.find_shell/install_shell` 的字典；`:108-125` setup 调 engine 安装 | `services/headless_browser.py:30`（硬编码 `HEADLESS_SHELL_ID`）、`:80-93`、`:240`；`api/render.py:1155-1183`（两个端点） | 340 行能力主体在 engine，插件是壳 |
| 15 | `kinetic_typography` | hook, prompt, animation ×6 | **半壳** | `main.py:76-82` 写 `ctx["catalog"]`；`:24-73` CSS 只是字符串 | `animation/catalog.py:116` 读的是 `ctx["extensions"]`；全仓无 CSS 消费方（`word_emphasis` 0 命中） | 目录钩子键名不匹配；6 个动画无 keyframes，CSS 从不执行 |
| 16 | `llm_anthropic` | llm_provider | **半壳** | `main.py:23-31` import 引擎 `AnthropicProvider` | `providers/llm/anthropic_provider.py`（实现）；`providers/llm/__init__.py:16-28`（同 id 内置注册）；`services/llm.py:727`（vendor 分支） | 注册在插件、实现在 engine |
| 17 | `llm_openai_compatible` | llm_provider ×2 | **半壳** | `main.py:27-38` | `providers/llm/openai_provider.py`；`providers/llm/__init__.py:16-28`；`services/llm.py:897`、`:950` | 同上 |
| 18 | `local_material` | material_source | **半壳** | `main.py:20` import `LocalFolderSource`、`:55` 实例化 | `material/local_source.py:55-177`（类实现 123 行） | 插件只做配置读取与注册 |
| 19 | `logic_animations` | hook, prompt | **半壳** | `diagrams/all.py:1-9` 自述「11 个副本已删，仅留 mindmap」、`:22-60`；但 `main.py:27-40` 仍宣称 12 种 | `animation/diagram_svg.py:1170-1878`（radar/gantt/venn3/heatmap/sankey/concept/codeblock/datatable/quote/compcard/orgchart 全在 engine）、`:301-305` 内置 presets | 声明面比实现面大 11 倍 |
| 20 | `lottie_animations` | hook ×2, prompt | **空壳** | `main.py:50-61` 写 `ctx["catalog"]`；`:64-82` 把 `renderers` 写成 **dict** | `catalog.py:116` 要 `extensions` **list**；`diagram_svg.py:212` 要 `renderers` **list**（元素需 `id`+`renderer`） | 两个钩子都拿不到东西，也从不注册 ANIMATION 贡献 → 无任何可达能力 |
| 21 | `lut_presets` | style_interpreter, hook | **真插件** | `main.py:74-97`（`interpret()` 推荐 LUT） | `services/style_interpreter.py:97-129` | 实现有效；`:99-101` 的空钩子是残留（§4.2） |
| 22 | `mg_animations` | hook ×2, animation ×4 | **半壳** | `main.py:123-144`（4 个 `AnimationDef`）+ `animations/*.json` | `animation/mg_renderer.py:598`、`:603`、`:619-624` 直接把 `plugins/mg_animations/animations` 写进搜索路径 | 数据在插件，但 engine 反向依赖插件目录（S5） |
| 23 | `my_animations` | animation ×2 | **真插件** | `main.py:36-85`（两个 `AnimationDef` 含 keyframes） | `animation/renderer.py:70-75` 通用按 `defs[animation_id]` 渲染 | 数据驱动，engine 只做通用渲染 |
| 24 | `news_category` | category | **真插件** | `main.py:19-45`（6 个方法） | engine 0 命中 | 类型知识在插件 |
| 25 | `pexels_material` | material_source | **真插件** | `main.py:29-49`、`:68-80`（视频/图片两路） | engine 0 命中 | API 客户端在插件 |
| 26 | `pixabay_material` | material_source | **真插件** | `main.py:31-57` | engine 0 命中 | 同上 |
| 27 | `platform_export` | hook ×2 | **空壳** | `main.py:17-60`（6 个平台预设）、`:63-89`（钩子读 `context["platform"]`/`["settings"]`） | `api/render.py:603` 只传 `{"task_id"}`（键根本不存在）；`:973-976` `_PLATFORM_EXPORT_COUNTS`、`:1091` `_EXPORT_PRESETS` | 钩子空转；engine 自己有两套平台表 |
| 28 | `pv_engine` | tool ×9, skill ×2 | **真插件** | `main.py:39-56`、`:910-922`；`adapter.py` 468 行 | 仅 `agents/animation_agent.py:69` 的能力名清单 | 探测/审计/渲染编排全在插件 |
| 29 | `rerank_providers` | rerank_provider ×2 | **半壳** | `main.py:18-24` import 引擎类 | `providers/rerank/__init__.py:19-51`（实现）、`:172-184`（同 id 内置注册） | 双注册 |
| 30 | `sandbox_demo` | tool ×2, hook, ui_slot, prompt | **真插件** | `main.py:63-79` | engine 只提供子进程档基础设施 | 沙箱示例实现完整 |
| 31 | `shortform_category` | category, hook | **真插件** | `main.py:29-83`（6 个方法 + 竖屏尺寸） | engine 0；`category/registry.py` 通用表 | 类型知识在插件；`:86-92` 的 pre_render 钩子同样空转（次要） |
| 32 | `stt_providers` | stt_provider ×2 | **空壳** | `main.py:18-45`（`_call_whisper`/`_call_faster` 与引擎**逐字重复**） | `providers/stt/__init__.py:43-68`（同 id 内置注册）、`:48-55`（同两份 `_call_*`）；`services/stt.py:110` 用注册表 | 双实现：插件抄了 engine 的两个闭包 |
| 33 | `subagent_demo` | agent | **真插件** | `main.py:26-58`（`SummarizerAgent.execute`） | `agents/registry.py` 通用注册 | Agent 实现在插件 |
| 34 | `subtitle_translate` | tool, skill | **真插件** | `main.py:26-60`（LLM/DeepL 两路）、`:61+` | engine 0；LLMService 属平台能力 | 翻译编排在插件 |
| 35 | `text_animations` | tool ×25 | **真插件** | `animations.py:45-585`（FFmpeg 滤镜与参数） | engine 0 命中 | 25 个工具的渲染实现全在插件 |
| 36 | `tts_cosyvoice` | tts_provider | **半壳** | `main.py:21-36`：`factory=lambda ...: _voice.CosyVoiceProvider(...)` | `services/voice.py:455-551`（类实现）；`:729-734`（模型映射）、`:771`（qwen 专属分支） | 典型「插件提供工厂、类在 engine」 |
| 37 | `tts_feature` | skill, ui_slot | **真插件** | `main.py:32-68`；`dub.py`（切句/逐段合成/时间轴） | `services/voice.py:7` 注释「编排已迁至插件」；`skill/builtin.py:193` | **正面样板**：编排在插件、协议调度在 engine |
| 38 | `tts_minimax` | tts_provider | **半壳** | `main.py:18-33`（factory → `_voice.MiniMaxProvider`） | `services/voice.py:552-656`（类实现） | 同 36 |
| 39 | `tts_qwen` | tts_provider | **半壳** | `main.py:20-35`（factory → `_voice.QwenTTSProvider`） | `services/voice.py:371-454`（类实现） | 同 36 |
| 40 | `tutorial_category` | category | **真插件** | `main.py:29-75`（6 个方法） | engine 0 命中 | 类型知识在插件 |
| 41 | `unsplash_material` | material_source | **真插件** | `main.py:29-60`（Unsplash 搜索 + 署名元数据） | engine 0 命中 | 实现在插件 |
| 42 | `voice_ext` | tool | **真插件** | `main.py:21-83`（ElevenLabs/Azure 请求与落盘全实现） | engine 0 命中 | 实现完整，但**没接入 `tts_provider` 面**（§3.15 另记） |
| 43 | `web_media` | tool ×2 | **真插件** | `main.py:44-48`；`web_images.py:43-150`（抽图/落盘/过滤）；`fact_need.py:36-177` | `agents/animation_agent.py:118`、`:216` 只按能力名 `call_tool` | 能力在插件；engine 只留 SSRF/robots/落盘基座（`services/web_assets.py`） |
| 44 | `whisper_stt` | tool, skill | **半壳** | `main.py:54-76`：`STTService().transcribe(...)` 的薄包装 | `services/stt.py:59`（STTService）、`:214-283`（两份 whisper 实现）；`tool/transcribe.py:16-18` 已有内置 `whisper_transcribe` | 与内置工具双实现，插件只是换名包装 |

### 2.2 判定统计

| 判定 | 数量 | 占比 | 插件 |
|---|---|---|---|
| **真插件** | **26** | 59.1% | ai_image_gen / ai_music_gen / ai_video_gen / bgm_library / custom_style / diagram_style / example_caption_plugin / example_full / gaming_category / gif_sticker / lut_presets / my_animations / news_category / pexels_material / pixabay_material / pv_engine / sandbox_demo / shortform_category / subagent_demo / subtitle_translate / text_animations / tts_feature / tutorial_category / unsplash_material / voice_ext / web_media |
| **半壳** | **13** | 29.5% | embedding_providers / headless_chrome / kinetic_typography / llm_anthropic / llm_openai_compatible / local_material / logic_animations / mg_animations / rerank_providers / tts_cosyvoice / tts_minimax / tts_qwen / whisper_stt |
| **空壳** | **5** | 11.4% | cloud_render / genui / lottie_animations / platform_export / stt_providers |
| **未核实** | **0** | 0% | — |

> 口径说明：`genui` 计入空壳是为了统计完整；它的整改**不**在本轮，见 PRD §3 与 coding plan B0–B6。

---

## 3. 半壳与空壳深度分析（每个一段：现状 / 为什么算壳 / 整改方案 / 搬不动 / 人日）

### 3.1 `genui`（空壳，引用既有裁定，不重复分析）

- **现状**：`plugins/genui/main.py:73-81` 一条 `PROMPT`；engine `clipwright/schema/genui.py` 549 行
  （白名单 `:29`、清洗器 `:345`、构造器 `:403-536`）+ `services/requirements_service.py` 20 处接线
  （`:1185-1187`、`:1290-1292`、`:2444-2467`、`:2790-2796`、`:3841-3894`）。
- **为什么算壳**：判据 D1–D4 全不成立（有专属文件、专属 import、专属常量、专属分支）。
- **整改**：PRD §3.1–§3.8（新增 `display_spec` 纯数据 kind + 通用清洗器 + recipe 解释器），
  coding plan B0–B6 已冻结，**本文不新增任务、不改其排期**。
- **搬不动**：清洗器（信任边界）、前端渲染器（编译期产物）、`_brief_hash`（业务判定）——PRD §4 已列。
- **人日**：17.0（已含在 B0–B6，不重复计入 B7/B8）。

### 3.2 `tts_cosyvoice` / `tts_qwen` / `tts_minimax`（半壳，三个同构）

- **现状**：三个插件各 39–42 行，`contributions()` 里把 engine 的类包成 factory：
  `plugins/tts_cosyvoice/main.py:25-30`、`tts_qwen/main.py:24-29`、`tts_minimax/main.py:22-27`。
  真正的鉴权、请求、响应解析在 `clipwright/services/voice.py:371-454`（Qwen）、`:455-551`（CosyVoice）、
  `:552-656`（MiniMax）。
- **为什么算壳**：S1+S2 同时成立（engine 有实现类、插件 import 它）。更糟的是 engine 还留了
  **provider 专属知识**：`voice.py:729-734` 的模型映射表、`:771` 的 `if provider_name == "qwen-tts"`
  （只有 qwen 需要 base64 data URI）、`config.py:535` 的 `Literal["qwen-tts","cosyvoice","minimax"]`。
  插件声明了 `config_schema`，但**真正的默认模型仍从 engine settings 读**（`tts_*/main.py` 的
  `_default_model()`），声明面与实现面各持一半。
- **整改（能搬）**：
  1. 把三个 `*Provider` 类逐行搬进各自插件（`plugins/tts_<id>/provider.py`）；
  2. `BaseVoiceProvider` 留在 engine（协议基类 + 上传器注入）；
  3. `_model_for()` 删除，改调 `TtsProviderEntry.default_model()`（该字段已存在，
     `providers/tts/__init__.py:22-32`）；
  4. `:771` 的 base64 分支改由 provider 能力位决定（`TtsProviderEntry` 增 `audio_ref_style`
     或在 `BaseVoiceProvider` 增 `build_audio_ref(path)` 默认实现，qwen 覆盖）；
  5. `config.py:535` 的 Literal 改 `str`，启动时用注册表校验（未知 id 落回 `""` + 可读日志）。
- **搬不动**：`PublicUploadManager`（`:209`，公网 URL 上传属宿主基座，插件不该拿凭据）；
  `VoiceStorage`（`:70`，音色库落盘）；Fernet 凭据解密——理由同 PRD §3.3（信任边界）。
- **验收**：
  ```powershell
  py -3.14 -c "import clipwright.services.voice as v; print([n for n in ('QwenTTSProvider','CosyVoiceProvider','MiniMaxProvider') if hasattr(v,n)])"   # 期望 []
  Get-ChildItem clipwright -Recurse -Include *.py | Select-String -Pattern '"qwen-tts"|"cosyvoice"|"minimax"' | Where-Object { $_.Path -notmatch 'providers\\tts' }
  # 期望：只剩错误文案/兼容别名，0 处 import、0 处 provider 分支
  py -3.14 scripts/verify_provider_pluginization.py          # 期望 13/13
  py -3.14 -m pytest tests/clipwright/test_voice*.py -q      # 期望全绿（打桩点须改到插件模块）
  ```
- **人日**：3.0（三个类约 286 行 + 三处测试打桩改写 + verify 脚本）。

### 3.3 `llm_anthropic` / `llm_openai_compatible`（半壳，两个同构）

- **现状**：插件 31/39 行，`contributions()` 返回 engine 的实例
  （`llm_anthropic/main.py:28`、`llm_openai_compatible/main.py:32-37`）；实现类在
  `providers/llm/anthropic_provider.py`、`providers/llm/openai_provider.py`（68/55 行）。
  而且 engine 在 `providers/llm/__init__.py:16-28` 又用同三个 id 注册了一遍内置实现
  （`register_builtin_once` 只在插件已注册时让位）。
- **为什么算壳**：S3 双注册 + S2 import engine。用户 2026-11-02 的裁定「能力交给插件」只兑现了注册，
  协议代码仍由 engine 拥有；`services/llm.py:727`、`:897`、`:950` 还保留 `if provider == "anthropic"`
  分支，说明**能力位（capabilities）声明没有真正取代分支**。
- **整改（能搬）**：
  1. 两个 provider 类逐行搬进插件（`plugins/llm_*/provider.py`），插件声明
     `config_schema`/`capabilities` 不变（已是唯一真相）；
  2. 删除 `providers/llm/__init__.py` 的内置注册（保留 `providers/base.py` 协议 + 访问器）；
  3. `services/llm.py` 三处 vendor 分支改为协议方法（`llm_normalize_messages`、
     `llm_tool_result_message`、`llm_tool_schemas` 已在 `providers/base.py` 提供最小语义，
     删掉 legacy `elif` 即可）；
  4. `config.py:75` 的 `llm_provider: Literal[...]` 改 `str` + 注册表校验。
- **搬不动**：`isobase` 传输层与 `services/llm.py` 的重试/计费/路由（宿主机制）。
- **验收**：
  ```powershell
  py -3.14 -c "import clipwright.providers.llm as m; print(m.ensure_llm_providers_registered.__doc__)"  # 期望报"已删除"或函数不存在
  Select-String -Path clipwright\services\llm.py -Pattern 'provider == \"anthropic\"|self.provider == \"anthropic\"'   # 期望 0
  py -3.14 scripts/verify_provider_pluginization.py -k provider_owners   # 期望通过，owners 仍为两插件
  py -3.14 -m pytest tests/clipwright/test_llm_provider_protocol.py -q
  ```
- **人日**：2.0（含打桩点迁移：`tests/clipwright/test_llm_provider_protocol.py:38` 现 monkeypatch
  `clipwright.services.llm.OpenAIChat`，搬类后必须改到插件模块）。

### 3.4 `embedding_providers` / `rerank_providers`（半壳）

- **现状**：`embedding_providers/main.py:22-34` 直接 `from clipwright.providers.embedding import ...`
  并注册三个实例；`providers/embedding/__init__.py:17-56` 是 100 行实现（`RemoteEmbedder` 等），
  `:128-141` 同三 id 再注册一次。`rerank_providers` 完全同构（`main.py:18-24` vs
  `providers/rerank/__init__.py:19-51`、`:172-184`）。
- **为什么算壳**：S2+S3。API 侧 `api/providers.py:65-66` 消费 `embedding_provider_names()` /
  `rerank_provider_names()`，而这两个函数内部**先 `ensure_*_registered()` 再读注册表**
  （`providers/embedding/__init__.py:148`、`:157`；`providers/rerank/__init__.py:191`、`:200`）
  —— 也就是插件禁用后列表照样有值，插件在不在都一样。
- **整改（能搬）**：实现类搬进插件；`resolve_*` 去掉 `ensure_*_registered()`（禁用即无 provider，
  与 PRD §3.6 口径 A、AGENTS #3 的 TTS 先例一致）；`api/providers.py` 的探活分支保留（宿主机制）。
- **搬不动**：`build_http_client`（`providers/transport.py`，代理/超时/头的统一构造）、
  `rag/reranker.py::_parse_api_response`（响应格式兼容，属平台解析）。
- **验收**：
  ```powershell
  py -3.14 -c "from clipwright.providers.embedding import embedding_provider_names as n; print(n())"  # 未启用插件时期望 []
  py -3.14 -c "import clipwright.main"                                                              # 0
  py -3.14 -m pytest tests -q -k "provider or embedding or rerank"                                  # 无新增红
  ```
- **人日**：1.5（两类约 250 行 + API 两处 + 列表语义变更的用例）。

### 3.5 `stt_providers`（空壳）

- **现状**：`stt_providers/main.py:18-45` 把 engine `providers/stt/__init__.py:48-55` 的两份闭包
  **逐字抄了一遍**，再从 engine 导入探测函数（`:33-37`）。engine `:57-68` 又注册了同样的
  `(whisper, faster-whisper)`。
- **为什么算壳**：S3 双注册 + 插件零独有逻辑（连 `_probe_*` 都 import engine），是最纯粹的"抄写"。
- **整改（能搬）**：两种都比现状好，二选一——
  - **A（推荐）**：删插件，承认 whisper 后端是 engine 的平台能力（转录依赖本地二进制，
    不是厂商协议）；`providers/stt` 只留顺序解析；
  - **B**：插件真正拥有探测 + 两个 `_call_*`（把 `services/stt.py:214-283` 的
    `_transcribe_whisper/_transcribe_faster_whisper` 也搬进插件），engine 只留 `STTService.transcribe`
    的调度与 `align()`。
- **搬不动**：`STTService.align()`（句级对齐属平台，插件交付不了确定性时间轴）、
  `_ensure_audio`（ffmpeg 抽音轨）。
- **验收**：`py -3.14 -c "import clipwright.providers.stt as s; print(s.resolve_stt_backends(object()))"`
  期望只反映注册表（插件禁用时为空）；`py -3.14 scripts/verify_provider_pluginization.py` 13/13。
- **人日**：1.0（选 A 则 0.3；此处按 B 计，因为 A 会丢"第三方可贡献 STT"的扩展面）。

### 3.6 `whisper_stt`（半壳）

- **现状**：`main.py:54-76` 的 `execute` 只有一句 `STTService().transcribe(...)` + 字段整形；
  转写实现全在 `services/stt.py:59,214-283`。更严重的是 engine 自带一个同功能工具
  `tool/transcribe.py:16-18`（`whisper_transcribe`），插件的是 `whisper_transcribe_local`——
  **同一能力两个工具名**。
- **为什么算壳**：S1+S2；且与内置工具双实现，模型选哪个取决于工具面顺序。
- **整改（能搬）**：删掉 `whisper_transcribe_local` 工具（保留 `transcribe_and_align` 技能，
  它做的是"转录→字幕 clip"的编排，属插件价值）；或把 `transcribe_and_align` 改调内置
  `whisper_transcribe`，插件只贡献技能。
- **搬不动**：无（本项是删除，不是搬移）。
- **验收**：`py -3.14 -c "from clipwright.tool.registry import ToolRegistry; print(ToolRegistry.get('whisper_transcribe_local'))"` 期望 `None`；
  `Get-ChildItem clipwright -Recurse -Include *.py | Select-String "whisper_transcribe_local"` 期望 0（除插件已删）。
- **人日**：0.5。

### 3.7 `headless_chrome`（半壳）

- **现状**：插件 131 行，`contributions()` 返回 `{"path": find_shell(), "resolve": find_shell,
  "install": install_shell}`（`main.py:39-48`），`setup()` 直接调 `headless_browser.install_shell()`
  （`:108-125`）。全部逻辑在 `services/headless_browser.py`（340 行：`find_shell` `:80`、
  `status` `:107`、`apply_env` `:148`、`install_by_download` `:193`、`install_shell` `:240`）。
  engine 还在 `:30` 硬编码 `HEADLESS_SHELL_ID = "chrome_headless_shell"`（S5），
  并在 `api/render.py:1155-1183` 提供两个端点。
- **为什么算壳**：S1+S2+S5 全中。插件名下的"能力"（下载 120MB 二进制、探测版本、写 env）
  一行都不在插件里，插件只剩一段 UI 文案。
- **整改（能搬）**：
  1. `find/install/probe/apply_env` 搬进 `plugins/headless_chrome/shell.py`；
  2. `renderer_backend` 合同扩展为 `{resolve(), install(), apply_env(env)}`（现在只认
     `path`/`callable`/`.path`，见 `headless_browser.py:51-69`），由插件实现；
  3. engine `services/headless_browser.py` 缩成 facade（读贡献 → 调方法，约 40 行），
     `api/render.py` 两个端点改为调 facade（路径不变，前端零改动）；
  4. 删除 `HEADLESS_SHELL_ID` 常量，id 由贡献提供（`find` 改成遍历 `RENDERER_BACKEND`）。
- **搬不动**：`_system_chrome_path()`（`:136`，系统 Chrome 兜底 = 宿主环境探测）；
  `/api/render/browser` 端点本身（HTTP 面属宿主）。
- **验收**：
  ```powershell
  py -3.14 -c "from clipwright.services import headless_browser as h; print(hasattr(h,'install_shell'))"   # 期望 False（或只剩薄 facade）
  Get-ChildItem clipwright -Recurse -Include *.py | Select-String -Pattern 'chrome_headless_shell'          # 期望 0
  py -3.14 -c "from clipwright.main import app"                                                             # 0
  # 运行期：禁用插件后 GET /api/render/browser 的 mode 应为 system-chrome-headless-cpu
  ```
- **人日**：2.0（340 行搬移 + 端点接线 + 一条禁用态验收）。

### 3.8 `platform_export`（空壳）

- **现状**：插件里有 6 个平台预设（`main.py:17-60`）与两个 hook（`:63-89`），hook 读
  `context["platform"]` 与 `context["settings"]`。但调用点 `api/render.py:603` 传的是
  `{"task_id": task_id}`（POST 为 `{"task_id","output_path","success"}`，`:645-648`）——
  **两个键都不存在**，两个 hook 从上线起就是空转。
  同时 engine 自己维护两张表：`api/render.py:1091 _EXPORT_PRESETS`（bilibili/youtube/tiktok）与
  `:973 _PLATFORM_EXPORT_COUNTS`（tiktok/douyin/weibo/bilibili/youtube），与插件的 6 平台
  （含 `bilibili_4k`/`wechat_channels`/`xiaohongshu`）**互不一致**。
- **为什么算壳**：S6 成立（贡献不生效）+ engine 里有第二份实现（双表）。
- **整改（能搬）**：
  1. 预设改走已有的 `export_preset` kind（`contribution.py` 里有这个枚举，当前**零消费方**），
     `api/render.py::list_presets` 与 `_resolve_settings` 合并"内置 + 贡献"，删除 `_EXPORT_PRESETS`
     里的平台项（保留本地 `default`/`draft` 等非平台预设）；
  2. 封面张数改由预设字段 `thumbnail_count` 提供，删 `_PLATFORM_EXPORT_COUNTS`；
  3. POST_RENDER hook 入参补 `preset`/`output_path`（`api/render.py:645`），插件据此回填封面。
- **搬不动**：`_extract_platform_covers()`（ffprobe 抽帧，宿主能力）；
  "禁用插件后导出页是否还有平台预设"是**产品决策**（口径 A 会没有）→ §7 待拍板 Q1。
- **验收**：`GET /api/render/presets` 返回的 key 集合 == 内置 ∪ 插件；禁用插件后只余内置；
  `Select-String -Path clipwright\api\render.py -Pattern '_PLATFORM_EXPORT_COUNTS'` → 0。
- **人日**：1.5（含 Web 侧预设合并；前端 `ExportPage.tsx:27` 的硬编码预设另算，见 §7 Q1）。

### 3.9 `cloud_render`（空壳）

- **现状**：`main.py:29-41` 的 hook 只做 `context["cloud_render"] = {...}`。全仓 grep
  `cloud_render` 只有插件自己与 `docs/`，**没有任何消费方**。真正的远程渲染在
  `services/remote_render.py:59`（23KB，上传/轮询/下载/回退全套）+ `providers/render/__init__.py:173`
  注册的 `RemoteRenderBackend`，由 `api/render.py:225-227` 按 `settings.remote_render_url` 选择。
- **为什么算壳**：S6 + 双实现。用户装了 `cloud_render` 并填了 `server_url`，**行为零变化**。
- **整改（两条路，二选一）**：
  - **A（立即，推荐）**：删除 `plugins/cloud_render/`（或降级为 README 说明"请用设置→远程渲染"），
    并在 `docs/pluginization-plan` §12.3 标注"远程渲染暂为宿主能力"；
  - **B（另立目标）**：把 `RemoteRenderService` 搬成 `renderer_backend` 插件（`object` 提供
    `render()` + `probe()` + `capabilities()`），engine 删 `:225-227` 的 url 特判，
    `settings.remote_render_*` 改由插件 `config_schema` 声明。
- **搬不动（B 档）**：`api/render.py` 的渲染队列/SSE 进度（宿主）、产物落盘路径校验（`workspace`）。
- **验收（A）**：`Get-ChildItem plugins -Filter cloud_render` 为空；
  `Select-String -Path clipwright -Recurse -Include *.py -Pattern 'cloud_render'` → 0。
- **人日**：A = 0.2；B = 2.5（23KB + 配置迁移 + 回退语义）。

### 3.10 `logic_animations`（半壳）

- **现状**：`diagrams/all.py:1-9` 的 docstring 自己写明「原有 12 个渲染函数是从核心
  `diagram_svg.py` 复制的陈旧副本……已全部删除，仅保留核心没有的 mindmap」；
  `main.py:27-40` 仍列 12 种描述，`manifest` 描述也写「12 种」。engine
  `diagram_svg.py:1170-1878` 有 11 个同名实现，`:301-305` 把它们列进内置 presets。
- **为什么算壳**：插件名下 12 种图解，**11 种由 engine 渲染**，插件只贡献 1 种 + 一段提示词。
  这是"声明面 > 实现面"最典型的一例（manifest 与 prompt 都在教模型使用 engine 的能力）。
- **整改（同一个任务内的两步，总额 2.0）**：
  - **第一步（诚实化，0.2）**：manifest 描述、`_DESCRIPTIONS`、prompt 都收窄到 `mindmap`，
    并在 `docs/pluginization-plan` §12.3 记录"其余 11 种是 engine 内置图解"；
  - **第二步（搬渲染器，1.8）**：把 11 个渲染器搬进插件 `diagrams/`（它们只依赖
    `DiagramStyle` + `utils/svg_helpers.py`，无宿主私有依赖），engine 只保留
    `get_supported_presets()` 的注册表合并。
- **搬不动**：`_inject_motion()`（`diagram_svg.py:238`，动效注入是所有图解共享的宿主机制）、
  `DiagramRenderer.render` 的入口分派（宿主）。
- **验收（B8）**：`py -3.14 -c "from clipwright.animation.diagram_svg import DiagramRenderer as R; print([p['id'] for p in R.get_supported_presets()])"`
  在禁用插件后不含 11 个 id；`Select-String -Path clipwright\animation\diagram_svg.py -Pattern 'def _radar|def _gantt|def _sankey'` → 0。
- **人日**：0.2 + 2.0。

### 3.11 `kinetic_typography`（半壳）

- **现状**：6 个动画以三份数据同时存在——`KINETIC_ANIMATIONS` 的 CSS 字符串（`main.py:24-73`）、
  `AnimationDef`（`:116-123`，**无 keyframes**）、hook 写入的 `context["catalog"]`（`:76-82`）。
  engine 读的是 `catalog.py:116` 的 `ctx.get("extensions")` —— **键名不匹配**，
  CSS 也没有任何消费方（`word_emphasis`/`bounce_text` 在 engine 0 命中）。
- **为什么算壳**：插件交付的三种形态没有一种真正生效；引擎侧也没有对应实现（所以不算"能力在
  engine"，而是"能力谁都没有"）。归为半壳是因为它**声称有 6 个动画**（manifest/contributions 都列了）。
- **整改（能搬）**：删掉 CSS 与死 hook，把 6 个动画改写成**带 keyframes 的 `AnimationDef`**
  （engine `animation/renderer.py:70-75` 的通用渲染器支持 `translate_y`/`scale`/`opacity`/
  `rotate`）；无法用关键帧表达的效果（渐变填充、3D 透视）改用 `mg_def`（结构化 MG JSON，
  AGENTS #22 已允许）或直接下线。
- **搬不动**：CSS 动画的执行环境（hyperframes 的 CSS 由宿主注入，插件不能执行 CSS）；
  `gradient_fill` 这类纯 CSS 表达式在 FFmpeg/MG 管线里没有等价物 → **建议下线，不硬凑**。
- **验收**：`Select-String -Path plugins\kinetic_typography\main.py -Pattern 'catalog'` → 0；
  `py -3.14 -c "from clipwright.animation.registry import AnimationRegistry as A; print([d.animation_id for d in A.list_by_plugin('kinetic_typography')])"`
  返回的每个 id 的 `keyframes` 非空。
- **人日**：1.0（6 → 4 个可用 + 2 个下线说明）。

### 3.12 `lottie_animations`（空壳）

- **现状**：两个 hook 全部错形状——`_extend_catalog` 写 `ctx["catalog"]`（`:52-61`），engine 读
  `extensions`；`_extend_renderer` 把 `renderers` 写成 `dict`（`:64-82`），engine
  `diagram_svg.py:212` 按 **list of `{id, renderer}`** 迭代。插件也从不注册 `animation` 贡献。
  于是"Lottie 动画"实际能力 = 一条提示词 + 一段不会被执行的 HTML 模板（里面还引了
  `cdnjs.cloudflare.com` 外链脚本，`main.py:72`）。
- **为什么算壳**：S6 两项全中；归属上连 engine 实现都没有（`lottie-web` 从未接入）。
- **整改（建议下线，不修）**：
  - **A（推荐）**：把插件降级为「本地 Lottie JSON 目录 + 说明」，删除两个 hook 与外链模板，
    只保留 prompt（不宣称可渲染）；并在 `docs/pv-engine-known-gaps.md` 记录"Lottie 渲染未接入"；
  - **B**：真接入——`ANIMATION` 贡献 + `mg_def`/`lottie_json` 元数据 + 宿主渲染器（前端要新原语，
    触碰 AGENTS #26 的"首版不开放 svg/image"红线）→ **不建议**。
- **搬不动**：前端渲染原语（编译期闭包）；外链 CDN 脚本（安全红线，`AGENTS #26`）。
- **验收（A）**：`Select-String -Path plugins\lottie_animations\main.py -Pattern 'cdnjs|renderers'` → 0；
  `GET /api/plugin/contributions` 里该插件只剩 `prompt`。
- **人日**：0.3。

### 3.13 `mg_animations`（半壳）

- **现状**：插件把 `animations/*.json` 转成 `AnimationDef`（`main.py:139-144`）并贡献两条 hook；
  但 engine `animation/mg_renderer.py:598-624` **直接把插件目录写进搜索路径**
  （`plugins/mg_animations/animations`），`load_animation`/`list_animations` 都走这条硬编码。
  另有一条 `plugins/llm_mg/templates`（已不存在的目录）也留在路径里。
- **为什么算壳**：S5 成立——engine 知道插件的物理目录与 id；插件禁用后 engine 仍会去读该目录
  （读得到就"能力还在"），与"禁用即无能力"矛盾。
- **整改（能搬）**：`mg_renderer.load_animation` 改为从 `AnimationRegistry` 取
  `AnimationDef.properties_meta["mg_json"]`（插件贡献时已可携带 JSON），删除三条 `plugins/...`
  路径；`list_animations` 同理。插件侧把 JSON 内容塞进 `properties_meta`（几 KB，注册期一次）。
- **搬不动**：`MGRenderer` 的 HTML/逐帧渲染（宿主能力，插件交付不了）。
- **验收**：`Select-String -Path clipwright\animation\mg_renderer.py -Pattern 'plugins'` → 0；
  `py -3.14 -c "..."` 禁用插件后 `MGRenderer.list_animations()` 不含 4 个 mg_* id。
- **人日**：1.0。

### 3.14 `local_material`（半壳）

- **现状**：插件 67 行，`LocalFolderSource` 的 123 行实现在 `material/local_source.py:55-177`
  （扫描/索引/搜索/close），插件只做配置读取 + 实例化 + 注册（`main.py:45-64`）。
- **为什么算壳**：S1+S2。engine 里还有 `source_from_env()`（`:180-190`）这个**没人调用**的残留入口，
  以及 `material/__init__.py:13` 的公开导出 —— 说明当初就是按"内置源"写的。
- **整改（能搬）**：把 `LocalFolderSource` 搬进插件（只依赖 `MaterialSource` 基类 + `pathlib`），
  engine 删除该类与 `source_from_env`，`material/__init__.py` 去掉导出。
- **搬不动**：`MaterialSource` 协议（`material/base.py`，插件契约）。
- **验收**：`py -3.14 -c "import clipwright.material as m; print(hasattr(m,'LocalFolderSource'))"` → False；
  `py -3.14 -m pytest tests -q -k material` 无新增红。
- **人日**：0.5。

### 3.15 附带发现：`voice_ext` 归属对、接线错（不是壳，但要不修就白做）

- **现状**：`voice_ext` 在插件里完整实现了 ElevenLabs/Azure 两套 TTS 协议（`main.py:53-83`），
  但贡献的是 **`tool`**（`extended_tts`），不是 `tts_provider`。后果：`VoiceService` 的音色下拉
  与 `/api/voice/*` **看不到**这两个 provider，插件只能被 agent 当工具调用，走的是"另起一条路"。
- **整改**：把 `ExtendedTTSTool` 的两套实现改成 `tts_provider` 贡献（`TtsProviderEntry` +
  `BaseVoiceProvider` 子类），`config_schema` 声明 key/region；工具可保留为兼容别名（或删除）。
- **搬不动**：`PublicUploadManager`（若 ElevenLabs 需要公网音频，仍走宿主上传器）。
- **验收**：`GET /api/voice/providers`（或等价端点）在启用插件后包含 `elevenlabs`/`azure`；
  `py -3.14 scripts/verify_provider_pluginization.py -k tts` 通过。
- **人日**：1.0（含一条 provider 列表用例）。

---

## 4. 横向模式（共性，不是个案）

### 4.1 engine 里仍有 15 处 vendor 字面量 / 9 处 vendor 分支

命令见 §1.3-④。结果（排除 `providers/` ——那是实现方，理应知道自己的 id）：

| 位置 | 形态 | 问题 |
|---|---|---|
| `services/llm.py:727`、`:897`、`:950` | `if provider == "anthropic"` | 协议能力位已声明（插件 manifest `capabilities`），engine 仍按名字分支 → 违反 #24 |
| `services/voice.py:709`、`:722`、`:729-734`、`:771` | 注释/兜底名单/模型映射表/`== "qwen-tts"` 分支 | provider 专属知识留在 engine |
| `config.py:75`、`:511`、`:535` | `Literal["openai","anthropic","ollama"]` 等 | 枚举把 provider 名单钉死在 engine，插件新增 id 无法进入默认值 |
| `api/providers.py:76` | `{"qwen_tts": "qwen-tts"}` 别名表 | 别名兼容属能力方知识 |
| `rag/embedder.py:176`、`:180` | `provider in ("openai","ollama")` | embedding 分支 |
| `services/vision.py:163`、`animation/mg/visual_eval.py:165` | `== "anthropic"` | 视觉/MG 评估分支 |
| `services/agent_loop.py:129` | `in ("ollama",)` | ollama 特判 |

**判定**：这 15 处不构成"插件是壳"（能力主体确实在插件或 providers 包），但**构成 #24 的持续违反**，
且是 3.2–3.4 无法把实现搬干净的根因：只要 engine 还按 provider 名分支，搬类就只是换个位置。

### 4.2 钩子契约缺失：`pre_render` 只传 `{"task_id"}`，4 个插件的钩子空转

`api/render.py:603` 的 PRE_RENDER 入参是 `{"task_id"}`，POST 是
`{"task_id","output_path","success"}`（`:645-648`）。而插件侧期待：

| 插件 | 期待的键 | 实际结果 |
|---|---|---|
| `cloud_render` | 写 `context["cloud_render"]`（无消费方） | 完全不生效 |
| `platform_export` | `context["platform"]`、`context["settings"]` | 两个键都不存在 → 空转 |
| `shortform_category` | `context["settings"]` | 空转（竖屏尺寸从未被强制） |
| `sandbox_demo` | 写 `sandbox_marker` | 写进去了但同样无人读（示例可接受） |

**共性**：hook 是"能挂上去但不知道能挂什么"的扩展点——**没有契约**（没有入参 schema、
没有"返回值是否被使用"的约定）。这是比单个插件更值得修的结构问题：建议在
`docs/pluginization-plan` §12.3 给 hook 增加**逐 point 的入参/出参契约表**，
并让 `execute_async` 把返回值 merge 回调用方（现在看行为是丢弃）。

### 4.3 双实现 / 双注册：6 个插件与 engine 的 `ensure_*_registered()`

`embedding_providers` / `rerank_providers` / `stt_providers` / `llm_*` / `tts_*` / `whisper_stt`
都存在"插件注册一份、engine 再注册（或再实现）一份"。共同后果：

1. **禁用插件 ≠ 失去能力**（与 PRD §3.6 口径 A 相反）——`resolve_*` 内部调 `ensure_*` 就补回来了；
2. **归属显示失真**（`/api/providers` 的 `source.plugin_id` 会显示插件，但跑的是内置实现）；
3. **测试打桩点分裂**（同一能力有两个 import 路径，monkeypatch 一边另一边照跑）。

### 4.4 "声明面 > 实现面"：3 个插件的 manifest/prompt 替 engine 宣称能力

| 插件 | 声明 | 实际由插件提供 | 差额 |
|---|---|---|---|
| `logic_animations` | 12 种图解 | 1 种（mindmap） | 11 种是 engine 内置 |
| `kinetic_typography` | 6 个动画 | 0 个生效 | 6 个（CSS 从不执行） |
| `lottie_animations` | "支持 Lottie 渲染" | 0 个可达 | 全部（钩子形状错） |

危害不是"虚标"这么轻——它们还把 engine 的能力写进**给模型的提示词**（`structure_agent`），
模型会按"插件提供"去用，实际走的是 engine 内置分支，**插件禁用后行为不变**，用户无法通过启停
判断能力归属。这与 #23（提示词单一来源）/#19（能力交给插件）同时冲突。

### 4.5 engine 反向知道插件目录 / 插件 id（S5，3 处）

| 位置 | 内容 |
|---|---|
| `services/headless_browser.py:30` | `HEADLESS_SHELL_ID = "chrome_headless_shell"`（插件 id） |
| `services/headless_browser.py:11` | docstring 直接写"插件通过 renderer_backend 登记 `id = ...`" |
| `animation/mg_renderer.py:598`、`:603`、`:619-624` | `plugins/mg_animations/animations`、`plugins/llm_mg/templates` 物理路径 |

这是"引擎知道插件长什么样"的耦合，等价于 PRD D1/D2 想消灭的"专属链路"，只是换了个 kind。

---

## 5. 与既有不变量的冲突（点名）

| 不变量 | 冲突点 | 证据 |
|---|---|---|
| **#19 判断交给模型、能力交给插件** | `web_search`/`web_fetch` **是 engine 内置工具**（`tool/web_search_tool.py:46,132`），不是插件；抓取/解析/落盘的主体在 `services/web_search.py:280`、`services/web_assets.py:272`。`web_media` 只接管了"网页抽图"与"事实判断"两段。用户 2026-11-02 的原话是"抓网页/解析/抽图/事实判断都属于插件"，**前两项尚未插件化**（不在本次 44 个插件内，但属同一条裁定的欠账） | 见 §6 的"另立目标" |
| **#24 Provider 表单由 schema 驱动、禁止厂商分支** | §4.1 的 9 处分支 + 3 处 `Literal` 枚举；`voice_ext` 用 `tool` 绕过 `tts_provider` 面 | `services/llm.py:727`、`services/voice.py:771`、`config.py:535` |
| **#23 提示词单一来源** | `logic_animations`/`kinetic_typography`/`lottie_animations` 的插件 prompt 在替 engine 内置能力做宣传（§4.4） | `plugins/logic_animations/main.py:56-59` 等 |
| **#11 能力解析** | 本审计的整改**不需要**动能力解析层（`tool/capabilities.py`）：provider 类走的是 `ContributionRegistry.resolve`，工具类已是插件工具。**明确不改** | — |
| **#12 权限档位** | 无冲突；搬家不得改变 `permission_tier`（`text_animations`/`ai_*` 等插件工具已自声明） | — |
| **#26 GenUI** | genui 已由 PRD/B0–B6 覆盖；本文的"声明面 > 实现面"（§4.4）是同一类病，建议把 #26 的措辞从"GenUI"泛化为"**任何插件的 prompt 不得替 engine 宣称能力**" | 在 B8-T8.7 一并改 AGENTS.md（已写进该任务） |
| **PRD §3.3 零信任** | 整改方案**不**让插件提供清洗器/校验器；provider 类搬移属于"能力实现"，不是"trust 边界"——TTS/LLM 的凭据仍由宿主注入，插件拿不到解密后的全量配置（`TtsCredentials` 由 `VoiceService._tts_credentials()` 构造） | `services/voice.py:679-694` |

---

## 6. 建议整改顺序（用户可见影响 × 整改成本）

排序依据：**用户是否能感知**（装了插件行为变不变）> **是否阻塞其他整改** > **成本**。

| 序 | 项 | 用户可见影响 | 成本 | 结论 |
|---|---|---|---|---|
| 1 | **§4.1 去 vendor 分支 + §3.2/3.3/3.4 协议类归位** | 高：插件启停第一次真的改变行为；归属显示变真 | 10.0 人日（B7 / 6 任务） | **先修**（用户 2026-11-02 的裁定至今只兑现了一半） |
| 2 | **§3.8 platform_export / §3.9 cloud_render** | 高：装了插件完全没反应（空转），是最容易被用户发现的"假插件" | 1.5 + 0.2（B8-T8.5 / T8.6） | **先修**（便宜且可见） |
| 3 | **§3.7 headless_chrome** | 中：功能能用，但"插件"名不副实；禁用后行为不变 | 2.0（B8-T8.1） | 第二批 |
| 4 | **§3.10 logic_animations / §3.11 kinetic / §3.12 lottie（声明诚实化）** | 中：模型被教了不存在的能力 | 0.2 / 1.0 / 0.3（B8-T8.2 / T8.3 / T8.4） | **诚实化立即做**；渲染器搬家在同任务第二步 |
| 5 | **§3.13 mg_animations / §3.14 local_material / §3.6 whisper_stt** | 低：功能正常，只是归属错/双实现 | 0.5+0.5+0.5（B8-T8.7 / T7.4） | 第三批 |
| 6 | **§3.15 voice_ext 接 `tts_provider`** | 低：能力在插件但用户看不到 | 1.0（B8-T8.7） | 第四批 |
| 7 | **§4.2 hook 契约（逐 point 入参/出参表）** | 中：不修则后续所有 hook 插件都可能空转 | 0.5（文档 + `execute_async` 合并返回值）（B8-T8.5） | 与第 2 项同任务（是它的根因） |
| 8 | **§4.4 提示词"不得替 engine 宣称能力"写进 AGENTS** | 低（纪律） | 0.2（已含在 B8-T8.7 的 2.0 里） | 收尾 |
| — | **`web_search`/`web_fetch` 插件化（#19 欠账）** | 高，但**不是壳**（它们从未声称是插件），且要动 agent 的联网路径 | ≥4.0 | **另立目标**，不进 B7/B8 |
| — | **cloud_render B 档（remote render 搬成插件）** | 中 | 2.5 | 另立目标（先做 A 档删除） |

**与 GenUI 批次（B0–B6）的关系**：B7/B8 **不修改** `schema/genui.py`、`requirements_service.py`、
`display_spec` 相关文件，因此与 B1–B6 **无文件级冲突**，可在 B1 之后任意时点并行开工；
若单人串行，建议排在 B6 之后（B7 → B8），理由见 coding plan §7 的关键路径。

---

## 7. 未决问题（≤3，每条给默认建议）

| # | 问题 | 默认建议 |
|---|---|---|
| Q1 | `platform_export` 的平台预设进插件后，**插件禁用时导出页是否还有平台预设**？ | 接受"禁用即无平台预设"（与 PRD §3.6 口径 A 一致），engine 只留 `default/draft` 等非平台项；若产品不接受，则保留 engine 的 3 个基础预设、插件只增补 |
| Q2 | `logic_animations` 的 11 个渲染器**搬到插件**（2.0 人日）还是**承认归 engine**（改声明，0.2）？ | 同一任务（B8-T8.2）先做 0.2 的诚实化，再搬 1.8；若 B8 预算不够，就只留诚实化并永久承认 engine 所有 |
| Q3 | `cloud_render` 选 **A 删除**（0.2）还是 **B 把 remote render 搬成插件**（2.5）？ | 先 A：删掉自 2026-09 起从未生效的插件，远程渲染能力保留在 engine 并在文档标注"暂为宿主能力"；B 另立目标 |


## 7.5 整改执行状态（2026-11-08）

> 对应 coding plan 的 B7 / B8。状态只记"能力归属是否已正确"，不评价功能好坏；
> 每条都有自检脚本里的门禁（见 `scripts/verify_provider_pluginization.py`）。

| 审计项 | 判定 | 整改状态 | 门禁 |
|---|---|---|---|
| §3.1 `llm_anthropic` / `llm_openai_compatible` | 半壳 → 真 | ✅ 实现搬进插件（B7-T7.2） | `provider_impls_in_plugins` / `no_vendor_branches` |
| §3.2 `tts_{qwen,cosyvoice,minimax}` | 半壳 → 真 | ✅ 实现搬进插件（B7-T7.1），`build_audio_ref` 取代 provider 名分支 | 同上 |
| §3.3 `embedding_providers` / `rerank_providers` | 半壳 → 真 | ✅ 实现搬进插件（B7-T7.3）；embedder 走严格 A6 | 同上 |
| §3.4 `stt_providers` | 空壳 → 真 | ✅ 探测/转写实现搬进插件（B7-T7.4）；`whisper_stt` 收窄为技能 | `provider_impls_in_plugins` |
| §3.9 `cloud_render` | 空壳 | ⏳ 待拍板（删除 vs 搬成 renderer_backend） | — |
| §3.10 `logic_animations` | 半壳 | 🟡 第一步完成（声明面收窄）；第二步（搬 11 个渲染器）待拍板 | `logic_animations_honest` |
| §3.11 `kinetic_typography` | 半壳 → 真 | ✅ 补 keyframes、删死钩子与 CSS、下线 3D/渐变（B8-T8.3） | `dead_hooks_removed` |
| §3.12 `lottie_animations` | 空壳 | ✅ 降级为素材目录（B8-T8.4） | `dead_hooks_removed` |
| §3.13 `mg_animations` | 半壳 → 真 | ✅ MG JSON 随 `AnimationDef` 走注册表（B8-T8.7） | `mg_impl_in_plugin` |
| §3.14 `local_material` | 半壳 → 真 | ✅ `LocalFolderSource` 搬进插件（B8-T8.7） | `material_impl_in_plugin` |
| §3.15 `voice_ext` | 真插件 / 接线错 | ✅ 改贡献 `tts_provider`（B8-T8.7） | `voice_ext_as_provider` |
| §3.5 `platform_export` | 空壳 → 真 | ✅ 预设改 `export_preset` 纯数据贡献 + hook 真接线（B8-T8.5） | `export_preset_wiring` |
| §3.7 `genui` | 空壳 → 真 | ✅ 能力迁移 B0–B6（契约/配方/提示词全在插件） | `display_spec_*` 四条 |
| §3.x 其余「半壳」名单（`headless_chrome` 等） | — | ✅ `headless_chrome` 实现搬进插件（B8-T8.1） | `headless_impl_in_plugin` |

**结论**：44 个插件中，审计判定的 13 个半壳 + 5 个空壳，已转真 15 个；
剩余 2 个（`cloud_render` / `logic_animations` 第二步）等产品语义拍板。
