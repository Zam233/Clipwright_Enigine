# 编辑器预览与资源库结构工程 PRD

版本：1.0　日期：2026-11-02　范围：Clipwright 前端编辑器与工作区 API

> 本文现状结论按当前代码快照书写；文件行号是证据定位。方案中的阈值是本 PRD 的验收契约，不表示代码当前已达到。

## 0. 结论摘要表

| 做什么 | 不做什么 | 谁验收 |
|---|---|---|
| 将逐帧视频 seek 改为连续解码帧合成；将 MG JSON/HTML 接入时间线预览；合并工作区媒体入口并把上传写入 `materials/` | 不重写时间线模型、不以服务端预渲染代替交互预览、不改变 Agent/任务终止机制 | 产品负责人确认 Tab 与历史迁移口径；前端/音视频工程师验收播放与交互；后端工程师验收工作区契约与权限 |

## 1. 现状与根因

### 问题 1：前端播放预览不顺畅，是一帧一帧在播放

- **现在的实现**：不是浏览器把整条时间线交给一个 `<video>` 播放。`PreviewPanel` 用 `performance.now()` 差值递增播放头，随后把每次时间变化写入 Zustand；独立 Canvas rAF 发现 `currentTimeSec` 变化后按时刻重合成；视频层调用 `seekVideo()` 设 `video.currentTime` 后立刻 `drawImage`。`PreviewPanel.tsx:168-207,502-522,983-1010`；`mediaManager.ts:329-344`。
- **根因**：视频解码与显示被实现为“每个预览时刻 seek 源视频 + 同步 drawImage”，而非让 `<video>` 连续播放、按解码帧通知合成；`currentTime` 被设定后，新帧异步到达，函数立即返回旧画面，只有 `seeked` 事件再通知重绘。播放 rAF 还把单次 `dt` 截断到 `1/15s`（`PreviewPanel.tsx:173-175`），后台/掉帧后播放头会丢失墙钟时间；这会造成源帧重复、跳帧和“幻灯片”观感。音频同步另以 100ms 节流及 80ms 偏差 seek（`PreviewPanel.tsx:111-166`），不能充当连续视频时钟。
- **影响面**：视频轨播放、视频转场重叠、视频与音频同步；静态图/文字主要受主 rAF 和合成耗时影响。现有帧缓存只按源帧号量化，能够减少同帧内重复绘制，但不能补出尚未解码的新帧（`PreviewPanel.tsx:1412-1438`）。

### 问题 2：动画既然是 WEB 的，理论上前端可以渲染动画，那也应当能预览 —— 总之只要在时间线上，就应当可以预览

- **现在的实现**：普通 `animation` clip 在 Canvas 中画正弦脉冲形状并显示“未渲染预览”，不是 MG 内容（`PreviewPanel.tsx:1129-1170`）；通用 clip 的关键帧插值已可驱动 opacity/transform/speed（`:1353-1366`）。后端已有 MG JSON 到 HTML/CSS/JS 转换和 `/api/animation/mg/html`，前端有安全 sandbox iframe 驱动脚本，但该 helper 是单独预览链路，不是时间线 compositor 的一层（`mgHtmlPreview.ts:1-13,23-89`；`clipwright/api/animation.py:14-22,119-167`）。
- **原因**：MG HTML 预览是隔离 iframe，Canvas compositor 不识别 `metadata.mg_def`/`mg_html` 并绘制其结果；当前 `animation` clip 绘制分支只输出占位形状。普通 timeline keyframe 与 MG 元素 keyframe 是两个不同契约。
- **影响面**：时间线可添加动画轨道，但 MG 视觉内容不能随播放头在主预览窗显示；单独 MG 预览页/接口不能满足多轨合成。

### 问题 3：工作区和素材库合并，以工作区功能为准，同时保留素材库上传功能（上传 = 传到工作区目录）

- **现在的实现**：AssetPanel 同时提供 `素材库` 与 `工作区` 两个 Tab（`AssetPanel.tsx:21-30`）。素材库读 `/api/asset/list` 注册表，工作区树读 `/api/workspace/{id}/media-tree` 实际目录，能显示未索引文件（`WorkspaceMediaTree.tsx:8-9,43-54`）。
- **原因**：`assetApi.upload()` 走 `/api/asset/upload`（`asset.ts:59-79`）；后端用 project_id 的 AssetManager 写 `projects/{project_id}/assets/{files,thumbnails,index.json}`，不是工作区 `materials/`（`asset.py:90-128`；`services/asset_manager.py:90-120`）。工作区上传端点 `/materials` 写 `materials/<kind>/<filename>` 后做一次扫描索引（`workspace.py:274-294`）。两者是不同物理目录和索引。
- **影响面**：目前素材库上传的文件不会自然出现在 workspace media-tree；目录树和素材注册表构成两份视图/两种索引。合并 UI 若只改 Tab，会出现上传后“素材不见”。

### 问题 4：顶部搜索框和上传按钮层级错误，它们应该在工作区 Tab 下，而不是所有 Tab 都显示

- **现在的实现**：搜索与上传行在 Tab bar 后始终渲染（`AssetPanel.tsx:197-239`），但 searchQuery 只过滤素材库、传给工作区树（`:193-195,282,286-305`）；AI/配音/插件/历史 Tab 也出现该行，上传仍调用资产库端点（`:111-123`）。
- **原因**：该操作区不受 `activeTab` 条件包裹，且“上传”闭包绑定旧 `assetApi.upload`。
- **影响面**：操作上下文混淆、无关 Tab 浪费垂直空间，工作区上传目标与用户认知冲突。

## 2. 术语

