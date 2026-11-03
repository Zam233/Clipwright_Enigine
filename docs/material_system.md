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
| 帧验证工具 | `frame_validator` | FFmpeg signalstats/blackdetect 真实实现（过滤黑帧/全白帧/过曝） |
| B-roll 匹配 Skill | `broll_matcher` | 根据文稿语义匹配 B-roll |
| 素材预下载 Skill | `material_downloader` | 批量预下载素材 |

## 素材源

> **2026-10-18 审计结论（逐个核过是否真调用外部 API）**
>
> | 插件 | 判定 | 依据 |
> |---|---|---|
> | `pexels_material` | ✅ 真实 | `https://api.pexels.com`，需 `PEXELS_API_KEY`；带 license/署名 |
> | `pixabay_material` | ✅ 真实 | `https://pixabay.com/api`，需 `PIXABAY_API_KEY`；带 license |
> | `unsplash_material` | ✅ 真实 | `https://api.unsplash.com`，需 `UNSPLASH_ACCESS_KEY`；带署名链 |
> | `gif_sticker` | ✅ 真实 | Giphy API；公开 beta key 需显式 `GIPHY_PUBLIC_BETA_KEY=1` 才启用 |
> | `bgm_library` | ✅ 真实 | Freesound API，需 `FREESOUND_API_KEY` |
> | `local_material` | ✅ 真实（本轮新增） | 扫描真实本地目录，返回真实文件路径，不依赖网络 |
> | ~~`coverr_material`~~ | ❌ **已删除** | `coverr.co/api/videos` 确实返回 200，但响应是 Algolia 结构、只含缩略图与 `playback_id`；实测其 CDN/Mux 链接均拿不到可下载 mp4 → 无法兑现"视频素材"契约 |
> | ~~`my_material_lib`~~ | ❌ **已删除**（换成 `local_material`） | 示例插件：catalog 指向不存在的文件、URL 指向 `cdn.example.com`，启用只会让管线拿到不可用素材 |

### Pexels 素材源（内置）

通过 Pexels API 搜索免费视频素材。需配置 API Key。

- 插件路径：`plugins/pexels_material/`
- 配置项：API Key、结果数量、安全过滤

### 本地素材文件夹（内置，`local_material`）

扫描指定目录中的真实媒体文件，接入多源检索。**只索引、不搬文件**，离线可用。

- 插件路径：`plugins/local_material/`（源实现：`clipwright/material/local_source.py`）
- 配置：插件配置 `material_dir`，或环境变量 `CLIPWRIGHT_LOCAL_MATERIAL_DIR`
- 支持格式：MP4/MOV/MKV/WEBM/AVI/M4V、PNG/JPG/WEBP/BMP/TIF
- 检索：按文件名与子目录名做关键词匹配（英文按词、中文按字），分数 0.5–0.95；
  未给关键词时返回中性 0.5 分（不冒充"相关"）
- 未配置或目录为空 → **零贡献并写明原因**，不假装有素材
- 限制：不提取时长/分辨率（拿不到就留空，不编造）；不生成缩略图

## MaterialAgent

素材搜索在 Agent 管线中由 MaterialAgent 负责：

1. 接收 StructureAgent 输出的场景列表
2. LLM 为每个场景生成具体视觉搜索词（非抽象关键词）
3. 多组关键词分别搜索、去重
4. FrameValidatorTool 帧验证（过滤全黑/全白帧）
5. 按方向/分辨率排序后返回候选列表

### 视觉 LLM 校验（默认开启）

- **enable_visual_llm 默认**：只要任一 LLM 已配置（`llm_api_key` / `vision_llm_api_key` / 对应 `base_url`，含 Ollama）即自动开启；显式 `plugin_config` 配置优先。
- 视觉校验接收的是**场景级** `narration_text` + `material_intent`，而非完整剧本：
  - `narration_text` = 场景 `voiceover_script` 或场景描述（绝不使用 `extra_params.script_text` 全文，避免旁白泄入校验上下文）
  - `material_intent` = `visual_description.material_content` + `material_preference`
- 视觉 LLM 失败时 graceful fallback（评分回落 0.5），不阻断场景处理。

### 文本相关性兜底评分

视觉校验关闭或全部为默认 0.5 时，排序键以本地零网络 **Containment（包含系数）** 兜底：

```
text_score = |A ∩ B| / min(|A|, |B|)
A = narration_text + material_intent + 场景关键词
B = 素材 title + tags
```

分词：CJK 按单字、Latin 按整词；任一侧为空返回 0，绝不崩溃。真实校验分（frame_validator / 视觉）仍优先于兜底分，兜底分仅参与排序。

### 重选循环（低匹配补充搜索）

首轮评分低于阈值时，MaterialAgent 至多做**一次**有界的补充搜索（`_RESELECT_THRESHOLD` 默认 0.45，可用 `plugin_config.reselect_threshold` 覆盖）：

- 查询词由本地提取（`narration_text + material_intent`，复用兜底分词器），零额外 LLM 往返
- 每个场景最多 1 次额外 `_search_with_cache(query, top_k=5)`，与首轮结果去重后以同一校验路径重新评分排序
- 无候选 / 首轮已达标 / 补充轮失败或未改善时跳过或保留原最佳，并写入 warning 笔记，绝不抛异常
- 结果 dict 携带 `reselect: {triggered, query, improved_score}` 元数据，便于追踪重选效果

## 素材筛选流程

```
场景描述 → LLM 生成搜索词 → 多源并行搜索
    → 去重 → 帧验证 → 排序 → 候选素材列表
```

## 相关文档

- [开发指南](development.md) — 插件开发与 Tool 注册
- [架构总览](structure.md) — MaterialAgent 在管线中的位置
