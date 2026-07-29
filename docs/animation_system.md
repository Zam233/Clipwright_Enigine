# 动画系统

## 概述

动画系统（Animation System）负责管理、编目和渲染各类视频动画，包括文字动画、逻辑动画、MG 动画和转场动画。AnimationAgent 在管线中根据 Persona 的视觉参数为时间线编排动画序列。

## 架构

```
动画注册表 (registry.py)
    │
    ├── 动画编目 (catalog.py) — 管理动画定义与分类
    │
    ├── 动画渲染器 (renderer.py) — 基于 FFmpeg drawtext 的文字动画渲染
    ├── Hyperframes 渲染器 (hyperframes_renderer.py) — HTML/CSS → MP4
    ├── MG 渲染器 (mg_renderer.py) — MG 动画 JSON → HTML/CSS → MP4
    └── SVG 图解渲染器 (diagram_svg.py) — 24+ 图表类型的 SVG 渲染
```

## 动画编目

动画编目（Catalog）将所有可用动画按类型分类，供 AnimationAgent 调用。

| 分类 | 数量（约） | 说明 |
|------|-----------|------|
| 文字动画 | 18 | 入场/保持/出场的文字动画效果 |
| 逻辑动画 | 10 | 箭头、对比、流程等逻辑关系图解 |
| 过渡动画 | 9 | 场景间转场效果 |
| MG 动画 | 动态 | LLM 动态生成或模板匹配 |

## 动画类型

### 文字动画 (Text Animation)

- 在文字轨创建 text clip
- 生成完整 keyframes：入场 → 保持 → 出场
- 基于 FFmpeg drawtext filter 渲染
- 支持字体、大小、颜色、动画类型配置

### 逻辑动画 (Logic Animation)

- 在动画轨创建独立 clip
- 展示箭头、对比、流程等逻辑关系
- 使用 Hyperframes SVG / drawtext 降级方案

### MG 动画

**静态模板**：ID 以 `mg_` 开头 → MGRenderer 加载预定义 JSON 模板 → HTML/CSS 动画 → Hyperframes 渲染

**LLM 动态生成**：LLM 识别 animation_intents 中的 `mg_dynamic` 标记 → `llm_mg` 插件生成完整 MG JSON → MGRenderer → Hyperframes
- 成功生成时 `generated_mg_count` 递增
- 失败时自动降级：LLM → 模板匹配 → drawtext 纯文字

### 过渡动画 (Transition Animation)

- `[过渡动画]xxx` 标记 → 设置 clip.transition_in 字段
- 通过 FFmpeg xfade filter 渲染
- 支持 Glitch、Pixelate、Morph 等转场效果

## 动画编排流程

AnimationAgent 编排流程：

1. 接收 EditAgent 的粗剪时间线
2. 接收 Persona 视觉参数（visual_config）
3. 为每个场景匹配动画类型
4. 对文字内容应用文字动画
5. 对逻辑关系应用逻辑动画或 MG 动画
6. 对场景切换应用过渡动画
7. 输出编排好的动画序列

## 降级策略

当动画渲染失败时，系统自动降级：

```
LLM 动态 MG → 预定义模板 MG → drawtext 纯文字
```

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/animation/list` | GET | 列出所有动画定义 |
| `/api/animation/get/{id}` | GET | 查看动画详情 |

## 添加新动画

新增动画类型需在 `catalog.py` 中注册动画定义，并在对应渲染器中实现渲染逻辑。

## 相关文档

- [架构总览](structure.md) — AnimationAgent 在管线中的位置
- [Agent 工作流](workflow.md) — 动画编排流程详解
- [开发指南](development.md) — 新增动画类型