| 术语 | 定义 |
|---|---|
| 播放时钟（master clock） | 唯一决定时间线媒体时间的单调时钟源；媒体帧呈现不得反向驱动多个互相竞争的时间值。 |
| 预览合成（preview compositor） | 将某时间点活跃轨道按 z 序、遮罩、变换及效果合成为预览帧的前端引擎。现有入口为 `compositeTimelineClips()`（`PreviewPanel.tsx:921-949`）。 |
| 逐帧驱动（frame-driven） | 按播放头变化对源视频反复设置 `currentTime`、等待 seek/解码再画帧；与连续播放解码不同。 |
| 动画中间表示（MG spec） | 后端 MGGenerator 生成的 JSON：动画元信息、元素数组、每元素 keyframes/样式/参数；不是 HTML 字符串（`mg_renderer.py:1-16`）。 |
| 工作区 | 项目绑定的文件系统目录；`materials/` 为用户媒体写入区（前端 API 注释 `workspace.ts:4-9`）。 |
| 素材库 | 旧 AssetManager 的资产元数据/缩略图/文件索引视图；项目库位于 `assets/`（`asset_manager.py:90-120`）。合并后该名称不再作为独立顶层 Tab。 |

## 3. 目标与非目标

**目标**

1. 1080p、30fps 项目普通单视频轨播放 60 秒可连续观看；帧调度目标 30fps，UI 呈现不以 seek 完成前的旧帧冒充新帧。
2. 视频、图片、音频、字幕/文字、shape、MG animation、转场按统一 timeline time 参与预览；能力未实现或失败时给明确占位/保真提示，而非静默黑屏。
3. MG spec 在预览中随 timeline playhead seek/play/pause；离线最终渲染仍以后端 Hyperframes/既有渲染器为权威。
4. 资源面板的“工作区”成为唯一媒体管理 Tab；顶部搜索、上传仅属于该 Tab；上传文件可在同一工作区目录树中见到。

**非目标**

- 不改成服务端渲染整片视频流，不把 `/api/animation/preview` 的 MP4 作为交互预览主路（现有端点是 Hyperframes 短视频渲染，`animation.py:170-216`）。
- 不重写时间线数据模型；复用秒、clip、track、keyframes 与 `metadata` 扩展（后端契约 `schema/timeline.py:58-79,172-184`）。
- 不实现专业 NLE 的全格式编解码、代理生成、HDR/色彩管理；浏览器解码能力仍受格式/硬件限制。
- 不改变任务状态、会话映射、Agent/Task 的 stop/capped/closeout 机制；AGENTS.md #14/#21（`J:\Clipweight-Client\AGENTS.md:72-76,119-138`）明确其状态机及终止语义。
- 不在 UI 合并时删除旧 `assets/` 文件、`index.json` 或已上传历史。
- 不违反渲染边界：模型输出/API/事件 detail 展示必须走 `asText`/`textList`；AGENTS.md #13（`:69-71`）。

## 4. 预览架构方案

### 4.1 推荐方案：连续媒体解码 + 单一 timeline clock + 统一 compositor

继续用 Canvas 作为跨轨合成面，不将各个 `<video>` DOM 直接堆在画布上。改变源帧供给和时钟职责：

1. **时钟**：播放期间由 `performance.now()` 的绝对基准计算 `timelineTime = anchorTimeline + (now-anchorNow)*speed`，不逐帧累加、不截断 dt；只在 rAF 回调发布最新播放头。主时钟仍是 timeline clock。若存在已播放音轨，第二阶段接入单一 WebAudio `AudioContext.currentTime` 作为 master clock；M1 不采用独立 AudioContext 作为权威，因为多个 media element 当前各自 seek/play（`PreviewPanel.tsx:123-150`），而不能证明所有 source 已统一调度。暂停/隐藏时冻结时钟，恢复重新设 anchor。loop/终点按现有 region 语义钳位。
2. **视频帧源**：每个活跃 video element 连续 `play()`，`playbackRate = clip.speed * timelineSpeed`；由 `requestVideoFrameCallback`（支持时）报告新解码帧，回退到 `timeupdate`/rAF 检查 `readyState`。Canvas 每个显示刷新周期最多合成一次，取每个 clip 最近已呈现帧，不 seek 每次 rAF。片段切换时 seek 仅发生在首次激活、源偏移变化、倒放/随机定位、时钟漂移超阈值。切轨策略：按 timeline time 更新活跃集合；退出活跃窗 pause，前后缓冲窗只 metadata/preload，禁止为全时间线所有视频 `preload=auto`。
3. **漂移校正**：视频媒体局部期望时间与 `video.currentTime` 偏差 ≤80ms 不校正；80-250ms 将 `playbackRate` 临时调整到目标速率 ±5%，最长 500ms；偏差 >250ms 或 clip 边界强制一次 seek，等 `seeked`/新帧回调后才显示新源帧。背景标签隐藏时暂停所有 media，继续沿用 `PreviewPanel.tsx:215-225`。
4. **音频**：短期沿用 HTMLMediaElement 音频与 AudioContext analyser；所有音轨均由 timeline clock 算 clip-local time。事件/音频同步节流由当前 100ms 改为 20ms 或边界/误差触发，不在每次 Canvas draw 遍历全 timeline。
5. **轨道 z 序与层**：`track.index` 升序绘制，较大 index 在上，沿用 `orderTracksForComposite()`（现状说明 `PreviewPanel.tsx:462-464,931-945`）。video/image/MG/text/shape 均参与画面合成；audio/waveform 不进入视频像素层。每 clip 顺序：源内容 → clip transform/opacity/blend → transition 几何/alpha → mask clip；MG 自带元素按 spec 元素顺序及显式 z-index 稳定排序，默认后项在上。遮罩作用于本 clip 离屏层，不能裁掉其它 track。
6. **缓存/缓冲**：同一时刻可解码视频上限 2（主画面 1 + 同时重叠转场 1）；超过 2 时按 z 序/可见面积选主层，非主层低分辨率/冻结最近帧并打性能徽标。预加载最多 2 个未来视频 asset，窗口为当前 clip 结束前 2 秒；预加载失败不阻塞当前帧。解码帧由 video element 内部缓存，不复制全尺寸 ImageBitmap 常驻；Canvas layer cache 总额 256 MiB、单项目 LRU，超额清退最久未用帧。2D 合成 1080p 目标 ≤16ms/帧（30fps 每帧预算 33.3ms，留至少一半给解码与浏览器）。
7. **渲染分层**：低负载/无视频时 Canvas 按 display rAF 绘制；高负载时视频 `requestVideoFrameCallback` 驱动画布合成，rAF 仅作 playhead/UI 更新。普通静态层缓存按内容 key 命中；视频层 cache key 为 asset + source frame + transform + media version，保留现有帧量化语义（`PreviewPanel.tsx:1412-1438`）。

