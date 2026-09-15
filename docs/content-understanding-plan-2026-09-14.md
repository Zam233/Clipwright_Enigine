# 素材内容理解与自然语言剪辑落位 · 开发计划（2026-09-14）

> 目标一句话：**让 Agent 知道自己在剪什么，让用户说人话就能达到剪辑目的。**
> 本计划不含第三方非 OSI 代码，全部能力建立在已装依赖（torch/transformers/cv2/onnxruntime/easyocr/sentence_transformers）+ 本仓库既有件之上。

---

## 0. 可验收的目标定义

把"更懂内容"这种模糊目标拆成三条**可测**的目标：

| 编号 | 目标 | 验收方式 |
|------|------|----------|
| **G1** | Agent 知道素材里有什么 | 给一段真实素材，问 30 条具体问题（"有没有人在说话""特写在哪几秒""有没有黑场"），必须给出**区间 + 证据**，不是文件名 |
| **G2** | 用户用 NL 达成目的 | 50 条用户原话 → 结构化意图 → 落到具体区间 → 时间线真的变了，且能解释"为什么这么改" |
| **G3** | 不幻觉、不失控 | 所有 NL 落地都过 schema + 白名单 + dry-run；说不清就问，不猜 |

反面目标（明确不做）：不做"全片逐帧语义索引"（成本不可接受）、不做通用视频问答（没有明确用途）、不引入需要 GPU 常驻的重模型。

---

## 1. 现状盘点（代码级，2026-09-14）

### 已有件（可直接复用）
| 能力 | 位置 | 说明 |
|------|------|------|
| 素材存储与去重 | `services/asset_manager.py` | 磁盘存储 + sha256 去重 → 索引缓存键的天然来源 |
| 元数据抽取 | `services/indexer.py` | **只有 ffprobe 元数据 + 文件名标签**，无画面内容 |
| 抽帧 | `tool/frame_extractor.py` | 均匀采样（避开首尾 10%），可复用 |
| 单图理解 | `services/vision.py` | 标签/描述/场景类型；本地 ViT（HF 缓存已有 `vit-base-patch16-224`）+ 可选 LLM；带 TTL 缓存 |
| 场景切点 | `tool/vision.py::SceneDetectTool` | `select='gt(scene,θ)'` + showinfo 解析时间戳 |
| 语音 | `services/stt.py` + `tool/transcribe.py` | whisper/API 后端 + **V6 短语级打包**（50,765→3,450 字符，93.2%） |
| 质量校验 | `tool/frame_validator.py` | signalstats/blackdetect：空镜头/全白 |
| 复核图 | `services/timeline_view.py`（V4/V7） | 切点三帧对照条 + 全片联络表（**现成的"喂给视觉模型的帧网格"**） |
| 检索栈 | `rag/`（embedder `bge-small-zh-v1.5` / reranker `bge-reranker-v2-m3` / vector_store / retriever） | 目前只服务 Persona 知识库 |
| 需求对话 | `services/requirements_service.py` | 对话 → `CreativeBrief` → `ProductionPlan(scenes: list[dict])` |
| **NL→时间线雏形** | `services/requirements_service.py:1134` | LLM 产出 `{ops:[{clip_id, field, value}]}`，**限定在用户已选中的 clip + `_ADJUST_FIELDS` 白名单**内 |
| 项目记忆 | `services/project_memory.py`（V8） | 决策理由的落点 |
| 成本控制 | `services/budget.py` | 月度 token 预算（Mongo 聚合 + 缓存） |
| 后台任务 | `services/task_queue.py`、SSE 约定 | 索引生成应跑在这里 |

### 缺口（本次要补的）
1. **没有"镜头级内容记录"**：素材的视觉内容看完就丢，`MaterialAsset` 只有 title/tags/duration/metadata。
2. **没有素材内容检索**：`semantic_match` 仍是返回 0.5 的占位（`tool/vision.py:74`），rag/ 的检索栈没接素材。
3. **NL 只能改"已选中 clip 的数值字段"**：不能表达"只保留有人物的镜头"这类**内容条件**，也不能新增/删除/重排。
4. **方案不接地**：`ProductionPlan.scenes` 是自由文本，没有任何字段指向"具体哪段素材的哪几秒"。

