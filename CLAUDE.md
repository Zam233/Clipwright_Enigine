# Clipwright / 帧艺 — 项目上下文

## 项目概要

帧艺 ClipWright 由两大子系统构成：

### 1. 帧艺 ClipWright 内容视频编排引擎（纯后端）

> 核心文档：`docs/structure.md` · `docs/Persona.md` · `docs/workflow.md`

Persona 驱动的 Agent 引擎，包含五层架构和 7 个 Agent 构成 LangGraph 管线。可独立运行，对外暴露 REST API。

### 2. 帧艺 ClipWright AI 辅助视频创作系统（全栈）

> 项目总览：`README.md`

前后端分离的 AI 辅助视频创作系统 = 内容视频编排引擎（后端）+ Web 视频编辑器（前端）。

- **后端**：Python + FastAPI + LangGraph。Persona 驱动的 Agent 引擎，7 个 Agent 构成管线。
- **前端**：React 19 + TypeScript + Zustand + Canvas 自研时间轴。对标 PR 的多轨编辑器。
- **Plugin 系统**：视频类型插件（knowledge_longform、kichiku_fastcut、digital_review、vlog_daily）
- **Persona 系统**：四层复合（参数层/示例层/嵌入层/模型层）

## 五层架构

自底向上：原子能力层 → Agent 编排层 → 类型插件层 → Persona 配置层 → 用户接口层

核心约束：Persona 配置层不直接调用原子能力，必须经过类型插件层翻译。

## 当前阶段

Phase 1：后端单 Agent 链条 + API；前端基础时间轴编辑器。

---

## 已安装的第三方 Skills

### Superpowers（obra/superpowers，243K⭐）

预装在 `.claude/skills/superpowers/`，包含 10 个子 skill：

| Skill | 场景 | 用法 |
|-------|------|------|
| `superpowers` 主 skill | 全流程工程开发：规划 → 实现 → 测试 → 代码审查 | 自动按需加载，也可手动 `/` 调用子 skill |
| `test-driven-development` | 新增 Agent/组件/API 前先写测试 | TDD 循环：Red → Green → Refactor |
| `systematic-debugging` | Agent 管线断裂、渲染失败、状态异常 | 假设驱动 + 二分定位 |
| `dispatching-parallel-agents` | 需要并行执行多个独立任务 | 自动拆解任务并派发子 agent |
| `subagent-driven-development` | 复杂功能需要隔离上下文实现 | 为子任务创建隔离的 agent 环境 |
| `requesting-code-review` | 提交 PR 前请求审查 | 生成 review 请求 + 上下文摘要 |
| `receiving-code-review` | 处理 review 反馈 | 逐条处理 + 标记 resolved |
| `executing-plans` | 按计划逐步实现 | 跟踪进度，不偏离 scope |
| `finishing-a-development-branch` | 分支完成时清理：squash、rebase、PR | 规范化分支收尾 |
| `using-git-worktrees` | 多分支并行开发 | 隔离工作区，互不干扰 |
| `brainstorming` | 方案设计前探索多种可能性 | 结构化发散 + 收敛 |

### 内置 Skills（Claude Code 原生）

| Skill | 何时使用 |
|-------|---------|
| `/tdd` | TDD 开发循环 |
| `/diagnose` | 硬 bug 和性能回归的诊断循环 |
| `/simplify` | 提交前代码 review：复用、质量、效率 |
| `/improve-codebase-architecture` | 架构边界检查，模块耦合分析 |
| `/to-issues` | 将路线图拆为独立可认领的 issue |
| `/handoff` | 模块间 / session 间上下文移交 |
| `/prototype` | 快速构建可抛弃的原型验证方案 |

---

## 已安装的 MCP 服务器

### mcp-video（87 工具）✅ 已连接

安装：`claude mcp add` — 基于 FFmpeg + Hyperframes 的视频处理工具集。

直接对应 Clipwright **原子能力层**，可在开发 Agent 时直接调用以下能力：

| 类别 | 工具数 | 对应 Clipwright 模块 | 项目内实现 |
|------|--------|---------------------|-----------|
| 核心视频 (trim/merge/text/audio/resize/chroma key/字幕/水印/稳定) | 32 | 原子能力层 | ✅ ToolRegistry + FFmpeg |
| AI (Whisper 转录/场景检测/Demucs 音源分离/升格/调色) | 11 | AudioAgent, QA | ✅ 场景检测已实现 |
| Hyperframes (HTML → MP4 代码驱动视频) | 8 | AnimationAgent | 📌 Phase 2 |
| 音频合成 (波形/预设/空间音频) | 7 | AudioAgent | ✅ FFmpeg 封装 |
| 视觉效果 (暗角/色差/扫描线/发光/蒙版) | 8 | AnimationAgent | 📌 Phase 2 |
| 转场 (Glitch/Pixelate/Morph) | 3 | 类型插件层 | 📌 委托 mcp-video |
| 布局与运动 (Grid/PiP/动画文字/进度条) | 6 | 时间轴编辑器 | ⏳ drawtext 基础版 |
| 分析 (场景检测/故事板/质量对比/波形) | 8 | 质检 Agent | ✅ FFmpeg scene detect |
### framely-cli（~15 工具）✅ 已安装

安装：`claude mcp add` — AI 原生视频编辑器 CLI。

同领域架构参考。支持 project init、asset management、silence cutting、captions、transcription、rendering。所有操作原子化可撤销，媒体文件不出本地。

---

## 已实现的 Skill 系统

`clipwright/skill/` — 可组合的高级能力（编排多个 Tool 完成业务目标）：

