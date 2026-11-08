# Clipwright / 帧艺 — AI Agent 项目地图

## 快速入口

| 用途 | 文件 |
|------|------|
| 项目总览 | [README.md](README.md) |
| 架构设计 | [docs/structure.md](docs/structure.md) |
| Agent 工作流 | [docs/workflow.md](docs/workflow.md) |
| API 参考 | [docs/api_reference.md](docs/api_reference.md) |
| 开发指南 | [docs/development.md](docs/development.md) |
| 修复与功能补全计划 | [docs/fix-and-feature-plan.md](docs/fix-and-feature-plan.md)（§10 执行进度） |
| 素材内容理解与 NL 落位计划 | [docs/content-understanding-plan-2026-09-14.md](docs/content-understanding-plan-2026-09-14.md)（内容索引→检索→NL 意图→落位校验，10 轮） |
| **主 Actor 化 PRD（下期主线）** | [docs/master-agent-prd-2026-10-26.md](docs/master-agent-prd-2026-10-26.md)（移除 6 阶段管线 → 主 Agent 派发阶段子代理 + 黑板书 + 上轮实测 bug 清单） |
| 工作区 PRD（已落地） | [docs/workspace-prd-2026-10-24.md](docs/workspace-prd-2026-10-24.md)（一个项目一个工作区：materials/assets/renders/cache） |
| 前后端对账 | [docs/frontend-backend-parity.md](docs/frontend-backend-parity.md) |
| 项目上下文 | [CLAUDE.md](CLAUDE.md) |

## 一键启动

```powershell
scripts\start.ps1        # 检查依赖 → 启动 MongoDB → 后端(8080) → 前端(5173)
scripts\stop.ps1         # 停止全部
```

- 后端：`uvicorn clipwright.main:app --port 8080`（`CLIPWRIGHT_PORT=8080`，见 `.env.example`）
- 前端：**独立仓库** `J:\Clipweight-Client`（`npm run dev`，vite 5173；`/api` 代理 → 8080，`/srv` 代理 → 账号服务 8090）
- 账号/市场服务：`K:\Clipwright Server` 独立仓库，端口 8090 —— `scripts\start.ps1` 会自动拉起（首次自动生成 `.env` 与随机 JWT secret；目录可用 `$env:CLIPWRIGHT_SERVER_DIR` 覆盖，端口用 `$env:CLIPWRIGHT_SERVER_PORT`）

## 项目结构（后端 monorepo + 前端独立仓库）

```
J:\Clipwright                    # 后端（Python FastAPI）
├── clipwright/       # 后端源码
│   ├── agents/       # 7 个 Agent（需求→结构→素材→剪辑→动画→音频→质检）
│   ├── api/          # 33 个 API 路由文件
│   ├── services/     # 30+ 后端服务
│   ├── tool/         # 44+ 原子能力工具
│   ├── skill/        # 4 个可组合技能
│   ├── animation/    # 动画系统（37+ 动画编目）
│   ├── category/     # 4 个内置类型插件
│   ├── plugins/      # 第三方插件系统（签名/权限/依赖/启停/错误通道/配置迁移）
│   ├── schema/       # 11+ 数据模型
│   ├── rag/          # RAG 知识库
│   └── persona/      # Persona 配置系统
├── plugins/          # 内置第三方插件目录（diagram_style 等）
├── docs/             # 20+ 文档
└── scripts/          # start/stop/check_env 一键启动脚本

J:\Clipweight-Client              # 前端（React 19 + TS 5.5 + Vite，独立 git 仓库）
└── src/              # 前端源码（见其 AGENTS.md）

K:\Clipwright Server              # 账号/CREADIT/市场服务端（独立仓库，8090）
```

## 核心不变量