**关键取舍**

- 用单一 timeline clock 而不是拿每个 `video.currentTime` 作全局权威：多轨可能多个视频并行，无法让多个 element 同时作主时钟；视频 currentTime 仅用于局部漂移校正。
- 用连续解码而不是继续降低 seek 阈值/提高 seek 频率：当前机制本质仍每个目标时刻 seek，减阈值会增加解码寻址与旧帧竞态，不会创建连续帧。
- 保留 Canvas compositor 而不是 DOM video 叠层：现有 Canvas 已实现多轨 z 序、变换、混合、mask、转场，DOM 叠层难以统一裁剪/混合和导出当前帧。
- 限制两路硬件解码而不是所有活动轨都 `play()`：解码器与显存有设备相关上限；打开多轨会造成解码资源竞争和内存峰值。
- 非主层冻结帧而不是掉帧时阻塞主画面：编辑预览优先维持播放头和主画面响应，降级必须有可见标识。
- 不让音频在 M1 变成唯一时钟：现状媒体 source 分散在每个 element，接 WebAudio 主时钟需要重构音频节点与自动播放策略，放到后续阶段可控。

### 4.2 被否方案

1. **继续 Canvas + 每帧设置 currentTime**：否决。`seekVideo()` 已在每次合成层调用，且立即返回 element；异步 seeked 仅补通知（`mediaManager.ts:329-344`），无法构成稳定 30/60fps 连续播放。
2. **纯 DOM `<video>` 叠层**：否决。最多适用单视频，不足以表达任意轨道 z 序、混合模式、遮罩、文字/图像/MG 统一像素合成；当前 timeline contract 有多轨（`schema/timeline.py:240-252,261-269`）。
3. **先把整条 timeline 后端渲成 MP4 再播放**：否决。可作质量对照/最终成片，不可即时反映拖拽修改；现有 `/api/animation/preview` 会跑 Hyperframes 渲染（`animation.py:170-216`）。

### 4.3 可人工测量指标

| 指标 | 验收值 | 人工测法 |
|---|---:|---|
| 普通视频播放 | 1920×1080、30fps timeline 连播 60s，累计丢失/重复新帧 ≤30 帧（≤1%）；播放头与墙钟误差 ≤100ms | 打开性能浮层，播放已知 60s 素材；比较开始/结束时间码和外部秒表；记录 frame callback 计数与视频自然帧计数 |
| 首帧 | 点击播放后首个有效画面 ≤500ms（本地 SSD/已缓存），网络素材 ≤1500ms；超时显式 loading/旧帧标识 | 暂停在 clip 前，播放并用秒表测有效内容出现时间 |
| Seek 恢复 | 1080p 文件随机 seek 后新画面 ≤300ms 本地、≤1000ms 网络；旧帧不得标作已就绪 | 暂停、拖到 5 个不同位置，逐次计时到画面更新 |
| 合成预算 | 活跃 2 video + 4 文本/shape 图层时 Canvas p95 ≤16ms（1080p）；后台主线程卡顿允许单次丢帧，不连锁时钟变慢 | 性能浮层记录 60s p95；无自动化测试在本 PRD阶段执行 |
| 音画漂移 | 60s 内音画偏差 ≤100ms；clip 切换恢复 ≤250ms | 使用可见闪光/拍手同步样片，外接耳机和波形目测对齐 |

丢帧口径：以 video frame callback 的 `presentedFrames` 增量与 timeline 目标帧数比较；同一解码帧重复合成不重复计数。指标是项目验收阈值，不是当前实测结论。

## 5. 动画前端渲染方案

### 5.1 能力结论与转换层

结论：**能在浏览器渲染，但需要把 MG spec 映射到浏览器渲染层，并接入统一 compositor**。MG 中间表示是 JSON，不是 HTML/CSS；MGRenderer 已有成熟转换：MG JSON → HTML/CSS keyframes/JS，并可生成共享 `hf_timeline` runtime（`mg_renderer.py:31-45,233-307,405-448`）。后端 `/api/animation/mg/html` 已接受 `mg_json` 或 `animation_id`、返回 HTML 和 duration/fps（`api/animation.py:14-22,119-167`）。浏览器可直接消费 HTML，但 iframe 画面不能直接由 Canvas 合成，故优先实现 MG spec → Canvas display-list；非支持元素以 sandbox iframe 单层 `drawImage` 回传为过渡实现，不允许把未净化 HTML 注入主文档。

