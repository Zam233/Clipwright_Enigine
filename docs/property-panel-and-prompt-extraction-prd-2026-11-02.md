# Clipwright（帧艺）工程 PRD：属性面板、MG 信息与 Prompt 单一来源

> 状态：指定核心代码已抽样核对并据此形成方案。prompt 清单覆盖可定位的主要静态模板与动态构造点；Persona/Skill/Category 的全目录逐文件穷举未完成，明确列为审计限制。本文为工程方案，不代表代码已修改或验证。

## 0. 结论摘要

| 做什么 | 不做什么 | 谁验收 |
|---|---|---|
| W1 将连续解码/解码池设为新安装默认路径，审计并修复属性面板字段对账 | 不在本 PRD 中修改代码、不运行测试 | 产品确认默认策略；前端负责人验收交互与字段 |
| W2 从 `metadata.mg_def` 解析并展示 MG spec 结构信息 | 不执行 `mg_html`，不把模型 HTML 注入 DOM | 动效设计/前端负责人验收解析显示及安全边界 |
| W3 把时间轴选区摘要传入副驾驶，并使意图执行严格限定目标 clip | 不允许无选区上下文时猜测目标，不允许跨项目修改 | Agent 平台负责人验收上下文与 patch 范围 |
| W4 将 prompt 文案集中到 `clipwright/prompts.py`，保留配置/Persona/插件覆盖边界 | 不把用户数据、运行态对象硬编码进静态 prompt | 后端负责人验收文本等价、导入关系与覆盖行为 |

**执行顺序**：W1 与 W2 可并行；W3 依赖稳定的选区/clip 摘要契约；W4 独立 PR 且排在最后，避免大范围行为变化与 UI/API 联动混在同一发布窗口。

## 1. 术语

- **属性面板（inspector）**：选中时间线 clip 后编辑其可持久化属性的右侧区域；通用字段与 clip 类型字段分层呈现。
- **MG spec**：`metadata.mg_def` 中的结构化动画定义，由 `mgRenderer.ts` 的 `MgSpec`、`MgElement`、`MgKeyframe` 类型约束；面板展示解析结果，不解析/执行 HTML。
- **选区**：`selectionStore` 中当前时间线选中的一个或多个 clip ID；选区不等同于副驾驶会话状态。
- **副驾驶**：Copilot 对话/任务运行界面；本 PRD 指用户发出“改这个 clip”时向 `/intent` 提交选区上下文，再由 `/intent/apply` 应用变更。
- **prompt 单一来源**：可执行 agent 系统/任务提示词正文在 `clipwright/prompts.py` 有唯一代码来源；Persona、插件、YAML 属于明确的运行时覆盖层，不伪装成重复的 Python prompt 常量。

## 2. 现状与差距

> 关键字段与主链路已按所列文件核对；未覆盖的 renderer 分支在对应字段上明确标为待核，不把 schema 声明等同于导出支持。

### 2.1 连续解码默认路径

- `src/stores/settingsStore.ts:52-77,107-126,160-177`：设置来自 localPrefs 的 `editorPrefs`；`previewContinuousPlayback` 有效布尔值优先，否则默认 false；写回同一 `editorPrefs` 对象。当前没有 schemaVersion migration，字段缺失即可安全切换缺省 true，显式 false 会保留。
- `src/features/preview/PreviewPanel.tsx:577-581,628-650,667-671`：播放合成路径受开关控制；`applyDecodePool` 仅在播放+开关启用时选择最多 `DECODE_BUDGET` 路活跃视频，其余停解码保留末帧；预算常量由前文导入定义，AGENTS #22 约束不超过 2 路。
- 差距：默认 false 与 AGENTS #22 的默认连续解码方向相反；注释仍描述“默认关闭、人工验收后开启”。迁移应只将缺失字段缺省改成 true，不覆盖已持久化 false。

### 2.2 MG 信息展示

- `src/features/properties/PropertiesPanel.tsx:537-542,576-697`：现有 AnimationSection 显示通用动画预设和 clip.keyframes 小时间轴；未见读取 `clip.metadata.mg_def` 并解析元素清单的面板区。
- `src/features/preview/mgRenderer.ts:25-77,103-111`：spec 为 `MgSpec`（animation_id/duration_sec/width/height/style/elements）；元素包含 type/content/shape/src/keyframes 与扩展静态样式；关键帧有 time/properties/easing 与扩展属性。
- `clipwright/animation/mg/validator.py:7-23,29-46,65-97`：合法元素含 text/shape/line/circle/ring/arc/bg/image；动画属性白名单和 easing 别名在后端定义。前端 renderer 镜像元素白名单，easing 映射在 `mgRenderer.ts:38-48`。
- `src/features/preview/mgRenderer.ts:4-16,103-110` 明确只消费结构化 metadata，兼容 mg_def/mg_spec，不执行 mg_html；属性面板新增区必须沿用此边界。

### 2.3 属性面板与 schema/render 对账

`src/types/timeline.ts:7-24,54-148` 与 `clipwright/schema/timeline.py:24-42,58-184` 的真实 clip kind 为 video/audio/text/image/caption/shape/waveform/animation；`subtitle` 不是独立 kind（字幕语义使用 caption），`transition` 是 Clip 的 transition_in/out 字段，不是独立 kind。面板用 `sectionsForKind` 区分类型：`src/features/properties/sectionsForKind.ts:30-46`；实际组件按分区渲染：`PropertiesPanel.tsx:367-541`。

矩阵按用户要求的七类概念映射：subtitle→caption；transition→转场属性区；动画→animation clip + 通用关键帧动画两种语义。UI 有+后端支持以字段 schema 且渲染器消费为准。