1. **7 核心 Agent + 插件 Agent**：RequirementsAgent → StructureAgent → MaterialAgent → EditAgent → AnimationAgent → AudioAgent → QualityAgent；第三方插件可经 AgentRegistry（`orchestrate` 权限）注册自定义 Agent 并入主管线 DAG 或作为子代理被调用（SA-2/3/4）
2. **44+ 个 Tool**：原子能力层，通过 ToolRegistry 注册
3. **3 个内置 Skill**：可组合的高级能力，通过 SkillRegistry 注册（`analyze_video_structure` /
   `generate_captions` / `analyze_audio_rhythm`）。`dub_script` 自 2026-11-03（B3-T6）起**由插件提供**
   （`plugins/tts_feature`），没有该插件时配音能力整体不可用——这是裁定 3 的预期，不是缺失兜底
4. **33 个 API 路由组**：每个路由文件对应一组端点（225 条路由）
5. **30+ 个 Service**：业务逻辑层
6. **11+ 个 Schema**：Pydantic v2 数据模型
7. **4 个内置 Category Plugin**：知识区长片、鬼畜快剪、数码评测、Vlog 日常
8. **五层架构**：原子能力层 → Agent 编排层 → 类型插件层 → Persona 配置层 → 用户接口层
9. **Persona 配置不直接调用原子能力**：必须经过类型插件层翻译
10. **实时链路为 SSE-only**：pipeline trace / requirements chat / render queue 流；WebSocket 已移除
11. **Agent 不写死工具名**：业务 Agent 通过**语义能力名**（`frame_check` / `video_trim` / `pv_audit` …）经
    `clipwright/tool/capabilities.py` 在调用时刻解析到具体工具；插件给工具声明 `capability` 即可接管，
    装卸/配置变化下一次调用立即生效。
12. **能力与工具可插拔，不在引擎里写死**（2026-10-31 用户裁定）：新增/迁移一个工具，**只改它自己的文件**——
    在工具类上声明三行即可：

    ```python
    class MyTool(BaseTool):
        name = "my_tool"
        capability = "image_clip"        # 语义能力名（可接管内置能力，优先级用 capability_priority）
        permission_tier = "write_local"  # read / write_local / costly / dangerous
        dry_run_capable = False          # 是否支持 dry-run 结构预检
    ```

    `capability_matrix` **只认**工具自声明的 `permission_tier`。`TIER_MAP` 已于 2026-10-31
    **全部迁移完毕并清空**（保留空 dict 仅为兼容 `explain()` 与外部读取），内置工具与插件工具
    （`plugins/*/`，含 `text_animations` 的 25 个文字动画、`pv_engine` 的 9 个引擎工具）都改为
    类上自声明。漏声明的内置工具仍硬失败（fail-closed），插件工具则降级为"不进 agent 工具面"并留痕。