### 依赖现状（本机实测）
`torch 2.12.0+cpu`、`transformers 5.13.0`、`cv2 4.13.0`、`numpy 2.4.6`、`onnxruntime 1.26.0`、`sentence_transformers 5.6.0`、`easyocr` 可用；HF 缓存已有 `vit-base-patch16-224`、`bge-small-zh-v1.5`、`bge-reranker-v2-m3`。
**缺失**：CLIP/SigLIP 权重、`librosa`/`soundfile`（音频事件若要做需自写 numpy 或新增依赖）、`ultralytics`（YOLO 若要做需新增）。

---

## 2. 架构：四层 + 三个契约

```
用户一句话
   │
   ▼  (C) 意图层       NL → EditIntent（结构化：ops + 引用 + 约束 + 目标）
   │                   ↕ 澄清回路（多候选/指代不明 → 反问 2-3 个选项）
   ▼  (C) 落位层       Intent → Resolver → 具体区间（ShotRef）+ 证据
   │                   ↖──────────────┐
   ▼  (D) 执行层       ops → Timeline patch（白名单字段）→ dry-run 校验
   │                                 │
   ▼  (D) 校验层       渲染前复核图 / QualityAgent / 失败回退          │
   │                                 │
   └── (B) 查询层  ◀───────┐         │
          内容问答/检索     │         │
   ┌── (A) 索引层 ──────────┘         │
      镜头切分 → 画面/语音/质量/镜头语言 → ShotRecord + embedding
```

### 契约 1：`ShotRef`（唯一寻址方式）
```
asset:{sha256[:12]}#s{shot_index}      # 稳定、可跨会话、可写进日志与 EDL
{asset_id, sha256, t0, t1, fps}        # 解析后的可用信息
```
**为什么用内容寻址而非文件路径**：素材会被移动/重命名/重复导入（asset_manager 本就按 sha256 去重），路径寻址会在会话之间失效。

### 契约 2：`EditIntent`（NL 的落点）
```jsonc
{
  "goal": "把这段素材剪成 60 秒的爆点集锦",
  "ops": [
    {"op": "keep",    "target": {"query": "有人物的镜头"}, "limit": null},
    {"op": "remove",  "target": {"kind": "black_frame"}},
    {"op": "reorder", "target": {"by": "excitement", "desc": true}},
    {"op": "set",     "target": {"ref": "clip:cl_07"}, "field": "speed", "value": 1.5}
  ],
  "constraints": {"total_duration_sec": 60, "platform": "vertical", "no_reuse": true},
  "unresolved": []      // 解析不了的引用必须写在这里 → 触发反问
}
```
- **op 词表固定**，未知 op 当场拒绝（不是让模型自由发挥）。
- `field` 复用 `_ADJUST_FIELDS` 的"类型 + 值域钳制"机制。
- `unresolved` 非空 → 必须反问，**禁止静默猜测**。

### 契约 3：`content_query` 的返回（Agent 读的东西）
```jsonc
{"summary": "42 个镜头：8 特写 / 3 含人物 / 语音提到'价格'2 次 / 1 段黑场",
 "hits": [{"ref": "asset:ab12…#s17", "t0": 12.4, "t1": 14.0,
           "why": "描述='人物特写，手部动作'；关键词命中'价格'", "score": 0.83}],
 "total": 42}
```
**摘要必须短**（几十行），这是 V6 短语打包（93.2% 压缩）已验证的原则：给 LLM 的是压缩结构，不是原始数据。