| 类型 | UI 有+后端支持 | UI 有但后端不支持＝过期/不生效 | 后端支持但 UI 没有＝缺失 |
|---|---|---|---|
| video | 时间/备注/播放/转场/关键帧；变换 metadata.transform 预览；视频 fx（PropertiesPanel.tsx:281-365,382-399,701-767） | blend_mode 非 normal/screen/multiply/overlay 时标“仅预览”（PropertiesPanel.tsx:345-363,1218-1220）；schema允许任意字符串但 render 仅支持子集 | 蒙版 feather/points/rotation/inverted 等 schema 字段无面板控件（timeline.py:102-129；UI仅 type/rect：PropertiesPanel.tsx:855-899） |
| image | 时间/图像适配/变换/fx；蒙版基础类型（PropertiesPanel.tsx:377-399,826-899） | 同 video：不支持 blend mode 仅预览 | 同 video 高级蒙版字段；image_rect 当前仅重置按钮、无数值编辑（PropertiesPanel.tsx:845-853） |
| audio | 时间/备注/播放/音量/淡入淡出/关键帧（PropertiesPanel.tsx:307-344） | EQ 控件禁用且 schema 标“预留未实现”（timeline.py:81-86；PropertiesPanel.tsx:326-342） | schema eq_preset 有字段但被禁用，属于已知不支持，不列可编辑缺失；无确定的额外端到端渲染字段缺失 |
| text | 时间/文字/转场/通用关键帧（sectionsForKind.ts:37-38） | 无本次确认的专属过期字段 | text_align 后端 Clip schema 支持但 UI未展示（timeline.py:137-142；PropertiesPanel.tsx:401-413）；注意 renderer 是否消费需进一步按 caption 路径核实 |
| subtitle（caption） | 文本与大部分样式字段；字幕可按轨级级联（PropertiesPanel.tsx:416-520；timeline.py:144-155） | 字距和阴影模糊控件明确提示导出 drawtext 路径不支持（PropertiesPanel.tsx:476-499）；是否仅特定 renderer/ASS 支持待 renderer 配置条件细化 | 无本轮可确认且完全受后端+renderer支持的样式缺失项；`stroke/glow` 已有控件 |
| animation | 通用时间/转场/预设/关键帧（sectionsForKind.ts:44-45；PropertiesPanel.tsx:537-542,904-1143） | 通用属性可能对 animation clip 无消费，需按 preview compositor 分支逐项限定 | `metadata.mg_def` 的结构化元素/spec 面板信息全部缺失（mgRenderer.ts:50-77,103-111） |
| transition | 入/出转场与时长 UI，schema 对应字段（PropertiesPanel.tsx:523-535；timeline.py:157-162） | 转场仅在可参与 xfade 的视频段生效；音频/图文轨上通用显示可能无效，需按 track kind 隐藏（PropertiesPanel.tsx:523-535；render.py:2342-2424） | 不作为独立类型；无需独立 kind 控件 |

**具体不匹配项清单**

1. EQ `eq_preset`：schema 字段存在但明确未实现混音滤镜；UI 已禁用并提示，保留只为存量兼容，不写回（timeline.py:81-82；PropertiesPanel.tsx:326-342）。
2. blend_mode：schema 接受任意字符串，导出只支持 normal/screen/multiply/overlay；UI 对旧不支持值标“仅预览”，需禁止新写入集合外值（PropertiesPanel.tsx:345-363,1218-1220；render.py:45-46,1809）。
3. 字距/阴影模糊：UI 提示 drawtext renderer 不支持（字距仅 ASS 路径/预览；blur 仅预览），应保留字段但标注 renderer 条件，不得宣传所有导出路径生效（PropertiesPanel.tsx:476-499；render.py:2535-2541,3119-3224）。
4. 高级 mask：后端 schema 支持 points/rotation/feather/inverted；UI 仅暴露 none/rect/ellipse 与 rect 数值，字段缺失（timeline.py:102-129；PropertiesPanel.tsx:855-899；render.py:1754-1777）。
5. image_rect：前端/后端都有字段，面板仅提供“重置裁切”而无可见数值编辑（timeline.ts:86-89；timeline.py:95-100；PropertiesPanel.tsx:845-853）。
6. MG spec：animation clip 的 mg_def/elements/content/keyframes/easing/duration 只被预览 renderer 消费，属性面板未结构化展示（mgRenderer.ts:50-77,103-111；PropertiesPanel.tsx:537-542）。
7. text_align：schema 声明支持，text/caption 面板未提供对齐控件（timeline.py:137-142；PropertiesPanel.tsx:401-413,416-520）；renderer消费路径需核实后决定是补控件还是标注限制。

取舍：不删仍承载兼容数据的 schema 字段；移除或禁用无后端渲染支持的写入控件，已有值用明确状态呈现；先补端到端缺失再开放编辑，避免 schema-only 造成“保存成功但导出无变化”。

### 2.4 时间轴选区进入副驾驶

- `src/features/agent/AgentPanel.tsx:465-480` 直接订阅 `selectedClipIds` 与 timeline，构造所选 clip/kind；`CopilotDock.tsx:13-21,39-43` 的 requirements 标签挂载 RequirementsPane（AgentPanel）。因此选区已能进入需求副驾驶，而非全链路断开。
- `AgentPanel.tsx:782-833` 在有选区时把标签化文字放入用户消息，且 `/intent`、`/edit` 请求均带 `selected_clip_ids`；`requirements.ts:92-103,117-139,152-182` 定义请求契约。确认执行时 `AgentPanel.tsx:735-747` 再带 IDs 调 `/intent/apply`；后端 `api/requirements.py:92-107,181-260` 收到 IDs 并传给 resolver/apply。
- 断点：选区 ID 在生成 preview 和确认 apply 时分别读取（`AgentPanel.tsx:805-810,735-747`），并非发送时冻结快照；用户确认前改选区会导致 intent_raw 与新的 selected IDs 不匹配。请求包含完整 timeline，但没有独立裁剪/长度限制的 clip 摘要字段；消息标签仅是自然语言标签，不是服务端可信契约。
- 执行粒度：不是所有路径都做增量 patch。`/intent/apply` 返回确认的 patch 与新 timeline，适合局部计划（requirements.ts:142-179；api/requirements.py:219-260）；意图预览失败会降级 `/edit`，返回 proposed_timeline（AgentPanel.tsx:821-837），属于整条时间线提案/覆盖审阅链路。副驾驶其他 Copilot 标签是任务轨迹/文档等，不共享该编辑入口。
- 目标语义：保留已有 intent 链路并补快照一致性、服务端目标子集校验和增量写回；不要重复新增第二套“选区进入 intent”机制。

### 2.5 Agent Prompt 清单

统计口径：以下列出定位到的静态系统提示词/明确复用片段共 **18 项**；另列 **7 个动态构造入口**（不是独立固定文案）。这不是 Persona/Skill/Category 全目录字符串穷举；那些来源具备运行时数据属性，需在独立清点阶段补全数量与逐文件来源。