24. **内置 Provider 让位必须发生在注册之前；插件对外 HTTP 只走通用能力网关**（PRD §15，2026-11-02）：
    - 每个 provider 家族（`llm` / `embedding` / `rerank` / `stt` / `tts`）的内置注册
      **只能**调 `clipwright/providers/builtin_registry.py` 的 `register_builtin_once()`——
      它先 `ContributionRegistry.resolve(kind, 探针 id)`，查到就让位、查不到才登记内置。
      **禁止**写成「先注册内置、插件后注册」：`_validate` 对已存在内置默认 REJECT
      （`plugins/contribution.py:189-193`），那样写会让同名插件被直接拒掉，而报错只显示
      「不得覆盖内置贡献」，根因极难查。六处复制粘贴的旧写法已全部收敛到该函数。
    - **TTS 三个 provider id 已全部插件化，engine 的内置注册与配音编排已删除**（2026-11-03 执行
      B3-T6）：`plugins/tts_cosyvoice`（cosyvoice）、`plugins/tts_qwen`（qwen-tts）、
      `plugins/tts_minimax`（minimax）负责 provider；`plugins/tts_feature` 负责 `dub_script` 技能
      与切句（`plugins/tts_feature/split_text.py`）。engine 侧 `VoiceService.dub_script()`、
      `clipwright/skill/dub.py`、`ensure_tts_providers_registered()` 均已删除——**没有 TTS 插件时
      配音能力整体不可用**（`/api/voice/dub` → 503 + 可读原因），这是裁定 3 的预期，
      不许再补"内置兜底"。行为边界用 `py -3.14 scripts/verify_tts_teardown.py` 一条命令自证。
    - 「让位」与「退场」是两件事：让位 = 插件在就让；退场 = 谁都不在也不注册。裁定 3
      要求 TTS **退场**（TTS 功能插件验收通过后 `ensure_tts_providers_registered()` 不再默认
      调用），**不是**改让位逻辑。
    - **协议实现住在插件，engine 只留协议与只读注册表**（2026-11-08 审计整改 B7-T7.2/T7.3/T7.4）：
      `llm` / `embedding` / `rerank` / `stt` 的实现类与探测函数已搬进
      `plugins/{llm_anthropic,llm_openai_compatible,embedding_providers,rerank_providers,stt_providers}/`；
      engine 的 `providers/*` 只保留协议（`providers/base.py`）与 `resolve_*` / `*_names`（**只读注册表**），
      且 `ensure_*_providers_registered` 这类**懒注册补位已全部删除**——它会让"禁用插件"不改变行为，
      是外壳化的根因。自检 `provider_impls_in_plugins` 守住这条（新增实现回 engine 会红）。
      LLM 的工具结果消息风格由 provider 的 `tool_result_style()` 声明（不再按 provider 名分支）。
      **tts 家族待迁**（B7-T7.1），在迁移完成前不要声称"全部归位"。
    - 插件对外暴露的 HTTP 入口**只有** `POST /api/capability/{name}`（`api/capability.py`，
      契约见 PRD §15.1.5）。它按**能力名**解析（`capabilities.resolve_name`，能力名优先、
      工具名兜底），状态码语义固定：`404` 解析不到（插件未装/被禁用）、`422` 参数或路径
      不合规、`503` 已解析但依赖缺失、`200` 成功。**网关内禁止任何具体 provider / 能力名
      分支**；插件不得自挂 router，也不新增 `API_ROUTE` 贡献种类（已否决）。`/api/voice/*`
      过渡期只做薄转发，不承载编排。
    - **贡献 id 允许连字符**（2026-11-02，Batch 4 放宽）：`schema/plugin.py` 的
      `ContributionDecl.id` pattern 放宽到 `^[a-z][a-z0-9_-]{1,63}$`。仓库里既有 provider id
      本来就带连字符（`faster-whisper` / `qwen-tts`），原 pattern 不含 `-` 会让插件**无法声明**
      这些 id（表现是 manifest 校验失败，而内置注册不校验所以不报错）。id 只做门面键，
      不参与文件路径。

25. **插件 UI 控件的值一律回传宿主，执行（上传/落盘/请求）也在宿主**（PRD §15.9，2026-11-02）：
    - `plugins/*/` 的 `ui_slot` 贡献只声明 `layout.widgets`（声明式 JSON）；前端
      `PluginLayoutRenderer` 渲染、`Slot.onValuesChange` 把控件值交回宿主页面。
      **插件拿不到上传 API、工作区路径、凭据**，所以 `file` 控件（`UIFile`）**没有**
      `action` / `endpoint` 字段——上传由宿主执行（`HomePage.pickAudio`：
      客户端时长探测 → `assetApi.upload` → 去重提示），宿主按「同一 `File` 对象」去重。
    - `select` 的动态选项走**宿主白名单**：`optionsFrom` 只能引用宿主已解析的数据源
      （当前 `voices`），插件拿不到任意数据；未知数据源名渲染「暂无可用选项」并给提示。
    - 未知控件类型**不许静默丢弃**：渲染器走 `default` 分支给可见占位（不变量 #13 的同一条纪律）。
    - 内置配音块现在只在 `usePluginSlots('create.dub')` 为空时回落；整块删除属 B3-T6，
      等人跑完测试后与删除清单一起执行。