### 七个设计决策（先定死，避免中途摇摆）
1. **索引粒度 = 镜头**（语音短语对齐到镜头），不做帧级全索引。
2. **成本分层**：L0 切分（免费）→ L1 本地小模型（CPU：ViT/CLIP/启发式）→ L2 ASR（1× 时长）→ L3 多模态 LLM **只对"被选中的镜头"或按预算抽样**。
3. **缓存键** = `sha256 + index_version + model_fingerprint` → 增量、失效、复查天然成立。
4. **索引生成走后台任务 + SSE 进度**，可取消（`task_queue` / `trace` 既有约定）。
5. **NL 只落到真实字段**，字段词表与值域钳制集中在 `edit_intent.py` 一处（供 API/Agent/前端共用）。
6. **解析优先级**：显式选择/时间码 > 会话指代（最近候选集）> 检索（关键词 + 向量 + rerank）> 结构条件；平票 → 反问。
7. **每次落位都要留证据**（用了哪个 shot、为什么），写进 `project_memory` 与 trace，支撑"再改一次"。

---

## 3. 轮次计划（10 轮 · 每轮 = 实现 + 测试 + 文档 + 提交不 push）

| 轮 | 阶段 | 交付物（主要文件） | 测试 / 验证 | 依赖 |
|----|------|--------------------|-------------|------|
| **R1** | P0-1 索引底座 | `schema/content.py`（ShotRecord/ContentIndex）、`services/content_index.py`（切分/缓存/增量/落盘）、`scripts/verify_content_index.py` | 单元：切分边界、缓存命中、sha 变化失效；脚本：合成视频端到端 | scene_detect、asset_manager |
| **R2** | P0-2 画面维度 | `content_index` 接 `VisionService`（本地 ViT/CLIP + 启发式质量/镜头语言：运动幅度/亮度/清晰度/人脸占比→景别粗分类） | 用 20 段人工标注算镜头切分 F1 与景别分类准确率 | R1、cv2、HF vit |
| **R3** | P0-3 语音与事件 | ASR `phrases` → shot 对齐；`tool/event_spot.py`（**可插拔领域词表**：sports/food/unboxing/通用） | 词表命中/误报率；对齐误差 ≤ 0.3s；`transcript_pack` 静音区间并入索引 | R1、V6、stt |
| **R4** | P0-4 查询与接地（**G1 达成点**） | `tool/content_query.py`（结构化过滤 + 关键词 + 向量 + rerank）；`_material_context_pack()` 摘要注入 Requirements/Structure/Edit | 30 条素材问答，区间命中 ≥ 80%；Agent 上下文长度与延迟实测 | R1–R3、rag/ |
| **R5** | P1-1 意图层 | `services/edit_intent.py`（schema + op/field 词表 + 值域 + 引用类型）、LLM 解析（few-shot，温度 0）、`scripts/verify_edit_intent.py` | 50 条用户原话夹具 → 解析正确率 ≥ 85%；**幻觉 op 拒绝率 100%** | R4 |
| **R6** | P1-2 落位与澄清 | `services/intent_resolver.py`（引用解析/会话指代表/多候选反问）；`api/requirements` 新端点；前端"意图确认卡" | 20 条指代解析 ≥ 85%；歧义时 100% 反问；确认卡可编辑后重解析 | R5 |
| **R7** | P1-3 执行与校验（**G2 达成点**） | ops → timeline patch（扩 `_ADJUST_FIELDS` 为 ops 级：adjust/add/remove/reorder/split/set_transition）；dry-run 校验器（时长/重叠/轨道/引用有效性） | 每条 op 的单元测试 + 冲突用例；失败必须给人话原因；端到端 3 条"一句话目标"跑通 | R6 |
| **R8** | P2 方案接地 | `ProductionPlan.raw_scenes` 带 `material_refs`；RequirementsAgent 先读索引摘要；StructureAgent 用 refs 选段 | 方案里"引用不存在的素材"占比 = 0；结构→时间线一致性 | R4 |
| **R9** | P3 语义检索真实现 | 补 `semantic_match`（CLIP/SigLIP，替换 `tool/vision.py:74` 的假分）；素材向量索引；`scripts/verify_semantic_search.py` | 检索召回率 @10 ≥ 70%（30 条查询）；与关键词基线对比 | R2、HF 权重 |
| **R10** | 评测与收尾 | 标注基准集（用户原话 → 期望区间/时间线）、指标脚本、真实素材 demo、文档（`docs/` 全套） | 全指标汇总表；后端 pytest 全绿；三仓文档同步 | R1–R9 |

