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
| 前后端对账 | [docs/frontend-backend-parity.md](docs/frontend-backend-parity.md) |
| 项目上下文 | [CLAUDE.md](CLAUDE.md) |

## 一键启动

```powershell
scripts\start.ps1        # 检查依赖 → 启动 MongoDB → 后端(8080) → 前端(5173)
scripts\stop.ps1         # 停止全部
```

- 后端：`uvicorn clipwright.main:app --port 8080`（`CLIPWRIGHT_PORT=8080`，见 `.env.example`）
- 前端：`cd web && npm run dev`（vite 5173；`/api` 代理 → 8080，`/srv` 代理 → 账号服务 8090）
- 账号/市场服务（可选）：`K:\Clipwright Server` 独立仓库，端口 8090

## 项目结构（monorepo）

```
J:\Clipwright
├── clipwright/       # 后端（Python FastAPI）
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
├── web/              # 前端（React 19 + TS 5.5 + Vite，见 web/AGENTS.md）
├── plugins/          # 内置第三方插件目录（diagram_style 等）
├── docs/             # 20+ 文档
└── scripts/          # start/stop/check_env 一键启动脚本
```

## 核心不变量

1. **7 个 Agent**：RequirementsAgent → StructureAgent → MaterialAgent → EditAgent → AnimationAgent → AudioAgent → QualityAgent
2. **44+ 个 Tool**：原子能力层，通过 ToolRegistry 注册
3. **4 个 Skill**：可组合的高级能力，通过 SkillRegistry 注册
4. **33 个 API 路由组**：每个路由文件对应一组端点（225 条路由）
5. **30+ 个 Service**：业务逻辑层
6. **11+ 个 Schema**：Pydantic v2 数据模型
7. **4 个内置 Category Plugin**：知识区长片、鬼畜快剪、数码评测、Vlog 日常
8. **五层架构**：原子能力层 → Agent 编排层 → 类型插件层 → Persona 配置层 → 用户接口层
9. **Persona 配置不直接调用原子能力**：必须经过类型插件层翻译
10. **实时链路为 SSE-only**：pipeline trace / requirements chat / render queue 流；WebSocket 已移除

## 鉴权三模式

`CLIPWRIGHT_ACCOUNT_VERIFY_MODE = off | token | jwt`（默认 off）。jwt 模式本地验签共享密钥，中间件写 `request.state.user_id/user_role`；前端 access token 仅内存 + refresh 走 Server httpOnly cookie（`cw_refresh`）。

## 本期改进（P8–P10 摘要）

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

## 验证清单

修改代码后，运行：
```bash
# 后端全量回归（工作目录 J:\Clipwright）
python -m pytest tests -q        # 期望 1154 passed
python -c "import clipwright.main"

# 前端回归（工作目录 J:\Clipwright\web）
npx tsc --noEmit
npm run test                     # 期望 349 passed
npm run build
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
| [需求分析 Agent](docs/requirements_agent.md) | agents/requirements_agent.py, services/requirements_service.py |