26. **声明式展示 spec：能力在插件，机制在 engine，渲染在前端**（2026-11-08 能力迁移完成）：
    - **能力归属**：组件白名单、9 个上限、动作/色调/字号枚举、卡片配方、教模型的规范全部在
      `plugins/genui/`（2 条 `display_spec` + 1 条 `prompt` 贡献）；engine 只有第 18 类扩展点
      `ContributionKind.DISPLAY_SPEC`（**纯数据**，`object` 必须 `None`）与通用机制
      `services/display_spec/{contract,sanitizer,recipe,assemble,legacy}.py`。**engine 里不允许再出现**
      genui 专属实现/导入/常量（判据：文件不存在、零 `schema.genui` 导入、零 `GENUI_` 常量、
      `"genui"` 字面量 ≤5 且只作老数据键兼容）。
    - **字段名是 `display_specs`**（`spec_id -> spec`），老键 `genui` 只在读路径归一（不双写、
      不批量迁移）。权威数据永远是 `CreativeBrief` 其它字段与规划书 `markdown_content`/`raw_scenes`。
    - **两道闸，规则都来自契约**：写入侧 `sanitize_display_spec`（engine，**信任边界不可外包**——
      插件若自带校验器就能放宽自己的白名单）、渲染前 `validate.ts`（前端，防旧缓存/历史 session）。
      契约获取：`GET /api/plugin/contributions?kind=display_spec`（运行期唯一来源）；前端拿不到契约
      → **不渲染**（不猜白名单）。前端原语是编译期闭包，新原语必须发前端版本。
    - **禁用插件 = 没有展示 spec**（2026-11-07 用户裁定，推翻早前"engine 保底"方案），三条保证：
      G1 业务字段不动（无契约时 `ensure_display_specs` 原样返回、不建空容器）、G2 指纹不漂
      （`_brief_hash` 排除 `{"genui","display_specs"}`）、G3 写入不阻断（异常只记 warning）。
    - **动作三种** `ask/select/confirm` 都已实现：`ask`→发回当前需求会话；`select`→选项组，
      点击发 `"选择："+value`（**文案宿主拼**，模型只决定选项）；`confirm`→卡片确认回调
      （按 `scope` 区分简报/规划书）+ **`window.confirm` 二次确认**，`busy` 不响应、`scope` 缺失不猜。
      **禁止** spec 携带 endpoint/URL/method；白名单是「词法」契约、可点性由宿主 `supportedActions`
      决定；审阅覆盖层用 `hideActions` 不渲染动作按钮（C10）。
    - **不碰 HTML/脚本/外链**：渲染器无 `dangerouslySetInnerHTML`，文本一律 `asText`；
      首版不开放 `mermaid/echart/plot/scene3d/svg/image`（要开必须单独安全评审）。
    - **生产接线在服务层**：`_handle_gathering`（主/修订）与 `_translate_plan`（含 legacy 路径）
      调 `ensure_display_specs(source, payload)`；`_with_display_specs` 负责读路径补 spec 与老键归一。
      `agents/requirements_agent.py` 当前无生产调用点，只做字段归一。
    - 详见 [docs/genui.md](docs/genui.md)（当前态）与
      [docs/genui-plugin-migration-prd-2026-11-07.md](docs/genui-plugin-migration-prd-2026-11-07.md)（迁移裁定）。

## 鉴权三模式

`CLIPWRIGHT_ACCOUNT_VERIFY_MODE = off | token | jwt`（默认 off）。jwt 模式本地验签共享密钥，中间件写 `request.state.user_id/user_role`；前端 access token 仅内存 + refresh 走 Server httpOnly cookie（`cw_refresh`）。

## 本期改进（P8–P10 摘要）

- **主 Actor 化（本期已落地，见 [docs/master-agent-prd-2026-10-26.md](docs/master-agent-prd-2026-10-26.md)）**：
  老 6 阶段管线**执行路径已切断**（`settings.pipeline_enabled=false` 默认；`run_pipeline` 能力拒绝、
  `POST /api/pipeline/run-async` 返回 410、`proceed` 单轨、规划失败退化为 6 个 `run_stage`）；
  新增阶段子代理通道 `run_stage/spawn_stage/get_stage/list_stages` + 黑板
  (`services/task_blackboard.py`：`by` 归属、timeline 单向覆盖、2MB 转指针) + 阶段契约
  (`schema/stage.py`、`services/stage_inputs.py`、`services/stage_merge.py`) + 自愈闭包
  (`services/stage_closure.py`：`edit → [animation, audio]`，quality 永不入闭包)。
  **能力目录修好了**：`_capability_catalog()` 现在合并 `META_CAPABILITIES`（此前主 Agent
  看不见 `spawn_subagent`/`run_stage`，永远不会主动派子代理）。