**里程碑对应**：
- **M1 = R4 完成** → "Agent 知道自己在剪什么"可验证（G1）。
- **M2 = R7 完成** → "用户用 NL 达到目的"闭环可验证（G2、G3）。
- **M3 = R10** → 有量化基线，后续优化有据可依。

---

## 4. 验收指标（量化，不是感觉）

| 指标 | 目标 | 怎么测 |
|------|------|--------|
| P0 索引成本（10 分钟 1080p） | ≤ 3 分钟 CPU、**0 次云端调用** | 计时 + LLM 调用计数 |
| 镜头切分质量 | F1 ≥ 0.90 | 20 段人工标注 |
| 素材问答区间命中 | ≥ 80% | 30 条问答集 |
| NL 意图解析正确率 | ≥ 85% | 50 条原话夹具 |
| **幻觉操作率** | **0**（schema 拒绝） | 夹具中故意构造不存在的 op/字段 |
| 指代解析正确率 | ≥ 85%，歧义 100% 反问 | 20 条指代用例 |
| 端到端"目的达成" | 3 个项目，主观 ≥ 4/5 且改动可解释 | 用户评分 + 理由链完整 |
| 回归 | 后端 pytest 全绿，新脚本可重跑 | CI 命令 |

---

## 5. 风险与对策

| 风险 | 对策 |
|------|------|
| 索引成本随素材时长线性爆炸 | 分层（L0/L1 优先）+ `budget.py` 上限 + L3 只对"用到的镜头" |
| 多模态描述不稳定/幻觉 | 结构化 JSON + 温度 0 + 证据字段 + 抽样人工复核；本地小模型优先 |
| 中文短查询语义检索效果差 | 复用 `bge-small-zh-v1.5` + `bge-reranker-v2-m3`；CLIP 只负责视觉召回，两者混合 |
| NL 说出系统不支持的操作 | op/field 词表 + 值域钳制 + dry-run；拒绝时给"我能做什么"的替代建议 |
| 时间线冲突（重叠/轨道非法） | dry-run 校验器 + 明确错误信息，绝不半应用 |
| 权重/许可风险 | 只用 HF 上的宽松许可权重（CLIP/SigLIP），不引入非 OSI 运行时；沿用现有许可审计流程 |
| 用户说不清 | 反问机制（给 2-3 个具体选项），把"猜"变成"确认" |

---

## 6. 拍板结果（2026-09-14，已确认）

| # | 事项 | 决定 | 落点 |
|---|------|------|------|
| 1 | 索引存储 | **本地内容寻址 + 可选 Mongo 镜像**（默认开镜像） | `<素材库>/content_index/<sha256>.json`；Mongo 集合 `content_index`（`sha256` / `text` 索引），`find_indexes()` 支持跨项目"我所有素材里找 X" |
| 2 | 素材帧送云端多模态 | **允许** | 设置项 `content_index_visual_llm`（默认关，开启后产生视觉 API 成本）；只对**候选镜头**调用，且走 `budget.py` 月度预算 |
| 3 | 下载 CLIP/SigLIP 权重 | **下载**（R9 使用） | HF 宽松许可权重；`vision_provider=transformers` 路径 |
| 4 | 评测素材来源 | **无真实素材** → 合成 + 公开素材建基准集 | `scripts/` 下用 ffmpeg 合成确定性素材做回归；语义检索基准另建 |

## 7. 执行进度

