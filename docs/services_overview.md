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
| `content_vision.py` | 镜头画面标注（R2）：CLIP 零样本（词表可配置）+ 本地分类器兜底 + 云端多模态按需（默认关，带每次索引的镜头数闸门）；OpenCV 人脸/行人 → 景别与 `has_face`；过曝/欠曝/边缘能量等质量维度。**CLIP 文本只编码一次、图片整批前向**；检测前统一降采样（HOG 在小图上会崩进程，已加防护） |
| `content_audio.py` | 音频维度（R3）：短时能量 → `silence`/`loud`/`onset` 事件（分位数自适应阈值，恒定电平不出事件）；转写片段按**时间交集**对齐到镜头并抽关键词；ffmpeg 抽音轨 + 标准库 `wave` 读取（不引入 librosa）。事件时间精度实测 0.020s |
| `event_lexicon.py` | 领域事件词表（R3）：`generic`/`sports`/`food`/`tech_review`/`knowledge` 五套词表 + 中文事件名；词表命中优先级最高（长词优先），支持设置项 JSON 追加 |
| `content_query.py` | 素材内容检索（R4）：`route_query()` 把自然语言说法路由成结构化条件/排序（黑场/有人脸/特写/掌声/最长…），混合打分（结构化硬过滤 → 标签+关键词 + 向量 → 可选 rerank），清单/排序式问题直接给关联清单；每条命中带 `ref` 与"为什么命中"。**向量只用于语义召回，结构化条件当硬过滤** |
| `edit_intent.py` | 自然语言剪辑意图（R5）：`FIELD_SPECS`（22 个真实时间线字段 + 类型/值域钳制）与 `OP_SPECS`（keep/remove/reorder/trim/set/add/transition）成为**唯一事实来源**（`requirements_service._ADJUST_FIELDS` 直接引用它）；`validate_intent()` 拒绝未知 op/字段/取值、钳制越界值；`parse_intent_rules()` 是零 token 的确定性解析器（把字句/后置动词/量词插入都覆盖），`parse_intent_llm()` 走受 schema 约束的结构化输出；`describe_intent()` 供意图确认卡 |
| `intent_resolver.py` | 意图落位与澄清（R6）：把 R5 的抽象 target（`query/filter/ref/index`）落到**具体时间线 clip**——按 `asset:{sha}#s{n}` 回连、内容条件复用 R4 检索层；`SessionRefState` 保留最近 5 个短名单以消解指代（"刚才那几段"），无短名单/零命中/候选 >12 时**反问并给选项**而非猜；`preview_intent()` **只读**（不写时间线），是"执行前确认卡"的后端 |
| `intent_apply.py` | 意图 → 时间线补丁（R7）：`plan_patch()` 把 `(intent, resolution)` 编译成具体补丁（remove/keep/reorder/trim/set/add/transition/fit）；`validate_patch()` 是 **dry-run**（只读、内置"输入未被改动"自检），产出"片段数与总时长的前后值 + 逐条预览 + 问题清单"；`apply_patch()` **有硬错误就一条都不执行**；`_ripple_track()` 的收拢规则是"只抽掉被删/被裁的时长，保留幸存片段之间原有的留白，且不制造新重叠"；`apply_answers()` 把确认卡上的选择回填进意图 |
| `scene_grounding.py` | 方案接地（R8）：把规划书里的每个场景钉到**真实镜头**（`asset:{sha}#s{n}`）——引用由索引检索**确定性**算出而非 LLM 编；命中不足写 `material_gap` 而**不塞最接近的凑数**（凑数引用会让下游以为有素材）；结构化条件零命中时**退化为文本匹配并标注 `degraded`**（因 R2 对看不出景别的镜头如实写 `unknown`，硬过滤会滤掉真素材）；关键词/标签全不命中时再走**画面语义兜底**（R9）；`validate_material_refs()` 是"引用不存在的素材占比 = 0"这条不变量的实现 |
| `content_semantic.py` | 画面语义检索（R9）：CLIP 图像/文本向量（`ShotRecord.embeddings["clip"]`，写进索引随索引落盘，检索时只做点积）；**中文提示词改写**（60 余条词表 + 多模板集成——CLIP 文本塔不认中文，实测改写带来 +33 个百分点）；`semantic_scores()`/`rank_shots()` 供检索层与接地层调用；CLIP 不可用或向量维度不匹配时**如实告警并降级**，绝不返回假分 |
| `content_describe.py` | 本地画面描述合成（真实素材补测）：用**已测到的字段**（明暗/景别/运镜/质量/标签/语音/音频事件）拼一句可检索的中文描述。真实素材上 `visual.description` 原本覆盖率 **0**（只有云端多模态会给，而它默认关闭），导致检索可匹配文本极少、方案接地 0 命中；本模块**零模型调用**补上描述，`evidence` 写明 `composed:stats+clip+audio`、置信度 0.5，不与模型级理解混为一谈 |
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
