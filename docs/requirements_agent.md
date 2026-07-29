# Requirements Agent（需求分析 Agent）

## 概述

Requirements Agent 是 Agent 管线中的第一个节点，负责接收用户选题，分析创作需求，提取约束和偏好，生成结构化的创作方案。它是 7-Agent 管线中最新加入的成员。

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
| `/api/requirements/analyze` | POST | 分析用户需求，生成创作方案 |
| `/api/requirements/extract` | POST | 提取创作约束 |

## 相关文档

- [Agent 工作流](workflow.md) — 完整 Agent 管线流程
- [架构总览](structure.md) — RequirementsAgent 在五层架构中的位置
- [API 参考](api_reference.md) — 完整 API 端点说明