| 轮 | 状态 | 交付物 | 验证 |
|----|------|--------|------|
| R1 | ✅ 完成（2026-09-14） | `schema/content.py`、`services/content_index.py`、`scripts/verify_content_index.py`、`tests/clipwright/test_content_index.py`（83 条）、7 个 Settings 字段 + 元数据 | 脚本 PASS：合成 6 段硬切视频 → 5 镜头；黑场/白场/噪声运动全部识别；缓存命中（`build_ms` 不变）；换 sha 与换模型指纹均正确失效；`pytest` 全绿 |
| R2 | ✅ 完成（2026-09-14） | `services/content_vision.py`（画面标注 + 镜头语言 + 质量）、`VisionService.analyze_image(provider=…)` 按调用覆盖后端、`INDEX_VERSION=2`、5 个新 Settings、`scripts/verify_shot_detection.py`、`tests/clipwright/test_content_vision.py`（39 条） | 切分 F1 **easy 1.000 / texture 1.000**（目标 ≥0.90）；`visual_tags` 覆盖率 **0.00 → 1.00**；CLIP 零样本与本地分类器双路可用；OpenCV HOG 崩溃防护有回归用例 |
| R3 | ✅ 完成（2026-09-14） | `services/content_audio.py`（音频事件 + 对齐 + 关键词）、`services/event_lexicon.py`（5 个领域词表）、`tool/event_spot.py`（已注册，工具总数 49）、6 个新 Settings、`scripts/verify_audio_events.py`、`tests/clipwright/test_content_audio.py`（57 条） | 音频事件时间误差 **0.020s**（门槛 ≤0.3s）；跨镜头转写按交集切分正确；进球事件落在真值 ±0.3s 且带证据句 |
| R4 | ✅ 完成（2026-09-14，**M1 达成**） | `services/content_query.py`（混合检索 + 自然语言路由）、`tool/content_query.py`（已注册，工具总数 50）、RequirementsAgent 上下文注入、1 个新 Settings、`scripts/verify_content_query.py`、`tests/clipwright/test_content_query.py`（78 条） | **32/32 = 100%** 区间命中（门槛 ≥80%）：A 组检索层 22/22、B 组端到端 10/10 |
| R5–R10 | 待开工 | 见 §3 | — |

**R1 的实现边界（如实）**：
- 只做 **L0**：ffmpeg 场景切分 + 抽帧 + 廉价统计（亮度/对比度/锐度/饱和度/帧间差/黑场白场）。
  **没有任何 ML 或云端调用**——`visual.description` / `visual.tags` / `speech` / `embeddings`
  在 R1 后仍为空（`coverage()` 会如实显示 0），这些是 R2/R3/R9 的活。
- `camera_motion` 是**帧间差阈值启发式**（`static/low/medium/high`），置信度标 0.4；
  单帧或黑/白场时给 `unknown` 而不假装"静止"。
- `visual.shot_size`（景别）刻意留 `unknown`：R1 没有人体/人脸估计，编一个值就是假数据。
- Mongo 镜像是 best-effort：未连接/写入失败只记日志，**不影响本地索引**。
- 索引文件里的 `keyframes` 默认保留（R2 要用）；`keep_frames=False` 时清理抽帧目录。
- 尚未接线：素材导入时自动建索引、`task_queue` 后台任务与 SSE 进度（都属 R4 的"Agent 接线"）。
  当前入口只有 `build_index()` / `ensure_index()` 两个异步函数。

**R2 的实测结论（画面维度）**：

