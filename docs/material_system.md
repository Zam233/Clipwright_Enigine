# 素材系统

## 概述

素材系统（Material System）负责多源素材搜索、检索与下载，为 Agent 管线提供素材支撑。MaterialAgent 在管线中通过素材系统为每个场景寻找匹配的视觉素材。

## 架构

素材系统采用**插件化多源架构**，每个素材源实现统一接口，注册到系统中后可通过统一的 API 进行跨源搜索。

```
统一搜索 API
    │
    ├── Pexels 素材源插件
    ├── 本地文件素材源
    ├── 自定义素材源插件
    └── ...
```

### 核心组件

| 组件 | 文件 | 说明 |
|------|------|------|
| 素材源接口 | `clipwright/plugins/` | 素材源需实现的基类 |
| 素材搜索 API | `POST /api/material/search` | 跨源素材搜索 |
| 素材源列表 API | `GET /api/material/sources` | 列出可用素材源 |
| 素材筛选工具 | `material_filter` | 按分辨率/方向/时长筛选 |
| 帧验证工具 | `frame_validator` | 验证帧质量（过滤黑帧/全白帧） |
| B-roll 匹配 Skill | `broll_matcher` | 根据文稿语义匹配 B-roll |
| 素材预下载 Skill | `material_downloader` | 批量预下载素材 |

## 素材源

### Pexels 素材源（内置）

通过 Pexels API 搜索免费视频素材。需配置 API Key。

- 插件路径：`plugins/pexels_material/`
- 配置项：API Key、结果数量、安全过滤

### 本地文件素材源

扫描指定目录中的媒体文件，建立本地索引。

- 支持格式：MP4, MOV, AVI, PNG, JPG 等
- 索引方式：文件系统扫描 + 元数据提取

## MaterialAgent

素材搜索在 Agent 管线中由 MaterialAgent 负责：

1. 接收 StructureAgent 输出的场景列表
2. LLM 为每个场景生成具体视觉搜索词（非抽象关键词）
3. 多组关键词分别搜索、去重
4. FrameValidatorTool 帧验证（过滤全黑/全白帧）
5. 按方向/分辨率排序后返回候选列表

## 素材筛选流程

```
场景描述 → LLM 生成搜索词 → 多源并行搜索
    → 去重 → 帧验证 → 排序 → 候选素材列表
```

## 相关文档

- [开发指南](development.md) — 插件开发与 Tool 注册
- [架构总览](structure.md) — MaterialAgent 在管线中的位置