### 5.2 结构与接口

前端 `MgSpec` 类型对齐 JSON：`animation_id,name,duration_sec,width,height,elements[],params,style`；每 element 为 `type,text|shape|line|circle|ring|arc|bg|image`、x/y/offset、尺寸、静态 style、`keyframes[{time,properties,easing}]`。后端 validator 的类型/可动画属性为权威（`mg/validator.py:7-12,89-169`）。进入时间线时 `Clip.metadata.mg_def` 存结构化 spec（而不是只存 HTML）；可选 `mg_params` 保存替换参数。clip `start_sec/duration_sec` 映射 spec local time：`local = clamp((timelineTime-start_sec)*speed + source_offset, 0, mgDuration)`。现有普通 clip.keyframes 保持 transform/opacity；MG 内部 keyframes 不再写入 timeline clip.keyframes，避免两套关键帧互相覆盖。

定义接口：`MgRenderer.prepare(spec, params, width, height): PreparedMg`；`PreparedMg.draw(ctx, localTime, rect, dpr): {drawn,unsupported[]}`；`dispose()` 释放图片/离屏资源。每帧不创建 DOM/iframe；准备阶段解析元素、加载受允许路径/工作区代理的 image、预编译文字和变换。MG 内容画在透明离屏 layer，再按 clip opacity/blend/mask/transition 合成。

### 5.3 easing、文本、兼容校验

- easing 名称复用前端 `interpolateProperties()` 的 cubic bezier 与 `bezier` 控制点（`timeline/engine/easing.ts:186-225`），统一别名表：linear/ease/ease-in/ease-out/ease-in-out、back-out、elastic-out、bounce。MG renderer 对 elastic/bounce 使用 cubic-bezier 近似（`mg_renderer.py:34-45`）；Canvas 实现用同一映射表，差异标注 approximate。
- 文字布局以 1920×1080 spec 坐标为逻辑坐标，按 timeline width/height 等比缩放；锚点 center/left/right/top/bottom 与 `x_offset/y_offset` 对齐后端规则（`mg_renderer.py:356-397`）。Canvas 用 `measureText` 换行与字距；字体加载完成前提示字体回退。最长行按 90% frame 宽度，溢出按 spec 明确缩小字体/换行，不静默裁切。
- CSS `background` 渐变、box/text shadow、图片 object-fit、圆弧、font metrics 等与 Canvas 行为可能不同。首先实现核心矢量/文字/图像元素；对不支持的 CSS 值由 `unsupported[]` 返回，显示预览差异角标。
- 一致性校验：固定 10 个模板/参数样例，在 0%、25%、50%、75%、100% 时间点，比较前端 canvas snapshot 与后端 Hyperframes Chromium 帧；首期人工审图，目标结构化矢量内容 SSIM ≥0.97、文字 bbox 偏差 ≤2%画布，图片/字体允许列明例外；每次 renderer 变更复核。服务端最终渲染仍以既有后端为准；不承诺像素级相同。
- 风险控制：主预览角标标“浏览器近似预览”；导出链不读取前端栅格帧。现有 fidelity 系统已承认 LUT 无预览、star/heart mask 仅预览等差异（`previewFidelity.ts:4-11,23-45`），MG 差异纳入同类机制。

### 5.4 为什么不是纯 CSS animation

后端确实生成 CSS `@keyframes`（`mg_renderer.py:405-448`），但纯 CSS动画由 wall clock 自行推进，无法可靠响应外部 timeline seek、变速、暂停以及跨轨同步。保留 CSS 作为 iframe 过渡/最终渲染输出，不把其作为 compositor 权威时钟；Canvas/display-list 根据 playhead 可确定性 seek 任意时刻。

## 6. 时间线可预览矩阵

| Clip 类型 | 当前能否预览 | 合并后能否预览 | 依赖能力 |
|---|---|---|---|
| video | 有条件：Canvas 读 `<video>` 帧，但反复 seek，有时旧帧/占位（`PreviewPanel.tsx:983-1037`） | 是，连续解码、帧回调、漂移校正 | 双 video 解码预算、MediaManager 生命周期 |
| image | 是，Canvas drawImage 与变换（`:997-1003`） | 是 | 图片预加载、同层遮罩/缓存 |
| audio | 无视频像素预览；独立 media element 播放并做电平分析（`:111-166`） | 是，时间线上可听；waveform 仅绘制可视波形，不占视频画面 | 统一时钟、激活/停播与音量/静音 |
| text/caption | 是，Canvas 排版、样式、keyframe（`:1040-1127`） | 是，保留并统一 compositor | 字体度量/排版基线 |
| shape | 是，基础 shape 绘制（`:1171-1175`） | 是 | layer 合成、属性插值 |
| animation（MG） | 有占位动画；MG 本体不在 timeline canvas 渲染（`:1129-1170`） | 是 | MG spec adapter、cache、safe asset loader |
| transition | fade/dissolve alpha、slide/wipe 几何已支持（`:724-767,1395-1405`）；其他转场近似 | 是，当前类型全覆盖并对近似项标记 | layer overlap、transition mask/shader；pixel dissolve/glitch 分期实现 |
| waveform | 不作为独立画面 clip 的通用媒体源（`paintClipContent` 默认不绘制，`:1176-1178`） | 是，作为 timeline 可见 waveform 图层；非声音真源 | waveform peak 缓存及 canvas bars |

## 7. 资源库合并方案

### 7.1 最终 Tab