| 项 | 结果 |
|----|------|
| 镜头切分 F1（easy 合成硬切） | **1.000**（P=1.000 R=1.000，3 段素材 9 个切点全中） |
| 镜头切分 F1（texture 纹理硬切） | **1.000**（目标 ≥0.90 ✓） |
| 渐变转场 crossfade | 漏报 1/1 —— **方法学盲区**：不是硬切，scene 检测看不到 |
| 同色相邻段 | 漏报 1/1 —— 像素完全相同，任何画面方法都测不到 |
| 段内亮度突跳（闪光/自动曝光） | 本组参数下**未复现误报**（阈值 0.3）；不能当作必然，需真实素材验证 |
| `visual_tags` 覆盖率 | **0.00 → 1.00**（5/5 镜头拿到标签） |
| 质量维度 | 黑场段 欠曝=1.000 ✓、纯白段 过曝=1.000 ✓、噪声段 边缘能量=181.84 ✓ |

**R2 的实现边界（如实）**：
- **合成素材上的语义标签不可信**：纯色/纹理帧上 CLIP 只给出"屏幕录制/字幕板/空镜"这类弱相关
  标签，本地 ImageNet 分类器甚至给出"revolver（左轮手枪）"这种明显无关结果（典型分布外失败）。
  真实拍摄素材上的标签质量**尚未验证**——这是本轮最该补的验证。
- 镜头语言只做了"景别 + 是否有人脸"，且用的是 OpenCV 自带级联/HOG（弱检测器）；
  **运镜（推拉摇移）没有做**，`camera_motion` 仍是帧间差启发式。
- 云端多模态通道已接通、**默认关闭**，本轮验收**没有真实调用过云端**（只有打桩测试）。
- 修掉一个真实崩溃：OpenCV HOG 的默认窗口是 64×128，在小图上调用 `detectMultiScale`
  会让**进程直接崩掉**（不是异常，`try/except` 捕获不到）。现在检测前先按最长边缩放到
  ≤640，且图小于窗口时跳过 HOG——回归用例 `TestDetectorSafety` 会在崩溃时杀掉测试进程，
  因此它"跑得过"本身就是证据。
- `INDEX_VERSION` 升到 **2**（ShotStats 增加质量字段），旧索引自动重建（不做迁移）。

**R3 的实测结论（音频维度）**：

| 项 | 结果 |
|----|------|
| 音频事件时间误差（大响段） | **0.020s**（门槛 ≤0.3s ✓） |
| 事件类型 | `silence` / `loud` / `onset` 三类，阈值分位数自适应并随事件记录 |
| 转写对齐 | 3 段转写落到 3 个镜头全命中；跨镜头片段按**交集切分**给两侧；边界片段归后一镜头 |
| 关键词 | 域词表命中优先（"进球集锦" > "进球"），无分词器依赖（CJK 二元组 + ASCII 词） |
| 领域事件定位 | 解说"球进了，绝杀！"→ `goal` 事件 @ [2.6-3.2]，置信度 0.75，**带证据句** |

**R3 的实现边界（如实）**：
- **只做能量类事件**：区分人声/音乐/掌声需要声学模型，本轮不做（所以事件类型里没有
  "speech"）。`silence`/`loud`/`onset` 都是可测量量，不含语义判断。
- **自动转写这条链在本机没有被真正跑通**：机器上**没有安装任何 STT 后端**
  （whisper / faster-whisper 均缺失，插件 STT 也没注册），因此
  `align_transcript_to_shots` 走的是"取音轨 → STT 不可用 → 记录原因并跳过"的分支；
  对齐逻辑本身用**合成转写**验证（含跨镜头切分与边界归属）。真实转写接入后需重跑本组验证。
- 恒定电平（整段数字静音/纯音）**不出任何事件**——没有动态范围就没有"事件"；
  全静音属于素材级属性，由语音层（无 speech）表达。
- 事件时间精度 = 文本片段的时间精度（词表命中只能定位到"这段话的区间"），
  不是"事件发生的精确帧"；要更精确需要词级时间戳或专用动作模型。

**R4 的实测结论（检索层，M1 达成）**：

