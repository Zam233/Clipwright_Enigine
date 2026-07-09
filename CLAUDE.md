# Clipwright / 帧匠 — 项目上下文

## 项目概要

前后端分离的 AI 辅助视频创作系统。

- **后端**：Python + FastAPI + LangGraph。Persona 驱动的 Agent 引擎，6 个 Agent 构成管线。
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

| 类别 | 工具数 | 对应 Clipwright 模块 |
|------|--------|---------------------|
| 核心视频 (trim/merge/text/audio/resize/chroma key/字幕/水印/稳定) | 32 | 原子能力层 |
| AI (Whisper 转录/场景检测/Demucs 音源分离/升格/调色) | 11 | AudioAgent, QA |
| Hyperframes (HTML → MP4 代码驱动视频) | 8 | AnimationAgent |
| 音频合成 (波形/预设/空间音频) | 7 | AudioAgent |
| 视觉效果 (暗角/色差/扫描线/发光/蒙版) | 8 | AnimationAgent |
| 转场 (Glitch/Pixelate/Morph) | 3 | 类型插件层 |
| 布局与运动 (Grid/PiP/动画文字/进度条) | 6 | 时间轴编辑器 |
| 分析 (场景检测/故事板/质量对比/波形) | 8 | 质检 Agent |
### framely-cli（~15 工具）✅ 已安装

安装：`claude mcp add` — AI 原生视频编辑器 CLI。

同领域架构参考。支持 project init、asset management、silence cutting、captions、transcription、rendering。所有操作原子化可撤销，媒体文件不出本地。

---

---

## 代码规范

- 前端：React 19 + TypeScript + Zustand + Radix UI + Tailwind
- 后端：Python + FastAPI + LangGraph + Pydantic
- 时间线数据格式：前后端统一的 JSON schema（Agent 输出 = 编辑器可编辑）
- Agent 之间通过 LangGraph 节点图传递数据，每个节点独立可测
- Persona 配置以 YAML 存储，支持版本管理

## 关键文件入口

- `structure.md` — 完整架构设计文档
- `Persona.md` — Persona 系统设计文档（四层复合体系）
- `workflow.md` — Agent 工作流设计文档
- `README.md` — 项目概览和快速开始