| Tab 名 | 内容来源 | 顶部操作区 |
|---|---|---|
| AI 匹配 | 远端素材源搜索/收藏/详情 | AI 搜索由 AI 面板自身持有；无全局工作区搜索/上传 |
| 工作区 | `GET /api/workspace/{id}/media-tree` 实时文件树；可附加索引状态 | 工作区搜索 + 上传到 `materials/<kind>/` + 刷新目录树 |
| 历史 | 本地 `assetHistory` 最近添加记录（最多 50，`assetStore.ts:42-47`） | 无全局上传；条目可定位工作区文件或提示已失效 |
| 配音 | DubView | 配音自身控件 |
| 插件 | PluginPanel | 插件自身控件 |

旧“素材库”Tab 下线为独立 Tab；素材浏览功能并入工作区，AI 匹配/配音/插件/历史功能保留。

### 7.2 上传目录与单一真相

- 新上传一律使用 `workspaceApi.upload(projectId,file)` → `POST /api/workspace/{id}/materials`，写入 `<workspace-root>/materials/<video|image|audio>/<sanitized filename>`，随后 scan/index（`workspace.ts:180-190`；后端 `workspace.py:274-294`）。
- **目录树是文件存在性唯一真相**：`media-tree` 以磁盘为准，包含未索引文件；workspace registry 仅提供 sha256、索引/视觉状态、tags，不决定文件是否可见（后端 `workspace.py:259-271`）。
- 旧 AssetManager `assets/index.json` 保持只读兼容；禁止新上传走旧 endpoint。合并期列表可用旧 AssetManager 条目补充“历史资产”虚拟分组，但不能把同一文件复制到 `materials/` 后生成第二份路径身份。
- 旧素材迁移：M2 后端提供一次性只读映射/导入动作，把旧 `projects/{id}/assets/files` 的文件复制（不移动、不删源）到 `materials/<kind>/`；基于 SHA256 去重，返回 `migrated/reused/failed` 和原 asset_id→新 rel path 映射。迁移默认用户显式触发，禁止静默批量写盘。外部素材/导入 path 若已不在 workspace，显示“外部引用”而不伪称工作区文件。

### 7.3 深链与状态迁移

`AssetTab` 从 `ai|library|workspace|history|dub|plugins` 迁移为 `ai|workspace|history|dub|plugins`（当前类型 `assetStore.ts:5`）。持久化偏好/URL 若含 `library`，解析兼容映射为 `workspace`；当前 store 并未持久化 activeTab/searchQuery（`assetStore.ts:28-53`），但仍需全仓检查路由 query/deep link 后再删除旧别名。搜索状态统一由 store `searchQuery` 提供，AssetPanel 当前 local state 将迁移到 store；切换离开 workspace 保留 query，进入 workspace 应用它。

## 8. 顶部操作区层级

- `activeTab === 'workspace'` 时才渲染工作区搜索与上传操作区；其余 Tab 不出现该区。AI 面板自己的语义搜索仍在 AI 内容区（`AssetPanel.tsx:584-596`）。
- 工作区操作区包含目录树范围搜索、上传、刷新；上传多文件逐个请求并显示总体进度与失败列表，成功后刷新 media-tree。不要再显示 `/api/asset/upload` 的素材库注册成功提示。
- AI 匹配保留 query/来源筛选/检索按钮；配音页放导入/生成配音控件；历史页提供清空历史/恢复工作区定位；插件页只放插件管理动作。
- 窄屏：操作区在 320px 宽时搜索输入占剩余宽度，上传按钮固定 36×36；目录树名称可截断但 title 提供完整 `asText(path)`。不把上传按钮藏入无标签图标（需 aria-label/tooltip）。
- 空态：无 projectId 显示打开项目提示并禁用上传；工作区无媒体时显示“上传到工作区”按钮；上传失败保留文件名和错误，不塞入非持久化 demo assets。所有错误/接口字段经 `asText`，满足 AGENTS #13。

## 9. 接口与数据契约增量

### 9.1 工作区媒体上传/列表

现有端点足够实现主路径，不新增必需 endpoint：

- `POST /api/workspace/{project_id}/materials`，multipart `file`；返回 `{project_id,saved,kind,scan}`（`workspace.py:274-294`）。前端应改为调用此端点，并将 `saved` 相对化为 `rel` 后刷新树。
- `GET /api/workspace/{project_id}/media-tree?limit=2000`，现有响应 `{project_id,root,count,kinds,dirs:[{dir,count,files:[{path,rel,name,kind,size,mtime}]}]}`（`workspace.py:259-271`；client `workspace.ts:149-168`）。为 UI 可操作性**修改** file shape 增加稳定 `sha256?`、`indexed_status?: indexed|pending|failed|unindexed`，缺省 `unindexed`；仍以 `path/rel` 标识真实文件。
- **新增** `POST /api/workspace/{project_id}/materials/import-legacy`，JSON `{dry_run:boolean=true, asset_ids?:string[]}`；默认 dry-run，不写盘；执行时按 SHA256 跳重并复制到 kind 目录，响应 `{dry_run, scanned, migrated:[{asset_id,sha256,old_path,rel}], reused:[],failed:[{asset_id,reason}]}`。每次最多 200 项；不支持直接覆盖目标文件。
- **不新增**独立 delete endpoint。保留现有 `DELETE /materials/{sha256}?purge=false` 仅管理索引；目录树实际文件删除另行列入开放问题，不把删索引误解成删文件（`workspace.py:297-322`）。

### 9.2 MG clip 数据契约