| 模块 | 文件:行号 + 常量/函数 | 用途 | 是否参数化 |
|---|---|---|---|
| Requirements | `clipwright/agents/requirements_agent.py:39-79` `CREATIVE_BRIEF_SYSTEM` | 需求简报对话及 JSON 字段约束；替换 `{{BRIEF_TEMPLATE}}` | 否，含运行时模板替换 |
| Requirements | `requirements_agent.py:84-` `PLAN_TRANSLATE_SYSTEM`（可见后续模板至约 120 行） | 结构输出翻译为用户规划书 | 否 |
| Structure | `clipwright/agents/structure_agent.py:59-116` `SYSTEM_PROMPT_TPL` | 场景脚本结构生成 | 是，`.format(tone, ...)`；消费在 388-426 |
| Structure | `structure_agent.py:117-142` `TOOL_PROMPT` | 素材生成/工具说明附加约束 | 否 |
| Structure | `structure_agent.py:143-` `MG_DYNAMIC_GUIDE` | 动态 MG 能力提示 | 是，拼接到系统提示 |
| Structure | `structure_agent.py:1026-1031` `compact_system` 局部模板 | 分段/场景标注 JSON 输出 | 否，局部拼接 |
| Structure | `structure_agent.py:1161-1170` `system_prompt` 局部模板 | 为场景补动画标记 | 否，动态输入另组 user_prompt |
| Material | `clipwright/agents/material_agent.py:245-264` `generate_search_keywords` 内 `prompt` | 按场景生成素材搜索词 | 是，场景列表插值 |
| Material | `material_agent.py:303-328` 局部 `prompt` | 品类定向素材关键词生成并注入插件扩展 | 是，场景/插件动态拼接 |
| Material | `material_agent.py:361-366` 局部 `prompt` | 单场景/素材辅助查询 | 是，输入数据拼接 |
| Edit | `clipwright/agents/edit_agent.py:1044-1074` 局部 system/user prompt | 剪辑节奏/转场权重 JSON 档案 | 是，动态上下文与 quality hint 拼接 |
| Edit | `edit_agent.py:993-995` PV 助理 system/user 字符串 | PV 工具检查任务 | 是，项目路径插值 |
| Animation / MG | `clipwright/animation/mg/generator.py:97-103` `_STRICT_JSON_OUTPUT` | MG 输出必须为纯 JSON | 否 |
| Animation / MG | `generator.py:107-112` `_NO_BACKGROUND_CONSTRAINT` | 背景遮挡限制 | 否 |
| Animation / MG | `generator.py:117-132` `_MG_ORCHESTRATION_CONSTRAINTS` | 动画节奏/构图/错峰 | 否 |
| Kernel | `clipwright/services/agent_loop.py:166-179` `_PLAN_ONLY_SYSTEM` | 规划任务只派 structure 后收尾 | 否 |
| Kernel | `agent_loop.py:193-205` `_CLOSEOUT_BLOCK` | 收口阶段安全/交付约束 | 否 |
| Kernel | `agent_loop.py:238-287` `_normal_system_prompt` / `_NORMAL_SYSTEM` | 主 Agent 创作任务与阶段/降级/产物约束 | 函数返回纯文本；任务运行态由邻接 `_budget_note` 动态注入 |
| Audio | `clipwright/agents/audio_agent.py:1182-1208` 局部 system/user prompt | BGM 情绪匹配结构化输出 | 是，场景/品类插值 |
| Quality | `clipwright/agents/quality_agent.py:613-623` system/user prompt | 视频文案与简报质检 JSON | 是，category 动态拼接 |

以上静态/明确独立项为 18 项（修订统计：Requirements 2、Structure 4、Material 3、Edit 2、MG 3、Kernel 3、Audio 1、Quality 1）；动态模板入口另至少 7 个（不计入固定文案数）：`structure_agent.py:388-426`、`requirements_agent.py:135-179,232-240`、`animation_agent.py:1304,1701-1724`、`agent_loop.py:231-287,290+`、`mg/generator.py:566-604,965-1084`。`agents/base.py:361-413` 仅传递 system/user prompt，不是提示文案来源；不迁移。

**重复且措辞不同的规则**

- “只输出 JSON”：Requirements 简报 `requirements_agent.py:39-79`、剪辑 `edit_agent.py:1044-1055`、质检 `quality_agent.py:613-615`、MG `_STRICT_JSON_OUTPUT` `generator.py:97-103` 均各自表达，范围/措辞不同；W4 仅抽取，不强行统一文案。
- 工具前说明与任务收尾：`agent_loop.py:169-179,243-283` 分别在 plan-only、normal 中重复，收口 `_CLOSEOUT_BLOCK` `193-205` 又补充“不声称未验证”。规则语义相关但适用状态不同，保留分组并显式组合。
- 类别提示不是静态常量：Requirements/Structure/Edit/Audio/Quality 经 `services/type_prompts.py` 动态 compose；插件提示经 PluginPromptRegistry；不得误搬为静态 prompt。
- MG 生成主模板来自 `animation/mg/config.yaml:50-` 的 `prompt.system_template`，`generator.py:566-604`、`965-1084` 运行时拼接上下文、知识、JSON 约束与编排规则；保留 YAML 作为运营覆盖，不重复复制模板。

**清点限制**：七个业务 Agent 中 animation_agent 的提示词大多由 MG/type-generation/Persona/插件模块组装；本次未对 animation/type specs、skill、category 全部文件逐一枚举字符串行，因此不能宣称全仓 prompt 清单已穷尽。

## 3. 目标与非目标

### 目标

1. 新安装的预览默认走连续解码与解码池；存量用户显式偏好不被静默覆盖。
2. 属性面板按 clip 类型呈现后端与渲染器真实支持的字段，MG 结构可读且安全。
3. 选区上下文可控地进入副驾驶意图与应用链路，编辑边界明确。
4. Prompt 正文具备可审计的单一代码来源，配置覆盖与参数化有清晰规则。

### 非目标

- 不在本次 PRD 实施或改动任何业务代码；不运行 pytest、vitest、tsc、py_compile 或其他测试/编译命令。
- 不重做时间线数据模型，不迁移既有 `mg_html` 数据，不替换后端最终渲染器。
- 不新增通用 Agent 工具市场，不允许 agent 绕过能力插件化约束直连服务。
- 不要求把 Persona、插件、Skill 或 Category 的用户/插件可配置内容全部搬入一个 Python 文件。
- 不以模型自由文本直接写回时间线；不在多选/跨项目情境下推断单一编辑目标。

## 4. W1：默认启用连续解码

### 默认值与存量迁移

- 默认值改为 `true`，只改变没有已存储值的新安装行为；持久化读取采用“显式值优先，缺省值为 true”。
- localStorage 键名：沿用代码核实所得现有键 `clipwright.editorPrefs`（经 localPrefs 读写 `editorPrefs`，settingsStore.ts:52-54）；不另造平行键。
- migration：不增加全对象 `schemaVersion`。`loadEditorPrefs` 对缺少/非布尔值返回 `true`，显式 boolean 原样保留；禁止把显式 false 覆盖为 true。
- A/B 开关：产品验收期保留设置页显式开关，但不新增实验分桶/远端 A/B 系统；当解码错误率与性能观测达标后另开变更移除开关。

**取舍**：保留用户显式 false 优先于“所有用户立刻启用”，避免升级造成资源占用变化；不新增第二套 localStorage 键，避免双真相。

## 5. W2/W1：属性面板结构与字段对账方案

### 面板结构