- **工作区（本期已落地）**：首页「一句话出片（主 Agent）」与 `/task` 启动页已下线，主 Agent 改由「新建制作」承载（`requirements/proceed` 单轨：`agent_mode=on` → Task）；「新建制作」新增 04 工作区（工程/素材/下载/成片同根，`materials/` 唯一用户写入区）＋自动索引与视觉解读。见 [docs/workspace-prd-2026-10-24.md](docs/workspace-prd-2026-10-24.md)。
- **视觉解读变真（本期）**：`label_shot` 改为 clip → **llm** → 本地分类器；`vision_llm_model=deepseek-chat`
  （DeepSeek flash 多模态）；`imread_unicode()` 修中文路径（`cv2.imread` 读不了中文名）；
  结果带 `source`（`llm/clip/classifier/filename`），**只看了文件名一律 `degraded`**，不再假装 `ok`。
- **配音与字幕（本期）**：上传配音入口始终可见（旧实现被 `mode` 派生自我隐藏，用户根本没有上传入口）；
  上传配音的字幕对齐从 `sfx` 分支里移出、**默认执行**（实测时间；段数不一致按配音时长等比；样式继承）；
  不上传时用自动配音生成旁白，字幕按 TTS 句级时间戳对齐。见 docs/voice_tts.md。
- **能力解析层（本期）**：`tool/capabilities.py` + `BaseAgent.resolve_tool/call_tool/call_skill/tool_schemas/run_tool_loop`；
  Material/Edit/Animation/Audio/Quality 的硬编码工具名全部改为能力解析；Tool/Skill 列表实时读取。
- **PV 引擎插件 v2**：双布局（`engine/` 包与上游 `_build/`）、契约内省（doctor）、结构化审计、
  带预算渲染、Timeline→project 桥、拍点/对齐；插件配置注入修复（`loader._inject_plugin_config`）。
  详见 [docs/pv-engine-plugin.md](docs/pv-engine-plugin.md)、[docs/pv-engine-known-gaps.md](docs/pv-engine-known-gaps.md)。

- **运营调度**：webhook 事件接线（pipeline/render 完成·失败，secret Fernet 加密落盘）、定时调度（interval/daily + Mongo scheduled_runs）、热点选题、脚本续写/改写、beat-sync、色彩匹配工具、参考成片风格模仿、dry-run 预览、管线诊断。
- **资产治理**：sha256 去重、used_count、素材巡检/违规检测（`/api/asset/governance/*`）。
- **Persona 治理**：复制/派生/导出/导入、知识库文档管理（删/改）、RAG 异步化、ChatForge 会话落盘、PersonaLearner 编辑事件接线（`/{persona_id}/learn`）。
- **插件治理**：manifest 签名（HMAC）+ 权限白名单、依赖解析、启停持久化、配置迁移、错误通道、reload 回滚、secret 加密、审计日志；前端 UI 控件集扩充（input/select/checkbox/slider）+ UI 预览 + 未加载插件预配置。
- **前端**：BGM 素材源、水印/特效工具接线、项目归档 zip、导出预设、dry-run 开关、账号会话（`/srv` 代理）。

### 历史改进（早期批次）

