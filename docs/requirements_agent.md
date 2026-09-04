# Requirements Agent（需求分析 Agent）

## 概述

Requirements Agent 是面向"需求分析"职责的 Agent 实现。F4 对齐注记（2026-09）：**生产链路并未实例化本 Agent**——需求分析实际由 `services/requirements_service.py`（对话式会话 + 自持提示词）承担，管线入口为 StructureAgent。本文档描述其设计意图与能力边界；相关端点见下方真实 API 表。

## 管线位置

```
RequirementsAgent → StructureAgent → MaterialAgent → EditAgent → AnimationAgent → AudioAgent → QualityAgent
```

Requirements Agent 在整个管线中处于最前端，其输出直接影响后续所有 Agent 的决策。

## 核心功能

### 输入
- 用户选题（选题描述）
- 文稿内容（可选）
- 参考素材（可选）
- Persona 配置

### 输出
- **creative_brief**：结构化创作方案，包含主题、目标受众、风格方向、关键信息点
- **animation_intents**：动画需求意图列表，标记需要特殊动画处理的场景

### animation_intents

当用户内容涉及以下元素时，RequirementsAgent 自动识别并标记：

| 类型 | 说明 |
|------|------|
| `type: "mg"` | 动态图形（数据图表、进度条、对比图等） |
| `type: "text"` | 文字入场动画 |
| `type: "logic"` | 逻辑关系图解 |

animation_intents 通过 `requirements_service` → `StructureAgent.extra_params` 注入 LLM prompt，指导后续结构分析和动画编排。

## 工作流程

```
用户选题 → RequirementsAgent 分析
    → 提取创作约束（时长/风格/受众）
    → 识别动画需求（animation_intents）
    → 输出 creative_brief → StructureAgent
```

## RequirementsService

`RequirementsService` 是 RequirementsAgent 的后端支持服务，提供：
- 用户输入解析
- 创作方案生成
- 动画需求识别
- 约束提取与验证

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/requirements/init` | POST | 初始化需求会话 |
| `/api/requirements/chat` / `chat/stream` | POST | 对话生成/修订创作方案（SSE 流式） |
| `/api/requirements/plan` | GET | 获取规划书 |
| `/api/requirements/proceed` | POST | 确认规划书 → 启动管线 |
| `/api/requirements/upload` / `session` | POST/GET | 参考文件上传 / 会话状态 |

## 相关文档

- [Agent 工作流](workflow.md) — 完整 Agent 管线流程
- [架构总览](structure.md) — RequirementsAgent 在五层架构中的位置
- [API 参考](api_reference.md) — 完整 API 端点说明