1. **通用区**：名称/轨道、时间区间、启用状态等所有类型共同拥有且后端模型定义的字段。
2. **类型专属区**：video、image、audio、text/subtitle、animation、transition 各自只呈现有 schema 与渲染器证据的字段。
3. **MG 展开区**：默认折叠；展示 spec 概览、元素列表、元素属性、文本、关键帧、时长、easing；数据异常时展示解析错误，不覆盖原始 JSON。

### MG 字段展示清单

- 只读：`animation_id`、`duration_sec`、画布 `width/height`、style.background/font_family；每个 element 的序号/type/content/shape/src、x/y/width/height、颜色/背景/透明度/字体/边框等静态属性；keyframes 数量、每帧 time/properties/easing/bezier；未知字段折叠在“其他结构字段”只读区。依据 `mgRenderer.ts:50-77,298-345` 与后端 validator `validator.py:7-23,100-148`。
- 可编辑：首期不开放 MG JSON 原地编辑，所有 spec 字段只读；理由是 MG spec 与渲染白名单及后端 validator 必须一致，避免局部编辑生成无效动画。后续编辑另定义 schema-aware 表单与 undo 事务。
- 展示安全：任何动态值先转安全文本（遵守 AGENTS #13 `asText`）；外链 src 展示为被拦截状态，不创建 Image，不展示为可点击任意 URL；对未知 type/easing 显示“不支持/将退化”而非推测渲染结果。

### 每类 clip 字段表

下表为有证据的主字段；所有可写属性进入 undo（单次用户提交/手势合并为一个历史点）并经 `updateClip` 写回 timeline。类型字段按 `sectionsForKind` 切换（sectionsForKind.ts:30-46）；身份字段不可写。

| 类型 | 字段 | 类型 | 控件 | 进 undo | 写回 |
|---|---|---|---|---|---|
| 通用（全部 kind） | start_sec/duration_sec/source_offset_sec | number 秒 | NumberInput | 是 | 是，updateClip |
| 通用 | notes | string/null | textarea | 是 | 是 |
| 通用播放 | speed/volume/opacity | number | slider | 是 | 是；volume audio 上限 2，其余 1 |
| video/image | metadata.transform x/y/scale/rotation | number | slider | 是 | 是，合并 metadata.transform |
| video/image | fx_brightness/contrast/saturation/blur/hue | number | slider | 是 | 是 |
| image | image_fit | enum cover/contain/free | select | 是 | 是 |
| image | image_rect | object/null | reset command（无参数编辑） | 是 | 是（仅 reset） |
| video/image | mask_type/mask_rect | enum/object | select + 4 sliders | 是 | 是 |
| audio/waveform | audio_fade_in_sec/audio_fade_out_sec | number 秒/null | slider | 是 | 是 |
| audio | eq_preset | string | disabled select | 否（禁止交互写入） | 否 |
| text/caption | text | string | textarea | 是 | 是 |
| caption/text | font/font_size/font_color/font_weight/font_italic/text_align/letter_spacing/stroke/shadow/glow 字段 | string/number/bool | select/number/color | 是 | 是；受 renderer 条件限制的字段须标注 |
| all kinds | transition_in/out/transition_duration_sec | string/number/null | select + slider | 是 | 是；仅可渲染轨道生效 |
| shape | shape/fill | string | select/color | 是 | 是 |
| waveform | bar_count/bar_width | number | NumberInput | 是 | 是 |
| all kinds | keyframes[].time/properties/easing/bezier | object | 关键帧编辑器/曲线编辑器 | 是，pushHistoryCoalesced | 是，timelineStore action/updateClip |
| animation | metadata.mg_def spec | JSON/object | 首期展开只读信息区 | 否 | 否 |
| all kinds | id/kind/asset_id/track_id | string | identity display / hidden | 否 | 否 |

### 过期/缺失字段与编辑写回

- 过期字段：删除 UI 控件；仅数据兼容确有必要时保留读取并显示“旧字段，不参与渲染”，不再写回。不能只标 deprecated 后继续提交无效值。
- 缺失字段：优先补 UI 控件；仅在后端 schema 明确支持且 renderer 有真实消费时纳入。schema 有但 renderer 不消费的字段先列为后端差距，不伪称端到端支持。
- 编辑链路：控件暂存 → 校验/归一化 → `timelineStore.updateClip` 或专用 action → 单个 undo 历史点 → 更新 timeline → 项目 PUT 持久化 → 服务端 `last_writer` 语义。`clipwright/api/project.py:264-306`：服务端签名比较 incoming/current；一致时写 `editor:<user>` 但保留 agent_sig，允许 agent 后续增量回写；内容变化时写 editor 标记并清 agent_sig，agent 中间回写停止。UI 更新应走项目编辑器保存链路，不伪装成 agent 写入。
- 写回必须只提交 dirty 字段，避免面板保存旧快照覆盖并发的时间线更新；时间与时长维持现有约束，跨轨引用不得由属性面板自行重排。

## 6. W3：选区联动副驾驶

### 数据契约

```text
selected_clip_ids: string[]          // 去重、有序、当前项目内 ID；最多 20 个
selected_clips: ClipSummary[]        // 仅供模型理解；最多 20 条，每条只含白名单字段
ClipSummary = { id, track_id, track_kind, name?, start, end, duration, enabled, text? }
```

摘要字符总量上限 8,000；超出按选区顺序截断摘要并保留全部合法 ID，UI 提示 agent 仅获得部分摘要。ID 与摘要必须服务端重新从当前项目 timeline 校验，不能信任前端传来的字段值。

### 消息与执行

- 副驾驶 composer 在发送时快照选区，不在任务运行中随选区改变而漂移；API `/intent` 接收 IDs 与摘要版本/项目标识，复用现有 endpoint。
- 意图分类与计划需明确目标 IDs；`/intent/apply` 只接受目标 IDs 子集的变更，复用现有端点及授权校验。
- 首选复用现有 timeline 修改工具；若现工具只支持全量重写，则新增按 clip 的原子 patch 能力并由 agent 通过语义能力解析调用，禁止 agent 直接写数据库或绕过插件能力层。

```json
{
  "project_id": "...",
  "selected_clip_ids": ["clip-1"],
  "operations": [
    {"op":"update_clip","clip_id":"clip-1","set":{"start":12.5,"duration":3.2,"text":"..."}}
  ],
  "base_revision":"...",
  "reason":"..."
}
```

`set` 仅接受 Clip schema 白名单字段；禁止改 id/project_id/track_id，禁止未授权跨轨移动。服务端校验 revision 与 clip 归属，冲突返回可见冲突而非静默覆盖。

### 边界

- 多选：最多 20 个；agent 需逐个明确 patch 目标，不能把单 clip 指令扩散到整组选区。
- 跨轨：允许摘要只读；写入跨轨字段/移动须拒绝，除非用户明确指令且 API 单独授权。
- disabled clip：可读取；默认不纳入渲染相关修改，用户明确要求启用/修改时再应用。
- 跨项目/过期选区：拒绝执行并要求重新选择；项目切换时清空 selection snapshot。