- **MG 动画质量深度改进**：`animation/mg/config.yaml` 重写专业动效设计原则提示词（easing 优先、预期动作、错峰 0.2-0.5s、粒子、光效扫过、逐关键帧 `easing` 字段）；`animation/mg_renderer.py` 支持新元素类型（`line`/`circle`/`ring`/`arc`/`bg`）与 `text_shadow`/`box_shadow`/`background`（渐变）/`letter_spacing`/`font_weight`/`transform_origin`/`line_height`/`height`/`border_radius`；`animation/mg/templates/` 内置 8 个专业模板（title_reveal/comparison_split/data_bars/timeline_progress/counter_up/flow_arrows/quote_card/mindmap）；`agents/structure_agent.py` 强化 `mg_dynamic` 标记引导；`animation/mg/generator.py` 新增 LLM 自批判闭环（score < `critique.min_score` 时带批判反馈修复一次，LLM 失败静默跳过）。
- **字幕样式字段**：`schema/timeline.py` Clip 新增 11 个可选样式字段——`font_weight`/`font_italic`/`letter_spacing`/`stroke_width`/`stroke_color`/`shadow_x`/`shadow_y`/`shadow_color`/`shadow_blur`/`glow_color`/`glow_width`；`services/render.py` drawtext 双通道实现发光，支持描边/阴影/粗体渲染；`services/subtitle.py` 新生成字幕 clip 统一默认样式（font_size=48、font_color=#ffffff、text_align=center）。详见 `docs/api_reference.md`「字幕样式字段」。
- **新端点 `GET /api/pipeline/runs`**：列出最近管线运行记录，返回 `[{id, topic, status, duration_ms, started_at, agents[{agent,start,dur,status}]}]`，供 Pipeline 管理页展示真实运行数据。

## 常用工作流

### 新增一个 Tool

1. 在 `clipwright/tool/` 下创建工具文件，继承 `BaseTool`
2. 实现 `execute()` 方法和 `to_llm_tool()` 方法
3. 在 `tool/__init__.py` 的 `register_builtin_tools()` 中注册
4. 更新 `docs/api_reference.md` 工具列表

### 新增一个 Skill

1. 在 `clipwright/skill/` 下创建技能文件，继承 `BaseSkill`
2. 实现核心逻辑和 `to_llm_tool()` 方法
3. 在 `skill/builtin.py` 的 `register_builtin_skills()` 中注册
4. 更新 `docs/api_reference.md` 技能列表

### 新增一个 Agent

1. 在 `clipwright/agents/` 下创建 Agent 文件，继承 `BaseAgent`
2. 实现 `execute()` 方法
3. 在 `agents/__init__.py` 的代理列表中注册
4. 更新 `docs/structure.md` 和 `docs/workflow.md`

### 新增一个插件 Agent（SA-2/3/4）

第三方插件可注册自定义 Agent（`clipwright/agents/registry.py`），两种形态：

1. **随主管线执行**：manifest 声明 `kind: agent` + `permissions: [orchestrate]`，
   插件 `initialize()` 内 `AgentRegistry.register(agent, name, plugin_id, deps=[...])`
   ——Agent 按 deps 并入主管线 DAG（依赖核心 Agent 或其它已注册 Agent；
   未知依赖/环会被剔除）。输入为 `PluginAgentInput`（context/data/timeline），
   输出 `PluginAgentOutput`（decision/payload，payload 键合并进共享数据）
2. **被子代理调用**：宿主 Agent/插件工具内
   `await run_sub_agent(ctx, agent_name, payload)`（`services/subagent.py`）——
   嵌套深度上限 2、宿主管线取消感知、用量以「宿主:子代理」复合名归因、
   per (pipeline_id, agent) 熔断（3 次/60s 恢复）、默认 120s 超时
3. Agent 边界 Hook：`HookPoint.PRE_AGENT`（可改写 input 或 skip）与
   `POST_AGENT`（只读观测 decision/result/error）
4. 参考：`clipwright/PLUGIN_AGENT_INTEGRATION.md`、`plugins/subagent_demo/`

### 新增一个动画类型（LLM 化后）

1. 在 `clipwright/animation/catalog.py` 注册 id / name / desc（逻辑动画清单唯一事实来源）
2. 需要更具体的构图/动效约束时，在 `clipwright/animation/type_specs.py` 的 `_OVERRIDES` 增加条目
3. 写类型专属提示词：`clipwright/animation/prompts/types/<id>.md`，分段固定为
   `Intent` / `Semantic constraints` / `Composition` / `Motion` / `Anti-patterns` / `Example`
4. 需要确定性回退图形时，在 `clipwright/animation/diagram_svg.py` 加 preset 并同步
   `type_specs._KNOWN_PRESETS`（回退 SVG 必须带真实关键帧，见 `_inject_motion`）