| Skill | 编排的工具 | 依赖 | 状态 |
|-------|-----------|------|------|
| `analyze_video_structure` | scene_detect + audio_extract + bpm_detect | ffmpeg | ✅ 已实现 |
| `analyze_audio_rhythm` | bpm_detect | ffmpeg | ✅ 已实现 |
| `generate_captions` | 纯逻辑（无工具依赖） | 无 | ✅ 已实现 |
| `summarize_captions` | caption_segment（示例插件注册） | 无 | ✅ 示例插件 |

Skill 与 Tool 的注册方式完全一致：
- 继承 `BaseSkill` → `SkillRegistry.register()` → 自动生成 LLM tool schema
- 也支持 `to_llm_tool()` 方法，与 `BaseTool` 统一接口
- 通过 `/api/skill/list` 和 `/api/skill/execute` 暴露

## 已实现的 Tool 系统

`clipwright/tool/` — 原子能力层，通过 `ToolRegistry` 统一注册和分发：

| 工具 | 后端 | 状态 |
|------|------|------|
| `video_trim`, `video_concat`, `video_overlay` | FFmpeg subprocess | ✅ 已实现 |
| `audio_extract`, `bpm_detect`, `audio_replace` | FFmpeg subprocess | ✅ 已实现 |
| `scene_detect` | FFmpeg filter | ✅ 已实现 |
| `semantic_match` | CLIP（占位） | ⏳ 待接入推理服务 |
| `typewriter_animation` | FFmpeg drawtext | ✅ 基础实现 |
| `tracking_text` | Manim（占位） | 📌 Phase 2 |

调用方式：
- REST API: `POST /api/tool/execute?name=scene_detect`
- Python: `await ToolRegistry.execute("video_trim", input_path=..., start_sec=10, duration_sec=5)`
- 批量: `POST /api/tool/batch`

### LLM Tool Calling（Agent 使用工具）

Agent（如 StructureAgent）在 LLM 模式下可自动调用工具：

```
LLM Agent → ① 构造 AgentToolkit（感知 ToolRegistry 可用工具）
          → ② 生成 LLM tool schemas（Anthropic/OpenAI 格式）
          → ③ with_tools() 循环：LLM 推理 ↔ 工具执行 ↔ 结果反馈
          → ④ 返回最终输出
```

关键类：
- `AgentToolkit(tool_names, fmt)` — 编译工具定义供 Agent 使用
- `LLMService.with_tools(system, user, tool_executor, tools)` — 完整的 tool-use 循环
- `BaseTool.to_llm_tool(fmt)` — 从 Python 签名自动生成 LLM tool schema

当前支持工具调用的 Agent：`StructureAgent`（LLM 可按需调用 scene_detect / bpm_detect / semantic_match）。

## 已实现的 Plugin 系统

`clipwright/plugins/` — 第三方插件发现与加载：

| 组件 | 状态 | 说明 |
|------|------|------|
| `PluginLoader` | ✅ | 支持 `plugin.yaml` + `importlib` 动态导入 |
| `HookRegistry` | ✅ | 7 个 HookPoint（Pipeline/Agent/Render 生命周期） |
| REST API | ✅ | `/api/plugin/list`, `/discover`, `/load/{id}`, `/unload/{id}` |

第三方插件放在 `plugins/{plugin_id}/` 目录，自动发现加载。

---

## 代码规范

- 前端：React 19 + TypeScript + Zustand + Radix UI + Tailwind
- 后端：Python + FastAPI + LangGraph + Pydantic
- 时间线数据格式：前后端统一的 JSON schema（Agent 输出 = 编辑器可编辑）
- Agent 之间通过 LangGraph 节点图传递数据，每个节点独立可测
- Persona 配置以 YAML 存储，支持版本管理
- 遵循[Google Python Style Guide](https://google.github.io/styleguide/pyguide.html)。

## 关键文件入口

- `docs/structure.md` — 完整架构设计文档（内容视频编排引擎）
- `docs/Persona.md` — Persona 系统设计文档（四层复合体系）
- `docs/workflow.md` — Agent 工作流设计文档
- `docs/quickstart.md` — 快速开始和使用说明
- `docs/api_reference.md` — API 参考文档
- `docs/persona_forge.md` — PersonaForge 使用说明
- `docs/development.md` — 开发指南
- `docs/material_system.md` — 素材系统文档
- `docs/voice_tts.md` — 语音与 TTS 系统文档
- `docs/animation_system.md` — 动画系统文档
- `docs/services_overview.md` — 服务层概览文档
- `docs/requirements_agent.md` — Requirements Agent 文档
- `AGENTS.md` — AI Agent 项目地图
- `README.md` — 项目概览和快速开始

## 文档维护规则

每次功能更新后，必须同步更新 `docs/` 中的相关文档：

| 变更类型 | 需更新的文档 |
|---------|------------|
| 新增/修改 API 端点 | `docs/api_reference.md` |
| 新增/修改 Persona 构建逻辑 | `docs/persona_forge.md` |
| 新增/修改 RAG 检索逻辑 | `docs/rag.md` |
| 新增/修改 Agent / Pipeline | `docs/workflow.md` |
| 新增/修改架构层 | `docs/structure.md` |
| 新增/修改 Persona 系统 | `docs/Persona.md` |
| 新增/修改 Tool/Skill 系统 | `docs/development.md` + `docs/structure.md` |
| 新增/修改 Animation 系统 | `docs/structure.md` |
| 新增/修改 Material 系统 | `docs/structure.md` + `docs/development.md` |
| 新增/修改 Plugin 系统 | `docs/development.md` + `docs/structure.md` |
| 新增/修改 STT 服务 | `docs/quickstart.md` + `docs/api_reference.md` |
| 依赖/启动方式/配置变更 | `docs/quickstart.md` |
| 开发流程/测试/构建变更 | `docs/development.md` |

规则：**不更新文档的代码变更不应合入主干。**