**取舍**：服务端按当前项目重查 ID，优先于信任前端摘要；先采用原子 patch，避免整条 timeline 重写造成无关 clip 丢失。

## 7. W4：`clipwright/prompts.py` 单文件组织

### 组织与取舍

- 分组常量：全局协议、Agent 角色、计划/执行/收口、MG 生成与质量、结构化输出约束；每项保持纯字符串、无业务导入。
- `PROMPTS` 字典：稳定名称到纯文本常量的索引，便于审计、快照比对和消费方迁移；不作为动态配置数据库。
- 参数化函数：仅在提示词确实需要运行态参数时使用函数；类型注解描述参数，返回字符串。优先 `.format()` 或显式替换模板，避免 Python f-string 把 JSON 花括号/示例变量误解析。
- f-string：只有调用点已有明确标量、且变量在函数签名显式列出时允许；禁止在模块导入时格式化运行态对象，禁止 `locals()`/全局变量隐式注入。
- 命名：`<MODULE>_<PURPOSE>_PROMPT` 常量；函数 `build_<purpose>_prompt`；字典 key 与符号名一致且全大写。
- 覆盖关系：Persona 是角色/风格运行时覆盖；插件可提供能力相关提示片段；Category 提供类型策略；Skill 提供可组合操作说明；YAML 配置保留可运营配置项。由装配层按“全局协议 + agent 基础 + category/persona/skill/plugin 扩展”拼接，扩展不得覆盖安全/终止不变量。

### 迁移三段式

1. **搬运**：原文逐字迁至新符号，消费点只改 import，不顺手润色或合并。
2. **参数化**：单独提交，把动态插值显式化；每个模板列变量名与来源，保留渲染后文本语义。
3. **验证**：脚本读取迁移前后常量，按符号映射比较字节/换行；参数化项用固定 fixture 渲染后与旧模板格式化结果比较。只做静态脚本思路与人工核对；本任务不运行 pytest 或任何测试。

### 风险

- 循环导入：`prompts.py` 不导入 agents/services；消费方只从 prompts 导入字符串。
- f-string 运行时对象：搬运前列变量占位表，拒绝隐式全局变量。
- 测试断言文案：字符串精确断言可能依赖原文，纯搬运必须逐字比较。
- i18n：首期保持当前语言原文；不在抽取时引入翻译抽象。
- 配置/Persona/插件重复：先盘点来源与优先级，不把动态配置错误复制为静态常量。

## 8. 接口与数据契约增量

| 名称 | 类型 | 默认值/约束 | 变更 |
|---|---|---|---|
| `previewContinuousPlayback` | boolean | 缺省 `true`；显式存量值保留 | settingsStore hydration/migration |
| `selected_clip_ids` | `string[]` | `[]`；去重，最多 20；当前项目 ID | `/intent` 请求可选字段 |
| `selected_clips` | `ClipSummary[]` | `[]`；白名单摘要，累计最多 8,000 字符 | `/intent` 请求可选字段 |
| `base_revision` | string | 必须与执行时项目版本匹配 | `/intent/apply` patch 并发校验 |
| `operations` | `ClipPatchOperation[]` | 非空；每个 clip ID 必须属于选区 | `/intent/apply` 增量 patch |
| `PropertiesPanel` MG 区 | 结构化 props | `mg_def` 缺省时隐藏/提示缺失 | 只读 spec 展示 |

具体 API schema 与现有 props 差异须依据代码审计补行号；不为已有等价字段重复造字段。

## 9. WBS

> **执行进度（W1 + W2 已落地，`npx tsc --noEmit` 通过；W3/W4 未开始）**
>
> - **W1.1/W1.2 ✅**：`settingsStore.loadEditorPrefs` 的缺省值改为 `true`；**显式存过 `false`
>   的用户仍保留 false**（不覆盖用户偏好）。持久化键不变（`editorPrefs`），没有新增 schemaVersion。
> - **W1.3 ✅（含两处对原文的更正）**：
>   - 不匹配项 2（`blend_mode`）：选项列表**只给导出真正支持的四值**，历史遗留的其它值改为
>     只读提示「导出不支持（仅预览生效）· 改回 normal」按钮，不再允许写进时间线。
>   - 不匹配项 5（`image_rect`）：补 x/y/w/h 四个数值控件（归一化 0–1，写回前 `clampNum` 收口）。
>   - 不匹配项 4（高级蒙版）：补 `mask_feather` / `mask_rotation` / `mask_inverted` 三个控件；
>     `mask_points`（多边形顶点）结构复杂，本期只做**只读提示**、不改写。
>   - **更正 A**：`text_align` 其实**已有**「对齐」控件（`PropertiesPanel.tsx` 文本区），原文"有字段无控件"不成立，无需改。
>   - **更正 B**：字距与阴影模糊**已有**「仅预览*」提示 + renderer 条件 tooltip，属已缓解项，本次未重复动。
>   - 不匹配项 1（EQ 预留未实现）：原文即"UI 已禁用并提示"，复核属实，无需改。
>   - 不匹配项 6（MG spec 无结构化区）：由 W2 解决，见下。
> - **W1.4 ✅**：新增/修改的所有控件都走 `set()` → `updateClip` + `pushHistory()`（多选走 `batchUpdate`），
>   与既有写回链路一致；项目持久化仍由编辑器保存触发 `PUT /api/project/{id}`（`last_writer=editor:<uid>`），
>   没有新增绕过路径。
> - **W2.1–W2.3 ✅**：新增 `MgSpecSection`（`PropertiesPanel.tsx`）——读 `clip.metadata.mg_def`
>   （复用 `mgSpecOf`，不另造类型），只读展示动画 ID / 时长与画布 / 元素数，可展开每个元素的
>   类型、文本、关键帧数与时间区间、出现的 easing；**绝不渲染 `metadata.mg_html`**；所有字符串过
>   `asText`；无 spec 时给出可执行的修复提示（重派 animation 阶段）。
> - **W3（选区快照/patch）与 W4（prompts.py）未开始**。