- `Clip.metadata.mg_def?: MgSpec`，`mg_params?: Record<string,string>`；缺省为空；clip duration 仍 timeline 权威，MG spec duration 缺省 3 秒、预览最大安全 duration 8 秒（现有 API `_resolve_preview_mg` 限制 `dur`，`animation.py:136-145`）。
- 可选 `metadata.preview_renderer?: 'canvas-v1'|'sandbox-html'` 仅诊断，不作为内容真相。渲染器不支持的 element 返回 warning list，不改写原 spec。
- 使用现有 `POST /api/animation/mg/html` 作为 HTML fallback，payload `{mg_json,params,width,height,fps}`，尺寸缺省 1280×720、fps 30（`animation.py:14-22`），但不通过它每次 rAF 往返。

### 9.3 前端 store / 组件

- `previewStore` 增 `playbackEpoch`/`clockMode`/`droppedFrames`/`lastPresentedTime`，仅用于运行时；timeline JSON 不持久化诊断值。时钟状态切换动作不可在渲染组件之外复制一套 playing 真值。
- MediaManager 增 `activateClip/deactivateClip/getPresentedFrame`、有限活跃解码池和销毁；保留 `seekVideo()` 供 scrub/随机 seek，不再由每个 `drawClipToPreview` 播放帧调用。
- `WorkspaceMediaTree` 接收 `search, upload, onRefresh` 或自持 API hooks；文件行双击/拖放入时间线需建 clip 元信息 `local_path` 为 workspace path，素材映射使用 `asset_id` 或稳定 workspace media id。
- API 改动同步 `J:\Clipwright\docs\frontend-backend-parity.md` 路由矩阵/客户端矩阵；对账以真实端点定义为准，文档指出 workspace upload `/materials` 与旧 `/api/asset/upload` 的差异（当前 parity workspace 增量 `:342-354`）。

### 9.4 契约取舍

不把完整 MG JSON 放新增顶层 clip 字段：`metadata` 已是双端开放扩展（`schema/timeline.py:181-184`），这样无需破坏现有 timeline shape；但需在 TypeScript `Clip.metadata` 对特定 key 做窄化 schema。不是存 HTML 字符串，因为 HTML 是后端 renderer 版本化产物且不能方便地确定性 seek/编辑。

## 10. 工作分解（WBS）

人日按 1 人日=8 小时，前后端联调另计；风险 H/M/L 表示影响×不确定性。各任务均含可点验行为。

| 编号 | 目标 | 涉及文件与函数 | 前置依赖 | 人工验收点 | 规模 | 风险 |
|---|---|---|---|---|---|---|
| T1.1 | 固化播放诊断基线与可见状态 | `PreviewPanel.tsx` playback loop/draw、`perf.ts` | 无 | 性能浮层显示 active decode、presented/drop/合成耗时 | M | M |
| T1.2 | MediaManager 连续播放与活跃解码池 | `mediaManager.ts` registerTimeline/seekVideo/prebuffer/pauseAll | T1.1 | 单 video clip 连播画面连续；最多两视频处于 decode-active | L | H |
| T1.3 | 单一 timeline clock 与漂移校正 | `PreviewPanel.tsx` Playback loop/audio sync、`previewStore.ts` | T1.2 | 播放 60s 后播放头误差≤100ms，seek 恢复≤300ms | L | H |
| T1.4 | compositor 帧回调/转场切轨 | `PreviewPanel.tsx` compositeTimelineClips/drawClipToPreview、`layerCache.ts` | T1.2/T1.3 | 两轨 overlap 转场无黑帧，主画面不被第二视频拖停 | L | H |
| T1.5 | 音频同步与性能预算控制 | `PreviewPanel.tsx` audio sync、`mediaManager.ts` analyser | T1.3 | 60s 样片声画误差≤100ms，音轨 mute 立即生效 | M | H |
| T2.1 | MG spec TS schema/adapter | `types/timeline.ts`、新增 `features/preview/mgRenderer.ts` | 无 | 将测试 spec 插入 timeline 后静态显示全部支持元素 | M | M |
| T2.2 | MG 动态关键帧与安全资源预载 | `mgRenderer.ts`、`PreviewPanel.tsx` paint MG | T2.1 | 拖 playhead 至 0/25/50/75/100%看到对应动画状态 | L | H |
| T2.3 | MG clip 与 timeline clock 联动/生命周期 | `PreviewPanel.tsx`、`mediaManager.ts` 或 renderer registry | T2.2/T1.3 | MG 与视频同时播放、暂停、seek、切项目均同步且无残留 iframe | L | H |
| T2.4 | 预览与后端输出一致性样例 | `previewFidelity.ts`、`mgHtmlPreview.ts`、`docs/animation_system.md` | T2.2 | 5 时点逐一比对 10 样例；不一致项显示角标 | M | M |
| T3.1 | 工作区上传与媒体树写通 | `AssetPanel.tsx` handleUpload、`workspace.ts` upload、`WorkspaceMediaTree.tsx` | 无 | 上传视频后树中出现对应 materials 子目录与文件名 | M | M |
| T3.2 | 搜索/上传操作区按 Tab 条件渲染 | `AssetPanel.tsx` render/header、`assetStore.ts` | T3.1 | 逐个点击 AI/工作区/历史/配音/插件，仅工作区显示通用搜索+上传 | S | L |
| T3.3 | 旧素材只读兼容与迁移 dry-run/execute | `api/workspace.py`、`services/workspace.py`、`asset.ts` legacy listing | T3.1 | 点 dry-run 显示计划不改盘；执行后旧文件在树中可见且旧路径仍在 | L | H |
| T3.4 | 旧 Tab/deep link 兼容、空态与错误边界 | `assetStore.ts`、AssetPanel、router/query consumer | T3.2/T3.3 | 打开旧 `tab=library` 自动落工作区；无 projectId 上传不可操作 | M | M |
| T3.5 | parity 文档更新与回滚开关 | 前后端 `docs/frontend-backend-parity.md`、前端设置/feature flag | T3.1-T3.4 | 关闭开关恢复只读旧入口且文件仍可见/不丢失 | S | M |

