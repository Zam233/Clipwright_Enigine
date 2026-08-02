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

**shotcraft 动效风格指引**：`llm_mg` 的 system prompt（`mg/config.yaml`）内置「动效风格指引（video-shotcraft 镜头卡方法论）」一节，约束 LLM 生成的运动节奏，避免动效堆砌：
- 单镜头单主角：一个镜头内只让一个元素担任主角动效，其余元素保持静止或轻微陪衬
- 入场错峰 0.2-0.5s/元素：多元素按 0.2-0.5s 间隔依次入场，制造推进感
- 关键信息落定后 hold：核心信息入场完成后静止约 1s 再切换或推进下一镜，留足阅读时间
- 批量动效收尾留白：一组连续动效结束时末尾预留 0.5s 静止画面
- 缓动节奏：常规入场/出场以 ease-out 为主；冲击感用快速放大 + 轻微回弹（如 scale 1.0 → 1.15 → 1.0）
- 发光克制：避免装饰性发光滥用，仅对单点关键元素做高质量光效，其余保持干净

**内置 MG 模板**：shotcraft 镜头卡模板存放于 `clipwright/animation/mg/templates/`（该目录被 `.gitignore` 忽略，模板属于运行时资源），配合 `catalog.py` 中对应的 `hf-*` CSS keyframes（`hf-spotlight` / `hf-row-stagger` / `hf-deal-in`，经 `get_css_keyframes_all()` 注入 HTML 渲染）使用：

| 模板 ID | 镜头卡效果 |
|---------|-----------|
| `mg_spotlight_hero_card` | 聚光主角卡：scale 放大 + brightness 高光后定格（scale 1 → 1.05 hold） |
| `mg_row_embed` | 行内嵌入：多元素自下而上错峰入场（translateY 24px → 0） |
| `mg_deck_deal_flyin` | 卡组发牌：translateY + rotate 组合飞入（translateY 60px / rotate 10° → 0） |

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

降级后的文字 clip 不再是静态文字：所有兜底路径（逻辑动画降级、MG 未匹配、MG 渲染失败、`mg_dynamic` 兜底）都会生成带关键帧的动画文字 clip（`anim_type="fade_in"`，opacity 0 → 1 → 1 → 0 四段），保证降级后仍有效果。

## 字幕烧录（Subtitle Burn-in）

字幕烧录由 AudioAgent 与渲染服务协同完成，受 Persona AudioConfig 的 `audio.subtitle_enabled`（`StrictBool`，默认 `True`）门控：

1. **生成字幕 clip**：Auto-Dub 配音时，AudioAgent 依据旁白分段（dub_script 返回的 segments）在文字轨生成 `kind=caption` 的 clip——与对应旁白音频 clip 的 `start_sec` / `duration_sec` 对齐，`text` 取分段文案（截断至 100 字符），`metadata` 记为 `{category: "caption", renderer: "drawtext", position: "bottom"}`，轨道名为「字幕轨」。
2. **幂等**：已有字幕覆盖同一时间段时跳过；重复运行管线不会产生重复字幕。
3. **门控关闭**：`subtitle_enabled=False` 时仍正常配音，但不生成字幕轨道与字幕 clip。
4. **渲染样式**：渲染服务的 `_extract_text_overlay` 对 `category == "caption"` 的 overlay 强制 `offset_y=0`（避免长视频多条字幕按 35px/行堆叠被推出屏幕）；未显式指定样式时注入默认描边 `{stroke_width: 2, stroke_color: "#000000"}`，最终在 drawtext 滤镜中输出 `borderw=2` / `bordercolor=#000000`。

## Hyperframes 可用性保障

Hyperframes 渲染器依赖外部 npx HTML/CSS → MP4 能力，冷启动探针在首次运行时可能返回默认 False，导致逻辑动画/MG 动画被误降级为静态 drawtext。为此提供以下保障：

- **`await_available(timeout)` 轮询助手**：`hyperframes_renderer.await_available()` 每 2s 轮询缓存探针（`_AWAIT_POLL_INTERVAL=2.0`），探针成功后返回 `True`，超时返回 `False` 且不抛异常。
- **启动预热**：`main.py` lifespan 中以非阻塞后台任务预热（`spawn_background(HyperframesRenderer.await_available(120.0), name="hyperframes-warmup")`），消除冷启动首跑降级。
- **异步探测**：AnimationAgent（`_handle_logic_animation` / `_handle_llm_mg`）与渲染服务 S2 门控均改为异步等待 Hyperframes 可用性（120s / 60s），仅在真实不可用时才降级并记录 trace warning。

## 内容质量检查（空镜头与动画生效）

QualityAgent 在动画编排之后对时间线做内容级校验，新增三类检查：

- **空镜头检测（error, category=material → redo_agent=material）**：调用真实的 `frame_validator` 工具（FFmpeg `signalstats` / `blackdetect`），对 video/image 素材做有界并行抽样（Semaphore(4)、上限 30 个 clip、单 clip 30s 超时）；`is_blank` / `is_white` 命中时输出「素材 {id} 为空镜头/全白帧，需重新选材」，`redo_agent="material"`，触发素材 Agent 重做。`frame_validator` 抛异常或返回无效结果时记录为 warning 而非 error，不阻断管线。
- **动画生效检查（warning, category=animation）**：动画 clip 若 `renderer` 降级为 drawtext / `mg_hyperframes` 缺 `mg_html`，说明动画未实际生效，输出 warning（附降级原因，如「hyperframes 不可用/降级」）。
- **越界检查（warning）**：动画 clip 时间范围超出场景/时间线边界时输出 warning。

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
