# 服务层概览

## 概述

服务层（Services Layer）位于 API 路由与 Agent/Tool 之间，提供业务逻辑编排、数据持久化、AI 服务集成和可观测性能力。共约 29 个服务模块。

## 服务分类

### Pipeline 执行

| 服务文件 | 说明 |
|---------|------|
| `pipeline.py` | 管线 v1（固定序列执行） |
| `pipeline_v2.py` | 管线 v2（DAG 并行 + 熔断 + 自愈） |
| `agent_bus.py` | Agent 上下文总线（消息发布/订阅、路由决策） |

### 渲染

| 服务文件 | 说明 |
|---------|------|
| `render.py` | 渲染引擎（多轨/缓存/降级/成片时长校验/切点复核 hook）；转场漂移校正为纯函数并走整数 tick（`transition_shift_ticks`/`shift_ticks_for`/`corrected_start_sec`），视觉 overlay 吸附帧边界、音频不吸附（见 [整数时间基座](timebase.md)）；`RenderResult` 暴露复核派工字段 `review_findings`/`visual_score`/`review_failed`；MG 渲染先试 PIL 快路径（`CLIPWRIGHT_MG_FASTPATH`，默认开）再回退 Chrome |
| `timeline_view.py` | 切点复核图（V4/V7）：切点前后 ±0.35s 三帧横向拼图，供人或视觉模型判定跳变/爆闪/字幕遮挡；切点用整数 tick 判重，可传 `frame_rate` 按帧吸附；另有**全片总览联络表**（12 帧等间隔 + 时间戳，`overview_times`/`build_overview_sheet`） |
| `animation/mg_fastpath.py` | MG PIL 快路径（V3）：白名单判定 + 逐帧绘制 + 原始帧管道编码成带 alpha MOV，模板覆盖率 11/11，实测 3.4–11.8× 提速；任何不支持的特征一律回退 Chrome |
| `post_review.py` | 渲染后复核的**问题派工**（V4 残余）：按描述归类 animation/edit/audio/material/other、投票选主重做对象、视觉评分门槛判定、转 `QualityIssue` 供质检复用既有 redo 映射 |
| `transcript_pack.py` | 短语级转写打包（V6）：逐词/逐句 ASR → 短语视图 `[start-end] S1 文本`（断句按停顿 ≥0.5s/换人/超长），静音区间单独保留作为切点候选；实测 600 词输入压缩 **93.2%** |
| `content_index.py` | 素材内容索引（R1）：ffmpeg 场景切分 → 镜头区间（碎片合并/上限）→ 抽帧 → 廉价统计（亮度/对比度/锐度/饱和度/帧间差/黑场白场）→ 内容寻址落盘 `<素材库>/content_index/<sha256>.json` + 可选 Mongo 镜像（跨项目检索）。缓存键 = `sha256 + 索引版本 + 模型指纹`；**L0 全程零 ML 零云端** |
| `remote_render.py` | 远程渲染客户端（worker 协议：资产上传 → 提交 job → 轮询 → 下载） |

### AI 服务

| 服务文件 | 说明 |
|---------|------|
| `llm.py` | LLM 服务（Anthropic/OpenAI/Ollama 适配） |
| `stt.py` | 语音转文字（Whisper） |
| `voice.py` | 声音克隆与 TTS |

### 数据持久化

| 服务文件 | 说明 |
|---------|------|
| `mongodb_service.py` | MongoDB 连接与操作封装 |
| `project_manager.py` | 项目 CRUD 与文件管理 |
| `project_memory.py` | 项目决策记忆 `memory.md` 四段式（V8）：解析/序列化/追加/摘要，纯 Markdown、追加式、人工可编辑 |

### 领域服务

| 服务文件 | 说明 |
|---------|------|
| `requirements_service.py` | 需求分析与创作方案生成 |
| `style_interpreter_service.py` | Persona 视觉参数 → 动画风格映射 |
| `chat_forge_service.py` | 对话式内容生成 |

### 可观测性

| 服务文件 | 说明 |
|---------|------|
| `trace.py` | 执行追踪（TTL/内存保护） |
| `tracing_service.py` | SpanTracer（⚠ 对账注记：内存实现 + cleanup 清空，MongoDB 持久化未实现，规划中） |
| `log_stream.py` | 实时日志流 |
| `llm_tracker.py` | LLM token 用量追踪 |

### 媒体处理

| 服务文件 | 说明 |
|---------|------|
| `waveform_service.py` | 音频波形可视化数据生成 |
| `vision_service.py` | 视觉分析服务 |
| `edl_service.py` | EDL 导入导出 |
| `fontconfig_service.py` | 字体管理与配置 |

### 工具与服务

| 服务文件 | 说明 |
|---------|------|
| `task_queue.py` | 并发任务队列（信号量+超时） |
| `proxy.py` | 网络代理配置 |
| `async_util.py` | 异步工具函数 |
| `material_preprocessor.py` | 素材预处理（转码/验证） |
| `predictor.py` | 时间预测 |

## 核心交互

```
API 路由 → Service → Agent/Tool
                ↓
          MongoDB / LLM / FFmpeg
```

## 相关文档

- [架构总览](structure.md) — 五层架构中的服务层位置
- [开发指南](development.md) — 新增服务模块
- [API 参考](api_reference.md) — API 端点与服务的对应关系