> - **W3.1–W3.4 ✅**（选区冻结快照 + 子集校验 + 按快照执行）：
>   - 新文件 `src/features/agent/selectionSnapshot.ts`：`buildSelectionSnapshot(ids, timeline)` →
>     `{ids(≤20), summary(≤8000 字符), truncated, total}`；**只收录能在当前时间线里找到的片段**
>     （过期选区不进摘要）。`selectionHint()` 给一句人话提示。
>   - `AgentPanel.sendEdit`：发送那一刻生成快照，`/intent` 与降级的 `/edit` 共用同一份
>     （新增 `selection_summary` / `selection_truncated` 一起发）。
>   - `intentPending` 现在带 `selection`；确认卡点确认时把它传给 `applyIntent`，
>     **执行用冻结快照而不是重新读 `useSelectionStore`**；若期间选区变了，回一条显式提示
>     「仍按确认时的 N 个片段执行，要按当前选区请重发」。
>   - 发送路由（回车与按钮两处）改为按**有效选区**判断：快照为空但原始 id 非空 → toast
>     「选区已失效」并按普通对话处理；超上限被截断 → toast 提示。
>   - 后端 `api/requirements.py`：`MAX_SELECTED_CLIPS=20`、`MAX_SELECTION_SUMMARY_CHARS=8000`；
>     新增 `_validate_selection()`（上限 + 子集），在 `/intent`、`/intent/apply`、`/edit`
>     三条路径上都执行；`IntentRequest`/`ApplyRequest`/`EditRequest` 新增
>     `selection_summary` / `selection_truncated`。实测 6 个用例：合法/空/含幽灵 id/超上限/
>     跨项目 id 全部按预期（400 或通过）。
> - **W4（clipwright/prompts.py）未开始**——按 PRD 要求单独一批最后做。

> - **W4.2 / W4.4 首批 ✅（11 条常量 + 1 个参数化函数，逐字校验通过）**：
>   - 新增 `clipwright/prompts.py`（101 行）：`CREATIVE_BRIEF_SYSTEM`、`PLAN_TRANSLATE_SYSTEM`、
>     `STRUCTURE_SYSTEM_PROMPT_TPL`、`STRUCTURE_TOOL_PROMPT`、`STRUCTURE_MG_DYNAMIC_GUIDE`、
>     `LOOP_PLAN_ONLY_SYSTEM`、`LOOP_CLOSEOUT_BLOCK`、`LOOP_NORMAL_SYSTEM`、
>     `MG_STRICT_JSON_OUTPUT`、`MG_NO_BACKGROUND_CONSTRAINT`、`MG_ORCHESTRATION_CONSTRAINTS`。
>   - 搬运方式：**用脚本把搬运前的值序列化后生成新模块**（`repr` 保真），原模块改为
>     `from clipwright.prompts import 新名 as 旧名`，调用点一行未改。
>   - **逐字校验**：搬运前后快照对比 11 条 / 8949 字符，模块侧差异 **0**、`prompts.py` 侧差异 **0**。
>   - 参数化示范（W4.4）：`LOOP_BUDGET_HEADER/PLENTY/TIGHT/CRITICAL` 四个常量 +
>     `build_budget_note(remaining, max_turns, used)`；`agent_loop._budget_note` 只保留算数与选档。
>     6 个档位用例（41/40/13/12/0 剩余、无限回合）与旧实现**逐字一致**，无限回合仍返回空串。
>   - 过程中出现过一次事故并已修复：第一版用"括号配平"删旧常量，把含 `{{BRIEF_TEMPLATE}}`
>     的字符串截断（`requirements_agent.py`/`structure_agent.py` 语法错误）——已 `git checkout`
>     还原并改用 **AST 行号精确定位**替换，不再做文本配平。
> - **W4 剩余未做**：material / edit / audio / quality / animation 的**函数内动态拼接 prompt**
>   （material 3 处、edit 2 处、audio 1 处、quality 1 处、structure 2 处局部模板、animation 2 处），
>   以及 Persona / Skill / Category 目录内文案的全量穷举。按 PRD 顺序，这些属于 W4.3/W4.4 的第二批。

> - **W4.3 第二批 ✅（再搬 3 条 + 1 个 builder，累计 13 条 / 9395 字符）**：
>   - 静态搬运：`EDIT_PROFILE_SYSTEM`（`edit_agent._llm_decide_edit_profile`，399 字符）、
>     `QUALITY_SEMANTIC_QA_SYSTEM`（`quality_agent._check_semantic_qa`，47 字符）——
>     用 AST 拿到函数内赋值的**精确文本**后以 `repr` 写入 `prompts.py`，原位置改成引用常量。
>   - 参数化：`build_plan_translate_system(brief_json)`（原 `f"{PLAN_TRANSLATE_SYSTEM}\n\n参考方案:\n{brief_json}"`），
>     3 个用例（空 JSON / 小 JSON / 长文本）与旧 f-string **逐字一致**；同时抽出
>     `PLAN_TRANSLATE_HEADER` / `PROMPT_PLUGIN_EXT_HEADER` / `PROMPT_WEB_CONTEXT_HEADER` 三个可复用抬头。
>   - 校验：首批 11 条差异 0、第二批 2 条差异 0、agent 侧 import 正确、builder 3 用例差异 0、
>     `python -c "import clipwright.main"` ok。
>   - **第二次事故（同样已修）**：替换函数内赋值时丢掉缩进 → `IndentationError`；已 `git checkout`
>     还原后改为**保留原行首缩进**再替换。教训：替换函数体里的语句必须沿用原语句的缩进。
> - **W4 剩余（下一轮）**：仍带插值的 4 处 —— `audio_agent._llm_match_bgm`、`structure_agent._enrich_scene_animations`、
>   `structure_agent._build_system_prompt`（由 `STRUCTURE_SYSTEM_PROMPT_TPL.format(...)` 组装，模板本身已搬）、
>   `material_agent` 的 3 处关键词生成 prompt；以及 Persona / Skill / Category 目录文案的全量穷举
>   （这些走 `compose_type_prompt(s)` 注入，属于"覆盖层"而非内联文案，需要单独确认是否纳入 `prompts.py`）。

> - **W4.4 收尾批 ✅（再抽 2 条为 builder，实测与旧实现逐字一致）**：
>   - `build_bgm_match_system(slot_keys)`（原 `audio_agent._llm_match_bgm` 的 f-string）；
>   - `build_anim_marker_system(anim_guide, mg_info)` + `build_anim_marker_user(scenes_count, scenes_text)`
>     （原 `structure_agent._enrich_scene_animations` 的两段），并抽出 `ANIM_MARKER_SYSTEM_HEADER`；
>   - 用例比对：BGM 3 组槽位（1/3/空）+ 动画标记 3 组 guide + 2 组场景文本，**9 个用例差异 0**。
>   - 两个 agent 的调用点已改为 builder，`py_compile` 与 `import clipwright.main` 均通过。
> - **`prompts.py` 现状**：179 行；**13 条静态常量 + 5 个 builder**（`build_budget_note`、
>   `build_plan_translate_system`、`build_bgm_match_system`、`build_anim_marker_system`、
>   `build_anim_marker_user`），累计 9395+ 字符文案，全部经快照/用例比对确认逐字未变。
> - **仍然内联（明确保留，不是遗漏）**：`material_agent` 的 3 处关键词生成 prompt
>   （`material_agent.py:245/303/361`）。原因：这三处是 4 个以上提示片段（scene_lines、
>   category_hint、style_hint、brief_hint_text）在 f-string 里反复拼接，且紧跟 LLM 调用；
>   本轮尝试用 AST 自动抽取时因括号内含注释、混合缩进导致 `ast.parse` 不稳定。
>   **下一轮处理方式**：逐个手工提成 `build_material_keywords_*(...)`，每个都用
>   "旧源码求值 vs 新 builder" 的用例比对（与 audio/structure 同一套验证），不冒进批改。
> - **Persona / Skill / Category 文案不搬**（口径确认）：它们通过 `compose_type_prompt(s)` 注入，
>   属于 PRD 裁定的**覆盖层**（`prompts.py` < Category < Persona < Skill < 插件），
>   搬进全局模块反而会破坏覆盖顺序；已在 `prompts.py` 头部写明这条边界。