| 组 | 题目数 | 命中 | 说明 |
|----|--------|------|------|
| A：检索层（注入的确定性索引） | 22 | **22/22 = 100%** | 关键词/语音/结构化/排序/时间定位全覆盖 |
| B：端到端（真跑合成视频建索引后提问） | 10 | **10/10 = 100%** | 期望值全部来自**视频构造方式**（黑段 0–1.2s、白段 2.2–3.2s、大响音 1.0–2.0s），不是从索引里读出来 |
| 合计 | 32 | **32/32 = 100%**（门槛 ≥80%） | 按类型：keyword 7/7、speech 4/4、structural 16/16、shot_at 5/5 |

**R4 被实测逼出来的两个设计修正**（都不是"加个模型"能解决的）：
1. **自然语言 → 结构化条件的路由**：用户说的是"黑场在哪""哪些镜头有人脸""最长的镜头"，
   而这些词**根本不在镜头文本里**，纯关键词检索必然落空。于是加了 `route_query()`：
   "说法 → 条件/排序"的规则表（黑场→`is_black`、过曝→`min_overexposed`、掌声→`loud` 事件、
   最长→按时长排序…），命中后把该说法从查询词里剔除，避免污染关键词打分。
2. **排序/清单式问题必须直接给清单**：`"最长的那一个镜头"` 剔除口水词后没有内容词元，
   若仍走"打分→过滤"就会返回空。现在"清单式问法（哪些/列出/一共…）"或"无有效词元"
   一律走清单模式（条件+排序即答案），并保留"为什么"（`结构化条件命中`/`按 X 排序`）。

**R4 的实现边界（如实）**：
- 命中率是**合成基准**上的数字：A 组用注入索引（隔离标签质量），B 组用合成视频
  （内容由构造决定）。**真实素材上的问答命中率仍未验证**；
- 文本向量召回用的是**镜头文本**（描述+标签+语音），**不含画面**——画面语义要等 R9 的
  视觉向量；本轮默认跑关键词+结构化（`--embed` 可开向量路径）；
- 路由规则表是**人工词表**：说法超出覆盖范围（方言、隐喻）会退回关键词路径；
- 任务书里的"30 条问答"实际做成了 **32 条**（A 22 + B 10），因为分两组才能说清是谁的问题。
- 只接线了 RequirementsAgent（简报阶段拿到素材内容摘要）；EditAgent/StructureAgent 的
  接线按计划留在 R8（方案接地：`raw_scenes` 带 material_refs）。

**R2 的实现边界（如实）**：
- **合成素材上的标签不可信**：实测纯色/纹理合成帧上，CLIP 只能给出"屏幕录制/字幕板/空镜"这类
  弱相关标签，本地 ImageNet 分类器甚至给出"revolver（左轮手枪）"这种明显无关的结果
  （典型的分布外失败）。真实拍摄素材的表现**尚未验证**——这是本轮最需要后续补的验证。
- 镜头语言只做了"景别 + 是否有人脸"，且依赖 OpenCV 自带级联/HOG（弱检测器）；
  运镜（推拉摇移）**没有**做，`camera_motion` 仍是帧间差启发式。
- 云端多模态通道已接通但**默认关闭**，且本次验收**没有真实调用过**云端（只做了打桩测试）。
- `INDEX_VERSION` 升到 2（ShotStats 增加质量字段），旧索引会自动重建（不是迁移）。

---

## 8. 与既有计划的关系

- 本计划**不推翻** `docs/fix-and-feature-plan.md` 的既有条目，而是补齐其中"Agent 对素材内容无感知"这一结构性缺口（此前 P8–P10 的资产治理只做了 sha256 去重/used_count/巡检，没做内容语义）。
- 复用并强化既有件：V6 短语打包（已被索引层消费）、V7 复核图（升级为"喂多模态的帧网格"）、V8 项目记忆（承接落位证据）、`mg_fastpath`（集锦包装的记分牌/标题动画渲染提速 3.4–11.8×）。
- 执行时每轮仍按既有约定：`docs/fix-and-feature-plan.md` 追加执行轮次记录 + 客户端 `docs/` 副本同步 + 各自仓库提交（不 push）+ 如实标注验证边界。
