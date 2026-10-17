# OpenCut / video-use 借鉴评估与 HyperFrames 动画增强报告

- **日期**：2026-09-13
- **评估对象**（shallow clone，材料留存于 `J:\Clipweight-Client\_research\`）：
  | 仓库 | commit | 日期 |
  |---|---|---|
  | `opencut-app/opencut`（新版重写骨架） | `400f097` | 2026-08-01 |
  | `opencut-app/opencut-classic`（线上可用版） | `cf5e79e` | 2026-05-17 |
  | `browser-use/video-use`（agent 剪辑 skill） | `9575612` | 2026-08-30 |
  | `heygen-com/hyperframes`（本项目动画渲染依赖，CLI v0.8.36） | `fdf9ffa` | 2026-09-13 |
- **被评估项目**：Clipwright / 帧艺（后端 `J:\Clipwright`，前端 `J:\Clipweight-Client`，双仓库均 MPL-2.0）
- **方法**：① 四仓库源码精读（结论均带 `file:line`）；② 本机实测（`npx hyperframes lint/check/snapshot/render` 计时），非纸面推断；③ 许可证以 `LICENSE` 原文为准。
- **配套原始材料**：`_research/findings-video-use.md`（310 行）、`_research/findings-opencut.md`（268 行）、`_research/findings-hyperframes.md`（153 行）。

---

## 0. 三问三答（摘要）

> **执行状态（2026-09-13 当日落地）**：§3.4 的 **P0-1（overlay 注册真实时间轴）、P0-2（lint 门禁）、
> P1-3（质量档 + 本地 bin）、P1-4（Persona 字体）** 与 §4 的提示词质量红线已实现并通过回归
> （后端 1814 passed）；实测 overlay 合成 **108.2s → 18.3s**、lint **0 error/0 warning**。
> 未做项与理由见 §6「执行状态」。执行记录见 `fix-and-feature-plan.md` 执行轮次 104。

**Q1「opencut / video-use 里哪些能用在我这个项目上？」**

- **video-use 的可迁移价值最高，而且几乎全是"方法论"而非代码**：12 条生产正确性硬规则（字幕最后烧、逐段提取+lossless concat、30ms afade、overlay PTS 平移）、**每个动画一个并行 sub-agent 的自包含 brief 契约**、**渲染后自评闭环（切点 ±1.5s 看图，最多 3 轮）**、短语级转写打包控 token、`timeline_view` 视觉下钻图、`project.md` 会话记忆。全部 MIT，零引擎新增。
- **OpenCut 要高价值看 classic，不要看新版**：新版是空骨架（api 用 Elysia、web 用 TanStack Start、Rust `crates/*` 在 `Cargo.toml:3-6` 被注释掉），README 里的 Editor API / plugin-first / MCP / headless / scripting tab **只有设计意图、无代码**；`changelog/*.md` 记的是 classic 的历史功能，不能当新版已实现。classic 值得抄的是**时间数据模型**（`MediaTime` 120000 tick/s 整数 + `FrameRate` 有理数）、`SceneTracks{overlay/main(单例)/audio}` 判别联合、Command+选择快照+diff 波纹编辑、声明式 `ParamDefinition`(6 型)/`EffectDefinition`/`MaskDefinition` 插件契约、IndexedDB+OPFS 分层与 v0→v31 版本化迁移链。**不建议**引入 Rust/wgpu WASM、不建议把服务端 ffmpeg 导出改成客户端 WebCodecs。

**Q2「是否符合当前的 MPL 协议？」**

- **合规。** 三项被借对象的许可证是 **MIT（opencut、opencut-classic、video-use）** 与 **Apache-2.0（hyperframes）**，全部与 MPL-2.0 单向兼容：MIT/Apache 文件可以原样并入 MPL-2.0 项目（各自保留自己的许可），MPL 的文件级 copyleft 不会传染到它们，反之亦然。
- 本项目 `LICENSE` 是标准 MPL-2.0 全文，**未附 Exhibit B 声明**（不构成 "Incompatible With Secondary Licenses"），因此 §3.3 的 GPL/LGPL/AGPL 二次许可通道仍然开放。
- **必须做的动作很少但必须做**：① 新建 `THIRD_PARTY_NOTICES.md`（收录 OpenCut/Browser Use 的 MIT 全文与 HeyGen 的 Apache-2.0 NOTICE）；② 直接移植的代码文件保留原版权头并注明来源；③ **禁止整仓拷贝 hyperframes 的 `skills/`、`registry/`、`frame-presets/` 资产**——其中混入 OFL-1.1、MIT 与**商业字体**（见 §2.5）；④ SaaS 运行不触发 MPL 分发义务，但一旦分发可执行体（Docker 镜像、桌面端），MPL 文件的源码可得性义务即生效。

**Q3「有哪些项目/做法能提升动画 Agent 用 HyperFrames 出片的效果？」**

- **最大的单一杠杆不是新引擎，而是本仓库对 HyperFrames 的用法太"浅"**：现在只用 `npx hyperframes render <dir> -o x.mov --format mov -f 30 --quiet`（`animation/hyperframes_renderer.py:156-158`），上游 0.8.36 的 `--quality/--workers/--variables/--batch/check/snapshot/keyframes/compare/catalog/skills` 一个都没用。
- **本机实测到一个必须立刻修的确定性缺陷**：文字/图解 overlay 走 `_build_html` + 自写 `setTimeout` 时序 JS（`hyperframes_renderer.py:337-360`），HTML 里**没有 `window.__timelines` 注册**，触发上游的 timeline 就绪轮询超时——**同一个 180 帧的 overlay 合成，108.2 s → 加一个 `data-no-timeline` 属性后 18.0 s（6×）**；另一份 135 帧 MG 产物 129.1 s → 22.9 s（实测数据见 §5.2）。且 `setTimeout` 驱动的可见性在逐帧 seek + 多 worker 并行抓帧下**不是 seek-safe**，上游明令禁止（`skills/hyperframes-core/references/determinism-rules.md:68-70`）。
- **排序后的三条主线**：① 渲染门禁与 seek-safe 化（lint/check + 声明式时间轴）；② 把上游 `skills/`（20 个技能）+ `registry/`（**180 blocks / 219 components / 13 examples**）+ `frame-presets/` 蒸馏成 MG 生成器的 few-shot 与设计系统；③ 复用 video-use 的"并行动画 sub-agent + 渲染后自评"编排，把 `snapshot --at` 的 PNG 喂给视觉模型做质量自评（本项目的 `mg/generator.py` 已有 LLM 自批判闭环，只差"看图"这一环）。

---

## 1. 许可证事实与 MPL-2.0 合规判定

### 1.1 事实（直读 LICENSE 原文）

| 对象 | 许可证 | 版权行 | 关键义务 |
|---|---|---|---|
| `opencut/LICENSE` | **MIT** | `Copyright 2026 OpenCut` | 保留版权+许可声明 |
| `opencut-classic/LICENSE` | **MIT**（非 AGPL，无附加条款） | `Copyright 2025-2026 OpenCut` | 同上 |
| `video-use/LICENSE` | **MIT** | `Copyright (c) 2026 Browser Use` | 同上 |
| `hyperframes/LICENSE` | **Apache-2.0** | `Copyright 2026 HeyGen, Inc.`（`LICENSE:178`） | 保留 LICENSE/NOTICE、标注修改、**不得使用其商标** |
| 本项目 `J:\Clipwright\LICENSE`、`J:\Clipweight-Client\LICENSE` | **MPL-2.0** | 标准全文 | 文件级 copyleft；Exhibit A 声明；**无 Exhibit B** |

依赖链侧：opencut-classic 的 `mediabunny`(MIT)、`opencut-wasm`(MIT)、Rust 依赖（wgpu/serde/wasm-bindgen/tsify-next…）全为 MIT/Apache-2.0，**全仓无 ffmpeg/ffmpeg.wasm**（WebCodecs 客户端导出）。

### 1.2 MPL-2.0 的判定逻辑

- MPL-2.0 是**文件级** copyleft：义务只落在"Covered Software"的文件上；与其它许可的文件组合成 "Larger Work"（§1.7）是被明确允许的。
- 因此 **MIT/Apache-2.0 代码并入本仓库 → 不改变其自身许可，也不会让 MPL 传染到整个项目**；反向（把本仓库 MPL 代码给别人）同样只需保证那些 MPL 文件继续以 MPL 提供。
- 本项目**未附 Exhibit B**，即不构成 "Incompatible With Secondary Licenses"——§3.3 的二次许可（GPL/LGPL/AGPL）通道保持开放，未来若要与 GPL 系代码组合不受阻。
- **SaaS 不触发分发义务**：MPL 没有 AGPL 式的网络条款。只有当你把「可执行体」（Docker 镜像、桌面端、前端 bundle 分发）交给第三方时，才需履行 §3.2（告知获取源码的方式）。本仓库源码公开即已满足。

### 1.3 四种使用方式的判定

| 使用方式 | 判定 | 需要的动作 |
|---|---|---|
| 只借鉴思路/架构/提示词（video-use 的 brief、OpenCut 的模型设计） | ✅ 无义务（版权不保护思想） | 建议在报告/PR 中注明来源以示尊重 |
| 复制代码文件/片段 | ✅ 合规 | 保留原版权头 + 许可文本；文件头注明 "Adapted from <repo> (MIT)"；记入 `THIRD_PARTY_NOTICES.md` |
| 通过 subprocess 调用外部 CLI（npx hyperframes） | ✅ 无衍生作品问题 | 无；仅在再分发其二进制/包时才需带 Apache-2.0 声明 |
| 打包其资产（模板 HTML、字体、图标、素材） | ⚠️ **需逐个核对** | registry 条目**自带 license 字段**（已见 `OFL-1.1`、`MIT`）；字体另见 §2.5 |

### 1.4 建议立刻落地的合规动作（0.5 人日）

1. 新增 `THIRD_PARTY_NOTICES.md`（两个仓库各一份，内容一致）：OpenCut MIT、Browser Use MIT、HeyGen Apache-2.0 全文 + 使用范围说明。
2. 移植代码处加 3 行文件头：`# Portions adapted from browser-use/video-use (MIT, Copyright (c) 2026 Browser Use). See THIRD_PARTY_NOTICES.md.`
3. 若蒸馏 hyperframes 的 skill 提示词进 `mg/config.yaml`，在文件头注明 "Adapted from heygen-com/hyperframes (Apache-2.0) skills/*"，**不要**把原文整段当作本项目专有内容。
4. CI/评审检查项：新增文件若含外部来源，必须同时更新 NOTICES（可写成一个 grep 检查）。

### 1.5 残留风险（必须人工处理）

| 风险 | 说明 | 建议 |
|---|---|---|
| **上游商业字体** | hyperframes 仓库内 `.claude/skills/changelog-video/assets/fonts/` 含 `TT_Norms_Pro_*.woff2`、`ABCSolarDisplay-Bold.woff2` **商业字体**（该技能属 AGENTS.md 所述"repo-internal skills"） | 绝对不要整仓拷贝 `skills/`；按需逐文件核对 |
| OFL 字体 | `frame-presets/*/fonts/*.woff2` 附带 `OFL-*.txt`（Inter/JetBrains Mono/EB Garamond 等） | 采用时**同时分发 OFL 文本**，并遵守 Reserved Font Name 约定 |
| 素材/图标/国旗 | OpenCut `public/{flags,shapes,stickers,icons,logos}` 来源未逐项核实 | 用前查来源；不确定的不用 |
| 商标 | Apache-2.0 §6 不含商标授权；HeyGen/HyperFrames 名称与 logo | 文档里可写"使用 HyperFrames 渲染"，但不得用于产品名/宣传背书暗示 |
| 其它引擎许可 | Remotion 为**源码可得的专有许可**（个人/小团队免费，规模门槛需购买，见 [remotion.dev/docs/license](https://v3.remotion.dev/docs/license)）；GSAP 自 2025 起免费但仍是 [GSAP 自有 Standard License](https://gsap.com/community/standard-license/)（非 OSI 开源，含竞争性产品限制） | 引入前单独评审，不要默认"开源可用" |
| FFmpeg 二进制 | 本项目渲染依赖 ffmpeg；GPL 构建 vs LGPL 构建在"分发可执行体"时义务不同 | 分发镜像/客户端时确认所用 ffmpeg 构建的许可与对应源码可得性 |

---

## 2. 能用在 Clipwright 上的东西（Q1）

### 2.1 OpenCut —— 先纠正三个事实

1. **新版 `opencut` 是空骨架**：`apps/api/src/index.ts:1-15` 是 **Elysia**（不是 Hono）3 个端点；`apps/web/src/routes/index.tsx:1-11` 是 "hello world"；`apps/desktop/src/main.rs:34-56` 只开一个 960×600 窗口；`Cargo.toml:3-6` 里 `crates/*` **被注释**。
2. **`changelog/0.1.0–0.3.0.md` 描述的是 classic**（其引用的 `rust/crates/*`、`src/lib/ripple/` 全是 classic 路径），不能当作新版能力。
3. **classic 才是可抄的那一个**（Next.js + zustand UI store + `EditorCore` 12 个 manager + Rust/wgpu WASM 合成器 + WebCodecs 导出 + IndexedDB/OPFS 持久化）。

### 2.2 采纳清单（按性价比）

| # | 借鉴内容 | 落点 | 改造量 | 风险 |
|---|---|---|---|---|
| O1 | **整数时间 + 有理数帧率**：`MediaTime` 120000 tick/s、`FrameRate{numerator,denominator}`（预设 23.976/24/25/29.97/30/48/50/59.94/60/120） | `schema/timeline.py` + 前端时间线类型；渲染层换算 | 大（全量迁移） | 中：一次数据迁移；收益是根治 23.976/drop-frame 对齐与浮点漂移 |
| O2 | **`SceneTracks{overlay[], main(单例), audio[]}` 判别联合**：用类型强制"主视频轨唯一" | `schema/timeline.py`（Pydantic discriminated union）+ 前端 | 中 | 中：需与 7 个 Agent 产出的时间线对齐，避免双份真值 |
| O3 | **Command 模式 + 选择快照 + diff 波纹**：`Command.execute/undo/redo`、`BatchCommand`（逆序 undo）、`CommandManager` 记录选择快照、ripple 用命令前后快照 diff 计算 | 前端编辑器撤销栈（新建 `src/editor/commands/`） | 中 | 中：命令粒度设计是难点，先覆盖 insert/delete/move/split/update |
| O4 | **声明式参数/特效/遮罩契约**：`ParamDefinition` 6 型（number/boolean/color/select/text/font）、`EffectDefinition{params, renderer.passes}`、`MaskDefinition` | 插件系统（`clipwright/plugins/`）+ 前端 Slot 控件集 | 中 | 低-中：与既有 manifest 签名/权限白名单对齐；**不要**照搬 shader 直通（权限面扩大） |
| O5 | **持久化分层与版本化迁移链**：IndexedDB(元数据)+OPFS(媒体)、`navigator.storage.estimate()` 配额预警、v0→v31 每步一个可测迁移器 | 前端离线草稿（若有）+ 后端 schema 演进范式 | 中 | 低 |
| O6 | **预览纹理缓存思路**：`contentHash` 去重 + OffscreenCanvas 复用 + 分段计时（`resolve/buildFrame/syncTextures/renderFrame`） | 前端预览面板（先做 TS Canvas 缓存版） | 中 | 低：不做 Rust/wgpu 版本 |
| O7 | **导出预设与进度/取消契约** 对齐（format/quality/fps/includeAudio + 进度回调） | 前端导出页 + SSE 进度 | 小 | 极低 |
| O8 | **工程规范**：Ultracite 的 a11y/TS 规则清单（`copilot-instructions.md:23-344`）编译成真正的 eslint/biome 配置；CI 3-OS 矩阵 | 前端 lint/CI | 小 | 低（注意：上游自己也没落地成配置） |

**不采纳**：Rust/wgpu WASM 合成器（工程负担 >> 收益）、把导出改成客户端 WebCodecs（服务端 ffmpeg 覆盖更全）、核心状态照搬 zustand（classic 自身核心走 Manager 聚合，UI store 才用 zustand）。

### 2.3 video-use —— 可迁移资产（MIT，全部"方法论"）

| # | 借鉴内容 | 证据 | 落点 | 改造量 |
|---|---|---|---|---|
| V1 | **12 条生产正确性硬规则**：字幕在 filter chain **最后**、逐段提取 + lossless `-c copy` concat、**30ms afade 消爆音**、overlay `setpts=PTS-STARTPTS+T/TB`、SRT 输出时间轴偏移、切点落字边界 + 30–200ms padding、字级 verbatim ASR、按源缓存转写、多动画并行、策略先确认 | `SKILL.md:22-33` | `services/render.py`（断言+顺序）、`agents/edit_agent.py`（prompt 硬约束） | 中 |
| V2 | **每动画一个并行 sub-agent + 10 项自包含 brief**（目标/绝对输出路径/技术规格/调色板/字体/逐帧时间轴/反清单/代码模式/交付检查/"不许提问"） | `SKILL.md:249-262` | `agents/animation_agent.py` 的 MG 分派 | 大（需并发与产物契约） |
| V3 | **引擎按 slot 选型**，不默认某一个引擎（HyperFrames/Remotion/Manim/PIL+ffmpeg 各有适用面） | `SKILL.md:203-214` | MG 生成器路由（先加 PIL 快路径：计数器/打字机/单条进度） | 中 |
| V4 | **渲染后自评闭环**：切点 ±1.5s 逐张看图查跳变/爆音/字幕遮挡/overlay 错位，首尾中点查连贯，`ffprobe` 校验时长，最多 3 轮后透传遗留问题 | `SKILL.md:91-99` | `agents/quality_agent.py` + 新 Tool | 中 |
| V5 | **动画时长/节奏规则**：旁白同步 3s 下限、简单卡 5–7s、复杂图 8–14s；末帧 hold ≥1s；overlay ≥ 旁白+1s；**不同时并行揭示独立元素**；**payoff 同步**（落点帧对齐旁白关键词）；easing 必须三次方禁用 linear；打字机以整串宽度居中 | `SKILL.md:216-237` | `animation/mg/config.yaml` 提示词 + 生成后校验 | 小 |
| V6 | **短语级转写打包**：`[start-end] S{spk} text`，静音 ≥0.5s 或换人断句，约 1/10 token；静音信号保留作切点候选 | `helpers/pack_transcripts.py` | 新服务 + 各 Agent prompt 组装 | 中 |
| V7 | **`timeline_view` 视觉下钻图**：filmstrip + RMS 波形 + 静音着色 + 词标签 + 时间标尺，仅在决策点调用 | `helpers/timeline_view.py` | 新 Tool（ffmpeg+PIL） | 中 |
| V8 | **`project.md` 四段式会话记忆**（Strategy/Decisions/Reasoning/Outstanding，追加式） | `SKILL.md:291-304` | 项目记忆服务 | 小 |
| V9 | **Manim 参考里的通用质量指导**：透明度分层 1.0/0.4/0.15、旁白-first 拆 beat、**同屏 ≤6 元素硬上限**、相邻镜头 variety audit（不同动画/配色/布局/节奏）、tempo 曲线慢→中→快→慢、图表最低要求（轴标签/Y 从 0/数值标注）、数字用平滑计数器 | `skills/manim-video/references/*` | `animation/mg/config.yaml` + `mg/generator.py` 校验项 | 小 |

> V1 的渲染规则要落地时注意：本项目字幕是 **drawtext 双通道发光**实现（见 AGENTS.md「字幕样式字段」），与 video-use 的 libass `subtitles=` 路径不是一回事，宜**取语义**（字幕最后合成、描边/安全区 MarginV≈30%、输出时间轴偏移）而非照抄滤镜链。

---

## 3. HyperFrames 动画增强（Q3，本报告重点）

### 3.1 现状：本地如何调用 HyperFrames（证据）

| 环节 | 现状 | 证据 |
|---|---|---|
| 可用性探针 | `npx hyperframes --version`，90s 超时，进程级缓存 600s，启动期后台预热 120s | `animation/hyperframes_renderer.py:27-43,179-194` |
| 浏览器路径 | 手工注入 `HYPERFRAMES_BROWSER_PATH`（Windows 探测失败会 hang） | 同上 `:71-112` |
| 单 MG 渲染 | `npx hyperframes render <mg_dir> -o <out.mov> --format mov -f <fps> --quiet` | 同上 `:130-177`（`:156-158`） |
| 文字/图解 overlay | 全部 overlay 合成**一个** `index.html`，元素用 `.hf-el` + `data-start/data-duration`，**时序由自写 `setTimeout` JS 控制 visibility + CSS animation** | 同上 `:247-360`（`:337-360` 为 JS） |
| MG 模板 HTML | 根 `data-composition-id="main"` + `data-width/height` + `data-duration`，并写入 **`window.__timelines['main'] = { paused: true }`（空对象桩，非 GSAP timeline）** | `animation/mg_renderer.py:190-207` |
| 管线编排 | 每个 MG 一个独立 npx 进程，`asyncio.Semaphore(6)` 并发；图解 overlay 再走一次 `render_overlays` 合成 | `services/render.py:1918-1996`（`:1990`） |
| 失败判定 | 只判 `returncode==0 && 文件存在`，stderr 取前 300 字 | `hyperframes_renderer.py:173-177,235` |
| 字体 | MG 恒 `font-family: sans-serif`（Persona 字体从未落进成片） | `mg_renderer.py:172,193`；`mg/config.yaml:268,395`（few-shot 里也是 sans-serif） |

### 3.2 本机实测（HyperFrames 0.8.36 / RTX 4070 Ti / beginframe 抓帧 / workers=4–6）

**① 渲染墙钟（同一份 HTML，仅加/不加 `data-no-timeline`）**

| 样本 | 帧数 | 原样 | 加 `data-no-timeline` | 差异 |
|---|---|---|---|---|
| 历史 MG 产物（无 `__timelines` 注册，来自 `_cache/tmp/render_38be02e1/…`） | 135 | **129.1 s**（带 `sub_timeline_readiness_timeout` 警告） | **22.9 s** | **−106 s（5.6×）** |
| 生产 overlay 合成 HTML（`_build_html` 输出，`render.py:1990` 实际调用路径） | 180 | **108.2 s**（带 `sub_timeline_readiness_timeout`） | **18.0 s** | **−90 s（6.0×）** |
| 当前模板 MG（`MGRenderer.render`，含 timeline 桩） | 120 | 22.8 s（无警告） | — | — |

> 上游 lint 对该情况的警告原文：*"The producer polls for timeline registration for up to 45 seconds before timing out, adding 45 s to every render."* 实测总损耗更大（还包含其它就绪等待）。**结论：任何缺少 `window.__timelines` 注册的 HTML 每次渲染都在烧时间；生产 overlay 路径正属此列。**

**② 静态门禁 `npx hyperframes lint`**

- 生产 overlay 合成 HTML（`_build_html` 真实输出）：**1 error + 4 warning**
  - ✗ `missing_timeline_registry`（无时间轴注册）
  - ⚠ `missing_data_no_timeline`（每次渲染 +45s）
  - ⚠ ×2 `studio_missing_editable_id`（元素无稳定 id，Studio 无法定位）
  - ⚠ `timed_element_missing_clip_class`（有时间属性但无 `class="clip"`）
- 历史 MG 产物：✗ `root_missing_dimensions` + ✗ `missing_timeline_registry` + ⚠ `missing_data_no_timeline`
- 当前模板 MG（`mg_title_reveal`）：**0 error**，1 warning `timeline_track_too_dense`（单轨 10 个定时元素）

**③ 浏览器门禁 `npx hyperframes check`（当前模板 MG）**

```
Lint     0 error, 1 warning
Runtime  0 errors, 0 warnings
Layout   0 issues across 9 samples
Motion   0 errors, 0 warnings
Contrast ✗ #mg-e5 1.6:1 (need 3:1, t=1.111s)   ← Check failed
```
即：当前 MG 产物的**文本对比度不达标**（暗底上的次要文字）。上游 `check` 还能查溢出、裁切、动效连续性（`--at-transitions` 专门抓转场接缝处的瞬时重叠）与运行时 JS 报错。

**④ 确定性契约（上游明文，本地违反）**

- *"The renderer takes a time value and produces a pixel buffer. There is no notion of 'playback' — every frame is a fresh seek. Any state that depends on having reached this frame through a prior frame (timers, accumulated state, event-driven animations) will desync when the renderer samples out of order or in parallel."* / *"If you find yourself reaching for `setTimeout`, `requestAnimationFrame`, or `addEventListener` to drive a visual, rebuild it as a tween on the timeline instead."* —— `skills/hyperframes-core/references/determinism-rules.md:68-70`
- *"Never tween `display` or raw `visibility` on a clip element. The framework owns clip visibility, and `lint` rejects it."* —— `skills/hyperframes-core/SKILL.md:73`
- 本地 `_timing_js`（`hyperframes_renderer.py:337-360`）**同时踩中这两条**：`setTimeout` 驱动 + 直接写 `el.style.visibility`。

### 3.3 上游能力 vs 本地用法（差距表，节选并整合实测）

| 能力 | 本地现状 | 上游支持 | 增益 | 成本 |
|---|---|---|---|---|
| 时序声明 | `setTimeout` 墙钟 JS（overlay 路径）/ CSS `@keyframes`（MG 路径） | `data-start/data-duration` + 每个合成**唯一 paused GSAP timeline** 注册到 `window.__timelines[id]`；7 种 runtime adapter（GSAP/CSS/WAAPI/Lottie/Anime.js/Three.js/TypeGPU） | seek-safe 确定性；elastic/back/bounce、stagger、label 编排 | 中 |
| 门禁 | 只看 returncode + 文件存在 | `lint`（静态）、`check`（运行时+布局+动效+**WCAG 对比度**，`--json` 供 agent 读）、`--strict/--strict-all` 直接让 render 失败 | 出片前拦截，替代"事后重渲" | 小 |
| 视觉自检 | 无 | `snapshot --at t1,t2` 出 PNG + contact sheet（`--describe` 可调模型描述）；`keyframes --shot` onion-skin；`compare` 多方案对比表 | 给自评闭环提供"眼睛" | 小 |
| 质量/性能分级 | 只传 `-f fps --quiet` | `--quality draft/standard/high`、`--crf/--video-bitrate`、`--resolution`（DPR 超采样到 4K）、`--workers auto`、`--experimental-fast-capture`、`--low-memory-mode`、`--frames-cache-dir`（Windows C: 迁移） | 预览提速、交付提质 | 小 |
| 参数化 | 自研 `{key}` 占位符 + 残留扫描 + 二次填充 | `data-composition-variables` + `--variables/--variables-file` + `--strict-variables` + `--batch` 批量出片 | 消除整套占位符"双保险"机器；一份模板出 N 份 | 中 |
| 字体 | 恒 `sans-serif` | 编译期确定性字体本地化 + woff2 缓存 + 内嵌 + 内置 11 个 @fontsource 字体 | Persona 字体终于落进成片（中文需自备 woff2） | 小 |
| 透明输出 | 只 `--format mov`（ProRes 4444，体积大） | `webm`(VP9 alpha) / `png-sequence`(RGBA) / `gif` | 中间件体积与编码时间下降（需验证本地 ffmpeg 解 VP9 alpha 后 overlay） | 小 |
| 启动成本 | 每次 `npx hyperframes`（冷启动记录可达 15–90s） | `@hyperframes/producer` 进程内 API + `./server`（Hono）+ Lambda/Cloud Run | 免 npx 冷启动；可上云 | 大（架构） |
| 内容资产 | 11 个私有 JSON 模板 | **registry：180 blocks / 219 components / 13 examples**；20 个 agent skills（含 `hyperframes-animation` 60+ 动效规则、23 个 blueprint、`motion-graphics` 分类、`hyperframes-creative` 20+ 套 `frame-presets` 设计系统）；`shader-transitions`；Studio/`<hyperframes-player>` | MG 质量与多样性的最大来源（few-shot 库 + 设计令牌） | 小-中 |

### 3.4 建议：提升动画 Agent 出片效果的动作（按性价比排序）

**P0（立刻做，1–2 天）**

1. **给 overlay 合成路径加 `data-no-timeline` 或注册真实时间轴** —— 改 `hyperframes_renderer.py:268-285`（`_build_html` 的根 div）。实测同一份 HTML 108.2s → 18.0s。同时把 MG 模板路径的时间轴桩（`mg_renderer.py:207` 的 `{paused:true}`）换成**真实 `gsap.timeline({paused:true})`**，否则一旦上游收紧校验就会失效。
   *验证*：`npx hyperframes render` 计时对比 + 产物中不再出现 `sub_timeline_readiness_timeout`。
2. **把 `hyperframes lint`（快，秒级）接进 MG 生成链路**：`mg/generator.py` 生成 HTML 后先 lint，`error` 即触发已有的自批判/降级路径。这是把"JSON 级校验"升级为"渲染契约级校验"的最低成本一步。
   *验证*：喂一个缺 `data-width` 的 mg_def，应被拦截并降级。
3. **overlay 路径 seek-safe 化**：用 `data-start/data-duration` + CSS `animation-delay`（或 GSAP timeline）替换 `_timing_js` 的 `setTimeout`，元素补 `class="clip"`，停止直接写 `visibility`。
   *验证*：`snapshot --at` 在多次渲染间逐帧一致；`--workers auto` 与 `-w 1` 结果一致。
4. **本项目常驻一条本地 hyperframes 版本**（`npm i -D hyperframes@0.8.36` 或全局固定版本），命令行改为直连本地 bin，绕开 `npx` 解析/下载冷启动。
   *验证*：首帧墙钟时间对比（当前探针注释记录冷启动可达 90s）。

**P1（本月，单项 0.5–2 天）**

5. **渲染门禁升级为 `check`**：MG 送渲染前跑 `hyperframes check --json`，把 `Contrast`/`Layout` 失败转成 trace warning 并触发一次"修 → 重渲"（复用现有自批判闭环）。实测当前模板 MG 就有 1.6:1 的对比度失败。
6. **`snapshot --at` 接入视觉自评**：对 MG 在入场/中段/退场三点出 PNG，交视觉模型（或 `--describe`）打分，闭环回 `generator.py` 的 `critique.min_score`。这一步把"LLM 盲写 HTML"变成"看图迭代"。
7. **质量分级**：预览端点（`POST /api/animation/preview`）用 `--quality draft`，正式渲染用 `standard/high`；`--workers auto` 交给上游并行（当前是"每 MG 一个进程 + 信号量 6"，Chrome 实例开销大）。
8. **Persona 字体落进 MG**：把 Persona 字体以本地 woff2 + `@font-face`（base64 或同目录文件）注入 MG 目录，替换 `sans-serif`；中文需自备字体子集。
9. **`--variables` 取代自研占位符**：MG 模板/LLM 产物用 `data-composition-variables` 声明参数，渲染时传 `--variables`；可让 `_RESIDUAL_PLACEHOLDER_RE` 那套兜底逐渐退役。
10. **透明中间件换 `--format webm`**（VP9 alpha）并验证本地 overlay 合成正常，对比体积与编码时间后再决定是否全量切换。
11. **Windows 专项**：`--frames-cache-dir` 指到非系统盘（当前缓存目录在 `C:`），`--browser-timeout/--protocol-timeout/--player-ready-timeout` 对重 MG 放宽，`--low-memory-mode` 兜底低内存机。

**P2（季度，需要设计）**

12. **把上游 skills 蒸馏成 MG 生成器的知识库**：`skills/hyperframes-animation/rules/*`（60+ 原子动效）、`blueprints/*`（23 个多阶段场景）、`transitions/*`（转场注册表）、`hyperframes-creative/frame-presets/*`（20+ 设计系统与 palettes）→ few-shot + 设计令牌来源；`registry/blocks|components` 直接作为"高质感镜头卡"候选（先跑 `hyperframes catalog --json` 建索引）。
13. **多方案生成 + `compare` 择优**：同一镜头生成 2–3 个变体，用 `hyperframes compare` 出对比图交模型/人工选优 —— 直接对标 video-use 的"并行 sub-agent + 自评"。
14. **`@hyperframes/producer` 常驻 sidecar** 或 `hyperframes cloud/aws-lambda/cloudrun` 远端渲染（与 `docs/remote_render.md` 对齐；注意云端服务条款与费用）。
15. **`<hyperframes-player>` / Studio 接入前端**：MG 预览从"渲染 MP4 再播放"升级为浏览器内即时预览（对应当前 `MgPreviewPage.tsx` 的 2–3s 预览定位），并让用户可在 Studio 里微调时间轴。

### 3.5 其他能提升 MG 质量的项目（Q3 补充）

| 项目 | 许可（核实状态） | 用途 | 建议 |
|---|---|---|---|
| **heygen-com/hyperframes** | Apache-2.0（已直读 LICENSE） | 上游资产：registry 180 blocks/219 components、20 skills、frame-presets、shader-transitions | **第一优先**，见 §3.4 P2 |
| **browser-use/video-use** | MIT（已直读） | 并行动画分派 + 自评闭环 + manim-video skill | 采纳编排方法论 |
| **Manim Community** | MIT | 公式推导、状态机、图变换、数据 morph（本项目逻辑动画的强项补充） | 作为"逻辑/公式类" slot 的备选引擎 |
| **GSAP** | GSAP Standard License（免费，非 OSI；已核实为 GSAP 自有许可） | HyperFrames 的首选适配器；stagger/elastic/back 缓动显著提升质感 | 已在 HF 依赖内；注意许可条款，勿用于竞争性产品 |
| **Lottie / dotLottie（lottie-web）** | MIT（运行时常见 MIT） | 图标/插画级现成动效 | 有 HF lottie adapter，适合"图标动效"slot |
| **Three.js** | MIT | 3D 空间/相机运动 | 有 HF three adapter；仅在需要 3D 时引入 |
| **Anime.js** | MIT | 轻量时间轴/路径动画 | 有 HF animejs adapter |
| **Motion Canvas** | MIT | 备选代码化动画引擎（TS） | 观察即可，暂不引入 |
| **Remotion** | 专有源码可得许可（规模门槛，需购买） | React 组件化合成 | **不建议**进主管线（video-use 也只是可选 slot） |
| 设计资源：Noto/Inter/JetBrains Mono 等 | OFL-1.1 | 字体质感（当前 MG 只有 sans-serif） | 采用时随附 OFL 文本 |
| 设计资产：Lucide 等图标集 | ISC（常见） | 图标一致性 | 使用前核对具体包许可 |

---

## 4. 建议的落地顺序（合并三问）

| 阶段 | 任务 | 落点 | 验收 |
|---|---|---|---|
| **P0-A** | overlay 时序 seek-safe 化 + 加 `data-no-timeline`（或注册时间轴） | `animation/hyperframes_renderer.py` | 渲染计时下降 ≥5×；`strict` 渲染无 `sub_timeline_readiness_timeout` |
| **P0-B** | MG 生成链路接入 `hyperframes lint`（error 即降级） | `animation/mg/generator.py` | 注入缺属性 HTML 被拦截 |
| **P0-C** | `THIRD_PARTY_NOTICES.md` + 移植文件来源头 | 两仓库根目录 | 评审检查通过 |
| **P1-A** | `check` + `snapshot` 接入渲染后自评（含对比度/溢出） | `agents/quality_agent.py`、`mg/generator.py` | 当前模板 MG 的 1.6:1 对比度问题被自动标记 |
| **P1-B** | 渲染规则显式化（V1 的 afade/PTS/字幕最后/时长断言） | `services/render.py`（+ 单测） | 新增断言测试通过 |
| **P1-C** | 质量分级 + 本地 hyperframes 固定 + Windows 缓存/超时参数 | `hyperframes_renderer.py`、预览端点 | 预览耗时下降；C: 盘不增长 |
| **P1-D** | Persona 字体注入 MG | `mg_renderer.py`、`mg/config.yaml` | snapshot 确认字形为 Persona 字体 |
| **P2-A** | 上游 skills/registry 蒸馏成 MG 知识库 + few-shot | `mg/config.yaml`、模板目录 | 生成多样性与主观质量提升（抽检 20 条） |
| **P2-B** | 多方案生成 + `compare` 择优；并行动画 sub-agent | ✅ 已落地（2026-09-13 续批）：并行 sub-agent **早已存在**（`agents/animation_agent.py` 的 `logic_jobs` + 信号量 + gather，本表此前误判为未做）；本轮补 `MGGenerator.generate_best`（`generation.variants` 1–4 并行候选 → 视觉分优先/批判分次之择优） | 13 条测试：单方案等价、择优、视觉分优先、失败隔离、全失败回退、**并发墙钟 < 串行一半**、上限截断、无分取首个成功 |
| **P2-C** | 时间模型（整数 tick + 有理数帧率）/ 命令模式（O1/O3） | `schema/timeline.py`、前端 | 23.976 素材对齐不再漂移；撤销栈单测通过 |

---

## 5. 风险与不采纳

- **不引入 Remotion 作为主引擎**：许可有规模门槛（非 OSI 开源），且现有 HTML→MOV 链路已满足需求。
- **不引入 Rust/wgpu WASM 预览合成器**：前端 + 后端都是 Python/TS 栈，收益不抵构建复杂度（先用 OffscreenCanvas 缓存方案）。
- **不把服务端导出改为客户端 WebCodecs**：ffmpeg 服务端在格式覆盖、码率控制、批量渲染上更强。
- **不整仓拷贝上游资产**：hyperframes 仓库混有 OFL 与商业字体（TT Norms Pro 等）。
- **风险点**：① overlay 契约改动会影响 `tests/clipwright/test_animation.py`、`test_mg_overlay_window.py`（多处断言 `_build_html` 的 HTML 结构），需同步更新；② `check` 每次多起一次 Chrome，需限制在"生成期"而非每次渲染；③ 并行 sub-agent 会与渲染并发争抢资源，需要与 `pipeline_concurrency` 统一治理。

---

## 附录 A：证据与复现命令

```powershell
# 1) 静态门禁（秒级）
npx --yes hyperframes lint <project_dir>            # 生产 overlay HTML：1 error + 4 warning
npx --yes hyperframes check  <project_dir>          # 当前模板 MG：Contrast ✗ #mg-e5 1.6:1

# 2) 渲染计时（同一 HTML 加 data-no-timeline 前后）
npx --yes hyperframes render <dir> -o a.mov --format mov -f 30 --quiet   # 108.2 s / 129.1 s
#   → 在根元素加 data-no-timeline 后：18.0 s / 22.9 s

# 3) 视觉取证
npx --yes hyperframes snapshot <dir> --at 0.5,2.0,3.5 -o snaps
npx --yes hyperframes compare  <dirA> <dirB> --at 1.5 --labels a,b --out cmp.png

# 4) 能力面自查
npx --yes hyperframes --help ; npx --yes hyperframes render --help
```

**材料路径**：`_research/{opencut,opencut-classic,video-use,hyperframes}`（源码）、`_research/findings-*.md`（三份分项报告）、`_research/mg-cur`（当前代码路径生成的 MG 样本）、`_research/snap-mg1`（快照 PNG + contact sheet）。

## 6. 执行状态（2026-09-13）

| 计划项 | 状态 | 落地位置 | 实测/验收 |
|---|---|---|---|
| P0-1 overlay 合成路径注册真实时间轴 | ✅ 已落地 | 新增 `animation/hf_timeline.py`；改写 `animation/hyperframes_renderer.py`（`_build_html` / `_overlay_to_html`）；`animation/mg_renderer.py` 同步替换空对象桩 | 180 帧 overlay：**108.2s → 18.3s**；`lint` 1 error+4 warning → **0/0**；帧 alpha 画像证明入场/保持/退场均按时间轴（旧实现入场瞬间满不透明、超窗口不消失） |
| P0-2 MG 生成期接入 `hyperframes lint` | ✅ 已落地 | `hyperframes_renderer.lint_html()`（best-effort + 注入式 run_cmd）、`mg/generator.py:_lint_gated()`、开关 `mg/config.yaml: generation.lint_gate` | 单测 6 条（error 拦截 / warning 放行 / 关闭开关 / CLI 异常放行 / JSON 解析） |
| P0-3 本地固定 hyperframes 版本 | ✅ 部分落地（解析层） | `hyperframes_renderer._local_bin()` + `_cli_cmd()`：`HYPERFRAMES_BIN` → `node_modules/.bin/hyperframes` → npx | 未在仓库内安装依赖（部署侧执行 `npm i -D hyperframes@0.8.36` 即自动生效） |
| P1-3 质量分级 | ✅ 已落地 | `render_mg_html_to_mov(quality=...)`、`CLIPWRIGHT_HF_QUALITY`、预览端点固定 `draft` | 预览链路走 draft 档 |
| P1-4 Persona 字体落进 MG | ✅ 已落地 | `mg/generator.py:_apply_persona_font()`（+ 提示词「字体令牌」段） | 单测 6 条（覆盖 / 保留显式字体 / 拒绝路径与 URL 注入） |
| §4 提示词质量红线 | ✅ 已落地 | `mg/config.yaml`「通用质量红线（跨引擎，硬性自检）」8 条 | YAML 解析通过；来源已标注（MIT / Apache-2.0 方法论提炼） |
| P1-2 `hyperframes check` 进生成期 | ✅ 已落地（可选） | `hyperframes_renderer.check_html()` + `MGGenerator._check_gated()` + `generation.check_gate: off\|warn\|strict` | 单测 4 条（off 不调 / warn 放行 / strict 拒绝 / 全绿放行）；本机实测新 overlay 合成 `check` **ok=true（lint/runtime/layout/contrast 全 0 问题）** |
| P1-1 `snapshot` 视觉自评闭环 | ✅ 已落地（默认关闭） | 新增 `animation/mg/visual_eval.py` + `MGGenerator._visual_eval_stage()` 接入批判闭环（低分 → `method="visual_repair"`） | 单测 9 条（采样点 / JSON 容错 / 归一化 / 门控 / 低分修复 / 高分保留 / 跳过保留）；本机实测 snapshot 出 3–4 帧正常，视觉 LLM 未配置时静默跳过 |
| 合规动作：`THIRD_PARTY_NOTICES.md` | ✅ 已建 | 两仓库根目录 | 含运行时依赖（HyperFrames/FFmpeg/Chrome）、方法论来源（MIT/Apache-2.0 标注）、商标声明与"将来内联代码"登记模板 |
| V1 渲染硬规则（§1.1） | ✅ 已落地（2026-09-13 续批） | `services/render.py`：`_compose_video_stages`（图解/MG → PIP → 文本/字幕）、`_apply_default_cut_fades`（切点 30ms） | 新增 9 条单测 + `scripts/verify_render_order.py` 实拍取证（命令序 `concat → mg_overlay → text_ass → audio`、`afade` `{0.03, 1.0}`）；顺带修复缓存命中重复叠加、字幕跨目录静默失败 |
| P1-5 `--variables` 取代自研占位符 | ✅ 已落地（2026-09-13 续批） | `MGRenderer.build_variables/_bind_or_fill` + `render_mg_html_to_mov(variables=…)` + 预览端点传参并回显 | 13 条测试 + `scripts/verify_mg_variables.py` 差分验收（空值覆盖后画面可见度 2.394% → **0.000%**，证明变量真驱动文本）；顺带修掉 2 个模板的 `{text}` 残留 |
| P2-1 registry/skills 蒸馏成 MG 知识库 | ✅ 已落地（bounded 版，2026-09-13） | `animation/mg/knowledge/motion_recipes.yaml`（8 条配方）+ 加载/选取模块 + 生成器四条路径注入；上游 registry 的 HTML 片段未采用 | 29 条测试（含**许可白名单 + 无代码片段**合规校验）；节选：data_impact + "增长" → `stat-count-punch` + `growth-bars` |
| P2-2 producer sidecar / 云端渲染 | ↩️ **修正：大部分已存在，无需重做** | ① worker 服务端：`clipwright/worker/api.py`（`/api/worker/health\|assets\|jobs\|download`）+ `tests/test_worker.py`；② 远程客户端：`services/remote_render.py`；③ 入口切换：`api/render.py:160-168` 按 `settings.renderer_backend=local\|remote`（或 `remote_render_url`）选择服务；④ 插件贡献类型 `ContributionKind.RENDERER_BACKEND` 已存在 | 真正残留只有一项：**尚无以 `@hyperframes/producer` 常驻进程为后端的 backend**（当前 hyperframes 只在 MG/图解子路径以 CLI 调用）；按上述贡献类型写一个 backend 即可插件化，无需新建 sidecar 体系 |
| P2-3 `<hyperframes-player>` 接前端 | ✅ 已落地（自研等价方案，2026-09-13） | 后端 `POST /api/animation/mg/html` + 前端 `features/preview/mgHtmlPreview.ts` + `MgPreviewPage` iframe 即时预览（未引入上游 npm 包） | 13 条测试（后端 5 / 前端 8）；iframe 用 `sandbox="allow-scripts"`（不开 same-origin），父子仅 postMessage |

## 附录 B：一句话结论

> **借鉴层面**：video-use 给方法（12 条渲染硬规则 + 并行动画 sub-agent + 自评闭环），OpenCut-classic 给数据模型与编辑器工程范式，HyperFrames 上游给内容资产与质量门禁。
> **许可层面**：MIT×3 + Apache-2.0 全部与 MPL-2.0 兼容，合规成本约 0.5 人日（NOTICES + 文件头 + 不整仓拷贝）。
> **动画层面**：先修"缺 timeline 注册导致每次渲染多烧 90–106 秒"的确定性缺陷，再把上游 lint/check/snapshot 变成生成期门禁，最后用 registry/skills 扩充 MG 的内容与审美上限。