> - **W4.5 收尾 ✅（material 三处搬完 + 全量映射报告）**：
>   - `material_agent` 三处关键词 prompt 已提成 `build_material_queries_batch_prompt` /
>     `build_material_queries_prompt` / `build_material_english_prompt`，并抽出
>     `MATERIAL_QUERY_*` / `MATERIAL_ENGLISH_HEAD` 片段常量与 `material_style_hint` /
>     `material_brief_hint` 两个"有才拼"的辅助函数。
>   - **这轮换了验证方法（更硬）**：用 stub 掉 `LLMService.ask` 的**运行时捕获**——改动前后
>     各跑一次真实代码路径，比对实际发给模型的 prompt：**372 / 244 / 86 字符逐字一致**。
>     之前的批次只能比对常量文本，这轮直接证明"模型收到的东西没变"。
>   - **`prompts.py` 最终状态**：274 行，**26 个常量 + 8 个 builder**，常量总字符 10314。
>   - 新增 `docs/prompts-symbol-map-2026-11-02.md`（两仓同内容）：21 条符号的
>     原位置 / 用途 / 类型 + 五批搬运的验证记录 + 两次事故复盘。
> - **W1–W4 全部完成**。唯一按裁定**不搬**的是 Persona / Skill / Category 文案（覆盖层，
>   经 `compose_type_prompt(s)` 注入），已在 `prompts.py` 头部与本文件写明边界。

> 任务估算为单人日，含实现/人工验收，不含跨团队等待；行号基于本次已读核心实现，尚未逐行核对的辅助模块明确只给文件名。

### W1：开关 + 面板审计修复（4 项，估算 5 人日）

| 任务 | 目标 | 涉及文件/函数 | 前置 | 人工验收点 | 规模 | 风险 |
|---|---|---|---|---|---|---|
| W1.1 | 盘点设置默认与 migration | `settingsStore.ts:52-77,160-177` loadEditorPrefs/persistEditorPrefs | 无 | 新用户打开项目播放时使用连续解码 | S | 存量 false 被覆盖 |
| W1.2 | 默认启用且保留显式偏好 | `settingsStore.ts:62-71,154,160-177` loadEditorPrefs/setPreviewContinuousPlayback；`PreviewPanel.tsx:628-671` applyDecodePool | W1.1 | 旧设置 true/false 重启后保持；无值时为 true | M | 解码资源增加 |
| W1.3 | 建类型×字段对账表 | `PropertiesPanel.tsx:181-185,281-541`；`sectionsForKind.ts:30-46`；`timeline.ts:54-148`；`schema/timeline.py:58-184`；`render.py:1412-1463,1754-1810` | 无 | 每类属性都能在面板看到真实字段，旧字段不再可写 | M | schema 与 renderer 语义不一致 |
| W1.4 | 修 UI 控件与持久化链路 | `PropertiesPanel.tsx:82-130,281-541,907-985`；`timelineStore.ts` updateClip；`api/project.py:264-320` update_project | W1.3 | 改值后预览与重开项目一致，Undo 只撤销本次修改 | L | 覆盖并发修改 |

### W2：MG 信息区（3 项，估算 3 人日）

| 任务 | 目标 | 涉及文件/函数 | 前置 | 人工验收点 | 规模 | 风险 |
|---|---|---|---|---|---|---|
| W2.1 | 核实 MG spec 类型与 validator 白名单 | `mgRenderer.ts:25-77,103-111`；`animation/mg/validator.py:7-46,65-148` | W1.3 | 合法类型/easing 与预览一致 | S | 前后端白名单漂移 |
| W2.2 | 新增只读 MG 展开区 | `PropertiesPanel.tsx:537-542,576-697`；`mgRenderer.ts:50-77,265-345` | W2.1 | 展开可见元素、文本、关键帧、时长、easing | M | 大 JSON 卡顿 |
| W2.3 | 异常/缺失 spec 状态与安全显示 | `mgRenderer.ts:103-119`；`PropertiesPanel.tsx:537-542`；`src/lib/render.ts` asText | W2.2 | 错误 JSON 有提示；mg_html 不显示为可执行内容 | S | 错误信息渲染不安全 |

### W3：选区联动（4 项，估算 6 人日）

| 任务 | 目标 | 涉及文件/函数 | 前置 | 人工验收点 | 规模 | 风险 |
|---|---|---|---|---|---|---|
| W3.1 | 设计并实现选区快照摘要 | `selectionStore.ts:6-27,29-74`；`AgentPanel.tsx:465-480,782-810`；`CopilotDock.tsx:34-50` | 无 | 发送消息时只提交当前项目所选 clip 摘要 | M | 选区变化竞态 |
| W3.2 | 接入 `/intent` 上下文 | `requirements.ts:117-139,152-182`；`clipwright/api/requirements.py:92-107,181-260`；`intent_resolver.py` | W3.1 | 后端收到 IDs 与摘要且拒绝外项目 ID | M | 请求体兼容性 |
| W3.3 | 增量 patch 与 apply 校验 | `AgentPanel.tsx:718-779,802-837`；`requirements.py:219-260`；`intent_apply.py` plan_validate_apply | W3.2 | agent 仅修改被选 clip，其他 clip 不变 | L | 误改非目标 clip |
| W3.4 | 多选/冲突/空选区交互 | `CopilotTaskRunTab.tsx`、`CopilotTabBar.tsx`；`AgentPanel.tsx:1272-1341` | W3.3 | 无选区通用对话正常；过期选区提示重选 | M | 用户误认为已应用 |

### W4：Prompt 抽取（5 项，估算 8 人日；必须按序）