估算：M1 约 18 人日；M2 约 15 人日；M3 约 10 人日；联调/人工回归预留 6 人日。关键路径 `T1.1→T1.2→T1.3→T1.4→T1.5`；MG 与 M1 可先并行做 schema/adapter，但最终时间线集成依赖 T1.3 clock；资源库 M3 与预览可并行。

## 11. 人工验收清单

### M1：平滑播放

1. **操作**：加载 1920×1080、30fps、60 秒单视频项目，点击播放并持续观察一分钟。**期望**：不是按 seek 一张张停顿；有效帧连续更新，丢失/重复帧≤30，播放头与秒表误差≤100ms。
2. **操作**：播放中拖播放头到随机位置，再播放。**期望**：先显示明确 seek 等待/旧帧状态，新帧到达后恢复；本地 300ms 内恢复，不因旧帧闪回。
3. **操作**：两段视频做 0.5 秒叠化，连续播放。**期望**：最多两路解码，叠化中两图层同时合成，无黑帧；第三路若超预算显示降级。
4. **操作**：播放 60 秒音画同步样片并在中途切轨。**期望**：声画偏差≤100ms，静音/轨道 mute 立即生效。

### M2：时间线动画

5. **操作**：将带文字、shape、image、至少两组关键帧的 MG spec 放入 animation clip，播放至中段。**期望**：预览窗出现真实元素与运动，而非“未渲染预览”占位。
6. **端到端：MG 动画在预览中播放**。**操作**：同一时间线加入一段视频、一条文字和一个 MG 动画；播放、暂停、拖到 50%、再播放至 MG clip 末尾。**期望**：MG 与视频同步；拖动能精确显示该时刻；越出片段后 MG 消失；导出仍以后端渲染结果为准并显示近似预览说明。
7. **操作**：选用不支持的 CSS 特性/缺失字体/无效图片地址。**期望**：对应 clip 显示 unsupported/fallback 标记，不导致整个预览空白。

### M3：资源库结构

8. **问题 3** 操作：在工作区 Tab 上传 MP4、PNG、WAV 多文件。**期望**：分别落 `materials/video|image|audio/`，刷新后树仍可见；workspace scan 状态可见；文件出现在可拖入时间线的媒体列表。
9. **问题 3** 操作：对旧素材执行迁移 dry-run 后执行。**期望**：dry-run 不写盘；执行后旧文件仍保留，工作区路径有 SHA 去重结果，旧素材不会“看不见”。
10. **问题 4** 操作：依次点 AI 匹配、工作区、历史、配音、插件。**期望**：通用搜索框与上传只在工作区出现；AI 自身语义搜索仍在 AI Tab。
11. **问题 4** 操作：在工作区搜索子目录文件名并清除搜索。**期望**：树只筛文件，不改变磁盘内容；清除后恢复全部文件。
12. **操作**：无项目状态打开工作区并尝试上传；将窗口缩至 320px。**期望**：上传禁用且有明确提示；窄屏搜索输入/上传按钮不遮挡或溢出。

## 12. 风险与回滚

| 风险/成因 | 影响 | 缓解 | 触发后的应急动作 |
|---|---|---|---|
| 预览与最终成片不一致：Canvas 与 Hyperframes/FFmpeg 实现不同；现有 LUT/noExport 差异（`previewFidelity.ts:23-45`），MG 字体/easing 也可能不同 | 用户依据预览做出错误剪辑 | Fidelity badge；固定 10 个 MG 样例、5 个时点校验；最终渲染不复用前端帧 | 对问题 renderer feature flag 降级为“未渲染预览”，保留最终渲染输出，修复前禁止静默宣称一致 |
| 视频解码并发过高：mediaManager 每 asset 建 element（`mediaManager.ts:82-109,126-164`），缩略图还有独立 video（`:397-420`） | GPU/硬解码器饱和、崩溃、卡顿 | active decode 上限 2、thumbnail 单独限并发 1、离开活动窗 pause+释放 | 降到单解码；冻结非主层；提供“低性能预览”开关 |
| 合并资源库后已上传素材看不见：旧 `/api/asset/upload` 写 assets，新 media-tree 只扫工作区（`asset_manager.py:107-120`；`workspace.py:259-271`） | 文件被误判丢失、用户无法继续编辑 | 旧索引保留只读；迁移 dry-run + 复制非移动；旧路径兼容映射 | 回滚 UI flag 恢复旧素材库 Tab；不删除任何资产或目录 |
| 深链/书签失效：`library` Tab 下线；当前 store 未持久化但其它 URL/query consumer 尚未核实 | 用户已有链接落空 | 全仓检查并映射 `library→workspace`，至少保留一个发布周期 | 旧 id 解析兼容并 redirect，不清理历史 URL |
| 连续播放速率/漂移错误：原 rAF dt 被截断（`PreviewPanel.tsx:173-175`），变速和 29.97fps 时间网格并存 | 60 秒后播放头与媒体脱节 | 绝对时钟 anchor、帧 timebase 转换；慢速/快速及后台恢复人工验收 | feature flag 切回旧 loop，但保留诊断；暂停实时播放功能不回滚 timeline data |
| Canvas 混合/遮罩影响其它轨：当前部分裁剪在共享 ctx 上，隔离边界需核实（`PreviewPanel.tsx:1395-1405`） | 一轨 mask/transition 裁切其它轨 | 每 clip 离屏层 save/restore；重叠两轨覆盖样例 | 关闭该 clip 的高级 transition/mask 并显示 fidelity warning |
| MG spec 不可信/资源 URL 注入：MG JSON 可由 LLM 生成；后端已对 CSS url 与 image 来源做白名单（`mg_renderer.py:71-124`） | XSS、越权读文件、Canvas taint | 浏览器 renderer 采用元素白名单；image 仅 blob/同源 workspace 路由；HTML fallback 保持 `sandbox=allow-scripts` 且不允许 same-origin（`mgHtmlPreview.ts:3-6`） | 禁用 HTML fallback，仅显示占位；后端按现有校验处理，不在前端主源执行 LLM HTML |
| 上传超大文件/目录扫描阻塞：asset upload 2GB 上限（`asset.py:22-23`）；workspace upload 当前 `await file.read()` 全读内存（`workspace.py:288-291`） | 浏览器/后端内存峰值、上传失败 | 新上传 endpoint 增流式写入、明确 2GB 上限与分块 progress；工作区扫描上限 5000 文件（`:259-271`） | 上传失败不清理源文件；提示用户重试；回滚 endpoint 到只读模式，修复后重传 |
| AGENTS 核心不变量被旁路：渲染输出未过 `asText`，或顺手改 session/task 终态 | UI 崩溃或 Agent 流程状态回归 | PR review 明列 #13/#14/#21；此需求不触碰会话状态/终止逻辑（AGENTS.md `:69-76,119-138`） | 撤回涉及边界改动；仅回滚 UI feature flag，不迁移或改写 Task 状态 |