5. 校验：`python -c "from clipwright.animation.type_specs import validate_registry; print(validate_registry())"` 必须为空

### 新增一个 Service

1. 在 `clipwright/services/` 下创建服务文件
2. 在依赖注入中注册
3. 更新 `docs/services_overview.md`

### 新增 API 端点

1. 在 `clipwright/api/` 下创建路由文件
2. 在 `main.py` 中注册路由
3. 更新 `docs/api_reference.md` 与 `docs/frontend-backend-parity.md`（§1 路由表 + §2 客户端矩阵）

## 文档维护规则

每次功能更新后，必须同步更新 `docs/` 中的相关文档。详见 [docs/README.md](docs/README.md) 的文档维护规则。
执行计划期间，`docs/fix-and-feature-plan.md` §10 每批追加执行记录，并同步 `J:\Clipweight-Client\docs\` 副本。

## 新增模块

| 文档 | 说明 |
|------|------|
| [素材系统](docs/material_system.md) | 多源素材搜索与检索系统 |
| [语音与 TTS](docs/voice_tts.md) | 声音克隆、语音合成与配音 |
| [动画系统](docs/animation_system.md) | 动画编目、渲染管线与 MG 动画 |
| [服务概览](docs/services_overview.md) | 全部后端服务层模块说明 |
| [需求分析 Agent](docs/requirements_agent.md) | Requirements Agent 设计与职责 |
| [GenUI 展示](docs/genui.md) | schema/genui.py、plugins/genui、src/features/genui/*（契约/产出路径/加组件条件） |
| [整数时间基座](docs/timebase.md) | `utils/timebase.py`：整数 tick + 有理数帧率（O1），换算纪律、时间码、漂移校正接线点 |

## 验证清单

修改代码后，运行（**下面每一行都是 CI 门禁，缺一行就可能在 CI 红灯**）：
```bash
# 后端全量回归（工作目录 J:\Clipwright）
python -m pytest tests -q        # 期望 4499 passed / 4 skipped / 1 xfailed（2026-10-29 实测）
python -m pytest tests/test_event_contract.py -q   # 事件契约：EVENT_TYPES ↔ 前端 parser 键集合
python -c "import clipwright.main"
# CI 同款硬门禁：只跑 pytest 会漏掉"未定义名 / 坏语法"，CI 立刻 F821 红灯
python -m ruff check clipwright --select E9,F63,F7,F82,F823 --output-format=concise
python scripts/verify_timebase.py   # 整数时间基座核对（VERIFY PASS）
python scripts/e2e_smoke.py         # 全管线冒烟（SMOKE PASS）
python scripts/route_inventory.py --out docs/route_inventory.txt  # 路由清单（文档引用生成物）

# 前端回归（工作目录 J:\Clipweight-Client）
npm run typecheck                # = tsc --noEmit
npm run test                     # 期望 1085 passed（127 files，2026-10-29 实测）
npm run lint                     # 允许 warning，不允许 error
npm run build                    # tsc 过不代表 build 过（vite 另有一套解析）
```

## 文档与对应代码

| 文档 | 覆盖的代码模块 |
|------|---------------|
| [架构总览](docs/structure.md) | 全部五层架构 |
| [Agent 工作流](docs/workflow.md) | agents/, services/pipeline*.py, services/agent_bus.py |
| [API 参考](docs/api_reference.md) | api/ 全部 33 个路由文件 |
| [开发指南](docs/development.md) | tool/, skill/, plugins/, 开发规范 |
| [Persona 系统](docs/Persona.md) | persona/ 模块 |
| [素材系统](docs/material_system.md) | 素材源插件, material Agent |
| [语音与 TTS](docs/voice_tts.md) | services/voice.py, api/voice.py |
| [动画系统](docs/animation_system.md) | animation/ 全部 |
| [服务概览](docs/services_overview.md) | services/ 全部服务 |
| [整数时间基座](docs/timebase.md) | utils/timebase.py, schema/timeline.py 的时间派生属性, services/render.py 漂移校正, services/timeline_view.py |
| [需求分析 Agent](docs/requirements_agent.md) | agents/requirements_agent.py, services/requirements_service.py |