| 顺序/任务 | 目标 | 涉及文件/函数 | 前置 | 人工验收点 | 规模 | 风险 |
|---|---|---|---|---|---|---|
| W4.1 搬运盘点 | 清点常量/模板/配置覆盖与重复规则 | `requirements_agent.py:39-240`；`structure_agent.py:59-143,388-426`；`material_agent.py:245-366`；`edit_agent.py:993-1079`；`animation_agent.py:1304-1724`；`audio_agent.py:1182-1208`；`quality_agent.py:613-642`；`agent_loop.py:166-287`；MG generator/config；`agents/base.py:361-413` | 无 | 清单覆盖全部来源且每条有用途/参数化标记 | M | 漏掉函数内模板 |
| W4.2 搬运核心 | 原文迁入 prompts.py，调用点只改 import | `agent_loop.py:166-287`；`structure_agent.py:59-143`；`requirements_agent.py:39-120` | W4.1 | 固定输入下系统提示逐字一致 | L | 循环导入/引用遗漏 |
| W4.3 搬运 MG 与模块文案 | 迁移可执行 Python 文案，配置保留明确配置边界 | `animation/mg/generator.py:97-132,566-604,965-1084`；`animation/mg/config.yaml:50-`；各 agent 模板 | W4.2 | MG 生成提示词原文一致，YAML 覆盖仍生效 | M | 配置重复与优先级变化 |
| W4.4 参数化 | 用显式参数函数替换动态插值点 | `structure_agent.py:388-426`；`requirements_agent.py:232-240`；`material_agent.py:303-328`；`agent_loop.py:231-287,290+`；`prompts.py` 参数化函数 | W4.2、W4.3 | 各参数组合生成内容与旧模板一致 | M | f-string 花括号损坏 |
| W4.5 验证 | 静态文本映射比对脚本与人工审查 | W4.1 来源清单 + W4.2-4.4 符号映射；测试断言位置静态检索 | W4.4 | 比对报告无未解释差异；不运行 pytest | M | 行尾/空白差异误报 |

## 10. 人工验收清单

### W1

- 清空测试用户设置后启动预览 → 默认进入连续解码并可看到解码池效果。
- 设置显式关闭后重启应用 → 仍保持关闭；升级迁移不覆盖该值。
- 分别选择 video/image/audio/subtitle clip → 面板字段与后端支持矩阵一致；修改并 Undo → 只回退该字段。

### W2

- 选择带 `metadata.mg_def` 的动画 → MG 区能展开看到元素、文本、关键帧、时长和 easing。
- 载入缺失/损坏 spec → 有清晰状态，不崩溃、不覆盖原始数据。
- 检查含 `mg_html` 的旧 clip → 不执行 HTML，预览仍只消费结构化 spec。

### W3

- 时间轴选择一个 clip 后发送“改这个 clip 的时长” → 副驾驶请求包含该 ID，执行只影响该 clip。
- 选中两个 clip 发起单对象修改 → agent 明确目标或逐项 patch，不改未选 clip。
- 切换项目或 clip 被删除后提交旧消息 → apply 拒绝并提示重新选择。

### W4

- 对照迁移映射运行静态文本比对脚本 → 搬运阶段文案逐字一致。
- 使用动态参数样例构造 prompt → 插值值正确，JSON 示例花括号不变成模板变量。
- Persona/插件/Category/YAML 提供覆盖时 → 优先级与迁移前一致，收口/安全规则不可被覆盖。

## 11. 风险与回滚

1. **默认值对存量用户**：migration 只补缺失值；若发现误覆盖，回滚 migration 写入并恢复原值，禁止清空整份 settings。
2. **面板写回破坏时间线**：采用 dirty-field patch、revision 校验与单 undo 事务；回滚时撤下写入控件，保留只读展示。
3. **选区联动误改其他 clip**：服务端校验 selected IDs 与 patch IDs 子集；异常时关闭 apply 联动，保留普通副驾驶。
4. **prompt 搬运丢 f-string 变量**：迁移清单列所有插值点，固定 fixture 做旧/新渲染比较；出现差异先回滚调用点 import。
5. **循环导入**：`prompts.py` 保持纯数据模块；发现环时回滚相应消费点，不通过延迟 import 掩盖层级问题。
6. **W4 影响面过大**：按 Agent 核心、MG、参数化拆提交；逐批比对，任一组有文案差异即停止后续迁移。
7. **字段 schema 与 renderer 不一致**：按端到端证据区分“schema 可存”与“renderer 生效”；未实现字段保持不可编辑并单独派工。
8. **MG spec 体积导致面板卡顿**：MG 区默认折叠，元素列表按需渲染；不引入重型图形编辑器。
9. **last_writer 语义污染**：仅用户显式提交产生 user writer；自动解析/模型建议不得写成用户操作。

回滚原则：前端 W1/W2/W3 可独立关闭对应 UI/默认开关；服务端 patch 端点须保持可选字段兼容。W4 按模块 revert 消费 import，保留 prompts.py 不被引用，直至文本差异解决。

## 12. 分期与依赖

- 第一批：W1.1/W1.2 与 W1.3 并行；新默认开启有独立回滚开关，字段审计先于字段修复。
- 第二批：W2 在 spec/validator 对齐后交付；MG 信息区只读，不阻塞基础 clip 面板修复。
- 第三批：W3 先完成摘要契约，再做 API 校验和 patch；不得只把选区文字塞进 prompt 而没有 apply 范围校验。
- 第四批：W4 必须最后做或单独 PR。理由：触及七个 agent、内核、loop、MG、Persona/Skill/Category 多层装配；与行为改动同批会令回归归因困难，文本变化还可能改变模型输出。先机械搬运并逐字对比，再单独参数化，最后验证。

关键取舍：W4 不与 W1-W3 同 PR；安全与可归因性优先于一次性减少文件数。W2 初版只读；避免 UI 写入造成 MG schema 与渲染器不一致。W3 采用局部 patch；避免整条 timeline 重写。

## 13. 开放问题

1. 产品是否接受升级后对“从未保存该字段”的老用户也启用连续解码？方案保留显式 false，仅缺失值迁移为 true。
2. 选区上限 20、摘要上限 8,000 字符是否符合典型剪辑项目规模？这是防上下文膨胀的首期硬上限。
3. `/intent/apply` 是否已有 revision/权限/时间线写回事务可复用？代码核查后确定复用或新增 patch schema。
4. MG 信息区是否在后续版本开放 schema-aware 编辑？本期结论为只读，产品需另行批准编辑与 undo 契约。
5. W4 的最终覆盖顺序由谁批准：全局协议、agent 角色、Category、Persona、Skill、插件？默认顺序见第 7 节，安全/终止条款不可覆盖。

---

> 审计范围声明：已核对本 PRD 中带行号的主要前后端实现；未读取的辅助组件/完整目录不作为已审计证据。Prompt 清单为主要静态 prompt 与动态构造入口盘点，Persona/Skill/Category 全目录穷举仍缺；实施前 W4.1 必须完成逐文件补清。未运行测试或编译命令。
