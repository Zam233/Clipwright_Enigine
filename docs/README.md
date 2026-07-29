# 帧艺 ClipWright 文档

## 架构设计（内容视频编排引擎）

| 文档 | 说明 |
|------|------|
| [架构总览](structure.md) | 五层架构设计：原子能力层 → Agent 编排层 → 类型插件层 → Persona 配置层 → 用户接口层 |
| [Persona 系统](Persona.md) | 四层复合数字人格体系：参数层 / 示例层 / 嵌入层 / 模型层 |
| [Agent 工作流](workflow.md) | 7 Agent LangGraph 管线：Persona 解码 → 结构 → 素材 → 剪辑 → 动画 → 音效 → 质检 |

## 使用指南

| 文档 | 说明 |
|------|------|
| [快速开始](quickstart.md) | 安装、配置、启动、快速验证 |
| [API 参考](api_reference.md) | 全部 REST API 端点说明和示例 |
| [PersonaForge 使用说明](persona_forge.md) | Persona 智能构建器的四种模式和用法 |

## 开发

| 文档 | 说明 |
|------|------|
| [开发指南](development.md) | 技术栈、开发规范、设计约束 |
| [安全部署指南](security.md) | API 令牌认证、CORS、文件白名单与生产部署清单 |

## 新增模块

| 文档 | 说明 |
|------|------|
| [素材系统](material_system.md) | 多源素材搜索与检索系统 |
| [语音与 TTS](voice_tts.md) | 声音克隆、语音合成与配音 |
| [动画系统](animation_system.md) | 动画编目、渲染管线与 MG 动画 |
| [服务概览](services_overview.md) | 全部后端服务层模块说明 |
| [需求分析 Agent](requirements_agent.md) | Requirements Agent 设计与职责 |