## 13. 分期与依赖

| 期 | 交付 | 依赖关系 | 独立发布条件 |
|---|---|---|---|
| M1：播放底座 | 连续视频帧、绝对 timeline clock、解码池、音画漂移和诊断；约 18 人日 | 必须先做。MG 与所有媒体类型依赖一致时钟、活跃帧供给与切轨语义；没有此基础无法验证“时间线上都可预览” | feature flag 默认开给单视频/静态图；遇设备兼容可切旧播放路径 |
| M2：MG 与全类型覆盖 | MG adapter、timeline 生命周期、文字/shape/转场矩阵补齐；约 15 人日 | MG spec/adapter可与 M1.1/M1.2 并行，但接入播放和声画同步必须等 M1.3；最终矩阵依赖 M1.4 合成层契约 | MG 单独 flag；未覆盖元素明确显示 fallback；普通视频播放不依赖 MG |
| M3：资源库合并 | 工作区上传、Tab 顶层修正、旧资产兼容/迁移、parity 文档；约 10 人日 | 可与 M1/M2 并行；上传目标和目录树必须一起切，不能先隐藏旧素材库再建设旧资产映射 | 新 UI flag；后端仍保留旧 `/api/asset/upload/list`；关闭 flag 可回旧入口 |

期内不改 AGENTS #14 的会话状态只由任务终态驱动，也不改 #21 的软着陆→收口→capped 判定单点；视频预览播放/暂停只是 editor-local state，不写任务状态。

## 14. 开放问题（产品拍板）

1. 旧素材 `assets/files` 是默认自动迁移全部文件，还是仅用户点击“导入旧素材”？本 PRD 默认用户显式触发，避免大体积复制与磁盘空间意外。
2. 工作区目录树是否需要直接支持删除/重命名实体文件？现有 workspace remove 默认只删索引，purge 才删文件；本次默认不新增破坏性删除。
3. 多视频轨/叠化时超过 2 路解码预算，产品接受非主层冻结最近帧并显示降级提示吗？本 PRD默认接受，保证主画面播放连续。
4. MG 预览差异角标是否默认常显，还是仅遇到 `unsupported/approximate` 项显示？默认仅有差异时显示，整体预览头保留“浏览器近似预览”。
5. 历史 Tab 的旧资产若文件不在当前工作区，是否允许跨工作区引用，还是只保留历史名称/不可再加入时间线？默认显示外部引用并可继续引用旧路径，不自动移动文件。

## 证据索引与约束

- 前端预览/时钟：`J:\Clipweight-Client\src\features\preview\PreviewPanel.tsx:168-207,396-522,983-1037,1129-1178,1353-1438`；媒体管理 `src\services\media\mediaManager.ts:70-189,300-344,397-420`；Store `src\stores\previewStore.ts:4-43,45-85`。
- MG：后端 `J:\Clipwright\clipwright\animation\mg_renderer.py:31-45,233-307,405-448`、`mg\generator.py:212-245`、`mg\validator.py:7-12,89-169`、`api\animation.py:14-22,119-167`；前端 `mgHtmlPreview.ts:1-13,23-89`、`timeline\engine\easing.ts:186-225`。
- 资源库：前端 `AssetPanel.tsx:21-30,111-123,197-239,276-305`、`WorkspaceMediaTree.tsx:8-9,43-66`、`assetStore.ts:5-15,28-53`、`services\api\asset.ts:49-79`、`services\api\workspace.ts:149-190`；后端 `api\asset.py:90-128`、`services\asset_manager.py:90-120`、`api\workspace.py:240-294`。
- 约束：`J:\Clipweight-Client\AGENTS.md:69-76,119-138`；前后端 parity `J:\Clipwright\docs\frontend-backend-parity.md:31-32,144-149,342-354`。API 增量需按 parity 文档路由 inventory + 客户端矩阵更新，不把历史手抄数量当真实端点事实。
