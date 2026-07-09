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

## 推荐的 Skill 使用指南

本项目预装了以下 Claude Code skills，在每个开发环节按需使用：

### 开发时

| Skill | 何时使用 | 用法 |
|-------|---------|------|
| `/tdd` | 新增 Agent 节点、前端组件、API 端点时。先写测试再实现 | `/tdd 为 MaterialAgent 的 CLIP 检索添加单元测试` |
| `/diagnose` | Agent 管线断裂、渲染失败、时间轴状态异常、Persona 参数不生效 | `/diagnose EditAgent 输出的时间线时间轴位置不对` |
| `/prototype` | 验证新方案：Persona 对话引导流程、Canvas 渲染方案、动画参数生成 | `/prototype PersonaForge 对话引导的交互流程` |
| `/simplify` | 提交前、PR review 前、重构后。检查代码复用、质量、效率 | `/simplify` |

### 架构与规划时

| Skill | 何时使用 | 用法 |
|-------|---------|------|
| `/improve-codebase-architecture` | 架构边界模糊时（如 Agent 逻辑混入原子能力层），检查模块耦合 | `/improve-codebase-architecture 检查 Agent 编排层和各层的边界` |
| `/to-issues` | 将路线图阶段或功能需求拆解为独立可认领的 issue | `/to-issues 将 Phase 2 的三个插件需求拆成 issue` |

### 上下文切换时

| Skill | 何时使用 | 用法 |
|-------|---------|------|
| `/handoff` | 从前端切换到后端、从一个模块切到另一个、或结束 session 前 | `/handoff` |

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
