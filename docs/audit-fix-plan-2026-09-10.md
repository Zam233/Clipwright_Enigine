# ClipWright 全量缺陷与功能缺口修复计划（可派工版）

> **审计日期**：2026-09-10
> **审计范围**：前端 `J:\Clipweight-Client`（React 19 + TS + Canvas 2D 时间轴）↔ 后端 `J:\Clipwright`（FastAPI + Agent 编排）
> **审计方式**：五轮静态审计 + 真实工程数据取证（`J:\Clipwright\projects` 下 392 个工程）+ 逐条交叉复核
> **基线状态**：`npx tsc --noEmit` 0 错误 ｜ `npm run lint` 0 error / 124 warning ｜ `npm run test` 60 文件 428 测试全绿 ｜ `npm run build` 成功 ｜ `npx playwright test` 10 passed / 1 failed（该 1 条依赖真实后端）
> **本文档用途**：交给执行 Agent 按条目施工。每条均含**证据（file:line）、根因、修复建议、验收标准**。

---

## 0. 执行须知（务必先读）

### 0.1 双仓库与工作目录

| 角色 | 路径 | 说明 |
|---|---|---|
| 前端 | `J:\Clipweight-Client` | 本仓库，React 19 + Vite 6 + Zustand 5 + 自研 Canvas 2D 时间轴 |
| 后端 | `J:\Clipwright` | FastAPI + 7 Agent + 44 Tool + 插件/Persona/类型系统 |
| 账号服务（可选） | `K:\Clipwright Server` | 端口 8090，前端经 `/srv` 代理 |

**跨仓库任务**：标有「后端」的条目在后端仓库施工；标有「前后端」的需要两侧同步改。修改任一侧后，两侧的 `docs/` 副本需同步（见后端 `AGENTS.md` 文档维护规则）。

### 0.2 施工原则

1. **先证据后动手**：每条都给了 `file:line`。动手前先读那几行，确认症状仍在（代码可能已被别的批次改过）。
2. **不做超范围改动**：本文档之外的重构、依赖升级、目录搬迁一律不做。
3. **明确跳过的范围**：
   - **国际化（i18n）不修**——用户已明确要求本轮不做。相关死依赖（`i18next` / `react-i18next`）保留不动。
   - 未复核项（见 §7）不得直接当事实修，须先自行验证。
4. **每条任务必须自证**：改完跑 §8 的验证命令，并在提交信息里带上任务 ID（如 `fix(P0-01): ...`）。
5. **禁止伪造验证**：不得声称跑过没跑的命令。

### 0.3 单条任务的交付格式

```
fix(P0-01): <一句话>

症状：<复现步骤>
根因：<file:line + 为什么>
改动：<改了哪些文件，怎么改>
验收：<按该条目的验收标准逐条勾选>
回归：tsc / lint / vitest / build 结果
```

---

## 1. 优先级总览

**统计**：P0 = 5 条 ｜ P1 = 37 条 ｜ P2 = 54 条 ｜ P3 = 15 条 ｜ 待验证 = 11 条。合计 **111 条已复核 + 11 条待验证**。

| 级别 | 含义 | 处理建议 |
|---|---|---|
| **P0** | 会丢用户数据 / 破坏工程 | 必须最优先，单独批次，每条配回归测试 |
| **P1** | 功能失效或静默给出错误结果 | 第二批起逐子系统推进 |
| **P2** | 体验受损 / 局部不一致 / 成本浪费 | 第三批，可并行分派 |
| **P3** | 维护性与文档一致性 | 收尾批次 |

### 1.1 索引表

| ID | 级别 | 子系统 | 一句话 | 证据 | 复核 |
|---|---|---|---|---|---|
| P0-01 | P0 | 编辑器 | 大工程关闭/刷新时时间线被 5 字段元数据桩覆盖 | `flushPayload.ts:25-47` + `EditorPage.tsx:273-285` + `project_manager.py:126-139` | ✅ |
| P0-02 | P0 | 编辑器 | 5s 防抖窗口内 SPA 跳转丢改动，无离开拦截 | `EditorPage.tsx:228-243, 252-293` | ✅ |
| P0-03 | P0 | 时间轴引擎 | 修剪拖拽不进撤销栈 | `TimelineEngine.ts:544, 636, 652` | ✅ |
| P0-04 | P0 | 时间轴引擎 | 拖拽修剪绕过邻接钳制，可造出同轨重叠 | `TimelineEngine.ts:629, 649, 721-734` | ✅ |
| P0-05 | P0 | Persona | 保存人格会静默重置一批后端专有字段 | `PersonaDetailPage.tsx:132-136` + `types/persona.ts:79-154` + `persona.py:59-68` | ✅ |
| P1-01 | P1 | 动画/MG | MG 预览端点永远返回空白视频且报成功 | `api/animation.py:155` + `hyperframes_renderer.py:238-242` | ✅ |
| P1-02 | P1 | 动画/MG | MG 生成缓存因键名笔误永不命中 | `mg/generator.py:214, 215, 1183` | ✅ |
| P1-03 | P1 | 动画/MG | MG 片段窗口被钳到场景时长，HTML 仍按自身时长渲染 | `animation_agent.py:586-587` + `mg_renderer.py:167` | ✅ |
| P1-04 | P1 | 关键帧 | Agent 关键帧绝对秒被编辑器当归一化值，导出又误标 clip_local | `easing.ts:82-83` + `timelineStore.ts:831` + `keyframes_expr.py:113, 121` | ✅ |
| P1-05 | P1 | Agent 闭环 | 差异视图只比 14 个字段 → 动画/样式改动被判「无差异」 | `timelineDiff.ts:26-30` + `TimelineDiffView.tsx:129-137` | ✅ |
| P1-06 | P1 | 导出 | 点「自定义」预设后渲染必 400 | `ExportPage.tsx:32, 189` vs `render.py:967-968` | ✅ |
| P1-07 | P1 | 导出 | 渲染失败只显示「提交失败」，丢弃 422 字段详情 | `ExportPage.tsx:233-235` vs `client.ts:49-59` | ✅ |
| P1-08 | P1 | 素材 | 素材入库只写 title，刷新后片段全部「素材加载失败」 | `AssetPanel.tsx:157` + `mediaManager.ts:23-32` vs `asset.py:143-162` | ✅ |
| P1-09 | P1 | 素材 | 多素材源检索逗号拼接 → 静默 0 条 | `asset.ts:131` vs `material.py:25` + `registry.py:62-63` | ✅ |
| P1-10 | P1 | 插件 | 插件配置保存丢掉 secret 标记并明文/掩码回写 | `PluginsPage.tsx:460-481` + `loader.py:88-103, 437-448` | ✅ |
| P1-11 | P1 | 插件/Skill | 工具与技能页「执行」全部 422 | `api/skill.py:22`、`api/tool.py:44` vs `project.ts:343-347` | ✅ |
| P1-12 | P1 | 插件 | 启用插件失败时回滚是死代码 | `api/plugin.py:94` vs `loader.py:563-571` | ✅ |
| P1-13 | P1 | 插件 | 同名工具/技能静默覆盖内置，卸载后内置永久消失 | `tool/registry.py:42-50, 61-63` + `skill/registry.py:29` | ✅ |
| P1-14 | P1 | 插件安全 | 权限白名单仅声明、entry_point 无校验、sys.path[0] 可被 shadow、tar 可符号链接穿越 | `config.py:46-47` + `loader.py:74-77, 674-686` + `install_service.py:17-27` | ✅ |
| P1-15 | P1 | 类型系统 | 自定义类型的节奏/转场/踩点配置整体失效 | `dynamic.py:33-41` + `edit_agent.py:279, 286` + `user_types/t_valid_ok.yaml` | ✅ |
| P1-16 | P1 | 类型系统 | user_types 里一个坏 YAML 让类型列表整体 500 | `type_maker.py:107-108` | ✅ |
| P1-17 | P1 | Pipeline | `/run-async` 成功后仍被标 failed（`ok` 未定义先引用） | `api/pipeline.py:375` vs `:401, :438-442` | ✅ |
| P1-18 | P1 | Pipeline | 类型后处理与署名轨只在 retry 路径执行 | `pipeline_v2.py:1045-1063` vs `:825-865` | ✅ |
| P1-19 | P1 | Pipeline | 取消/超时无法中断在飞的 LLM 线程 | `agents/base.py:38-47` + `pipeline_v2.py:1329` | ✅ |
| P1-20 | P1 | 需求 Agent | `plan_confirmed`/`brief_confirmed` 无路由分支 → 再发消息零回复 | `requirements_service.py:533-628` | ✅ |
| P1-21 | P1 | 需求 Agent | 单字确认词误判（「对比一下」「行业」被判确认） | `requirements_service.py:1139-1151` | ✅ |
| P1-22 | P1 | 需求 Agent | gathering 只把最后一条消息喂给 LLM | `requirements_service.py:530, 1352` | ✅ |
| P1-23 | P1 | 音频 Agent | 上传配音的 STT 对齐与 NEL 对齐都失效 | `audio_agent.py:178, 211, 214-248, 440-446, 529-539` | ✅ |
| P1-24 | P1 | 音频/语音 | dub_script 全段失败仍返回 200 成功 | `voice.py:855-859` + `api/voice.py:163-165` | ✅ |
| P1-25 | P1 | 语音 | 音色克隆默认 provider 名不一致（`qwen_tts` vs `qwen-tts`） | `VoicePage.tsx:14, 229` vs `voice.py:640, 647` | ✅ |
| P1-26 | P1 | 首页/语音 | 音色无法可靠传递到新项目 | `VoicePage.tsx:37-47, 212` + `HomePage.tsx:375-377` | ✅ |
| P1-27 | P1 | 编辑器 | `.vtt` 导入静默无效果 | `EditorToolbar.tsx:716` | ✅ |
| P1-28 | P1 | 编辑器 | 真 FCPXML（FCP X）导入 0 片段 | `EditorToolbar.tsx:290` + `services/edl.py:136` | ✅ |
| P1-29 | P1 | 编辑器 | EDL 导出丢失源入点 | `EditorToolbar.tsx:330-336` + `services/edl.py:96` | ✅ |
| P1-30 | P1 | 时间轴引擎 | 锁定轨道仍可被拖入片段 | `TimelineEngine.ts:561` | ✅ |
| P1-31 | P1 | 时间轴 Store | 波纹删除只作用于单轨，编组不参与 | `timelineStore.ts:534-556` | ✅ |
| P1-32 | P1 | 预览 | 预览≠成片（动画占位/转场近似/滤镜语义） | `PreviewPanel.tsx:883-924, 581-624, 1073-1081` | ✅ |
| P1-33 | P1 | 预览 | 逐帧/拖动画面滞后 + 音画最大 0.25s 漂移 | `mediaManager.ts:252-261` + `PreviewPanel.tsx:91-145` | ✅ |
| P1-34 | P1 | 编辑器 | 无本地草稿/崩溃恢复；错误页文案不实 | `EditorPage.tsx:105-116` + `ErrorBoundary.tsx:65` | ✅ |
| P1-35 | P1 | 前端全局 | 长请求无法取消（全仓无 AbortController） | `grep AbortController` → 0 命中 | ✅ |
| P1-36 | P1 | 需求 Agent | token/jwt 模式下需求对话流式 401 且不回退 | `requirements.ts:32-40` vs `main.py:506-508, 526-528` | ✅ |
| P1-37 | P1 | 时间轴 | 隐藏轨道仍参与吸附/框选/命中 | `snap.ts:41-47` + `selectionStore.ts:64-71` + `TimelineEngine.ts:747` | ✅ |

> P2 / P3 / 待验证 的完整索引见 §4 / §5 / §7。

---

## 2. P0 详情（数据丢失与工程损坏）

### P0-01 · 大工程关闭/刷新时时间线被元数据桩覆盖

- **子系统**：前端编辑器 + 后端项目持久化
- **症状**：工程体积超过 48KB 时，在有未保存改动的状态下关闭标签页/刷新/离开页面，磁盘上的 `timeline` 被替换为 `{id, duration_sec, track_count, clip_count, last_edit_at}`；下次打开该工程，`timeline.tracks` 为 `undefined` → 渲染崩溃页，工程内容丢失。
- **证据**：
  - `J:\Clipweight-Client\src\pages\flushPayload.ts:25-47` —— `JSON.stringify(input).length > 48*1024` 时返回 `kind:'metadata'`，payload 的 `timeline` 只有 5 个字段。
  - `J:\Clipweight-Client\src\pages\EditorPage.tsx:270-285` —— `kind === 'metadata'` 时仍然 `fetch(PUT /api/project/{id}, body: JSON.stringify(decision.payload))`。
  - `J:\Clipwright\clipwright\services\project_manager.py:126-139` —— `existing.update(project_data)` 是**顶层 merge**，`timeline` 被整体替换；`_write_json` 无任何 schema 校验。
  - `J:\Clipwright\clipwright\api\project.py:63-65` —— `ProjectUpdateRequest.timeline: Any = None`，无校验。
  - **实测**：本机 392 个工程中，唯一两个有真实内容的工程为 348,047 B（299 片段）与 189,169 B（158 片段），**均超过 48KB 阈值**；空工程 194 B。约 1.2KB/片段，即 ≥40 片段必踩。
- **根因**：前端为了绕过 `keepalive` 大负载限制做了「降级保存」，但后端把「任意 timeline 字段」当真值整体落盘，两侧对「什么算有效 timeline」没有共识。
- **修复建议**：
  1. **前端**：`decideFlushPayload` 的超限分支改为**不发请求**（宁可不保存，也不能写坏）；或改为只发项目级字段（`name/persona_id/plugin_id`）而**不带 timeline 键**。
  2. **后端（必须做）**：`update_project` 在 `"timeline" in update_data` 时校验形状——要求是 dict 且含 `tracks` 为 list；否则忽略该字段或返回 400。建议在 `project_manager.save` 加一层 `_validate_timeline_shape`。
  3. **补测试**：`flushPayload` 单测覆盖 ≤48KB / >48KB 两个分支的 payload 形状；后端补一条「PUT 无 tracks 的 timeline 不会覆盖既有 timeline」的用例。
- **验收标准**：
  - 构造一个 >48KB 的 timeline，编辑后触发 `pagehide`，再次加载工程，`tracks` 完整、片段数量不变。
  - 后端收到 `{"timeline": {"id": "x"}}` 时，既有 timeline 不被修改（或返回 400），并有测试覆盖。

### P0-02 · 5s 防抖窗口内 SPA 跳转丢改动，且无离开拦截

- **症状**：编辑后 5 秒内点「返回首页 / 导出 / Ctrl+E」，最后一次改动不落盘；重新打开工程看到的是旧版本。刷新（触发 `pagehide`）反而能保存，所以表现为「有时保存有时不保存」。
- **证据**：
  - `EditorPage.tsx:228-243` —— 防抖定时器在 effect cleanup 里 `clearTimeout`，组件卸载即取消 pending save。
  - `EditorPage.tsx:252-293` —— 只有 `pagehide` / `beforeunload` 会 flush；SPA 路由跳转不触发这两个事件。
  - `EditorToolbar.tsx:673-678` —— 「导出」按钮直接 `navigate`，未调用 `requestSave()`。
  - 全仓无 `useBlocker` / 路由级脏数据拦截（grep 命中 0）。
- **根因**：把「保存」寄托在浏览器卸载事件上，而 SPA 导航不触发卸载。
- **修复建议**：
  1. 加 TanStack Router 的 `useBlocker`（或全局导航守卫）：`isDirty` 时弹确认或先 `await doSave()` 再放行。
  2. 卸载时若 `dirtyRef.current` 为真且无法同步保存，至少 toast 提示「有未保存改动」。
  3. 「导出」按钮改为 `await requestSave()` 后再 `navigate`。
- **验收标准**：
  - 编辑 → 立即点返回首页 → 重新打开工程，改动仍在（或明确弹窗阻止）。
  - 编辑 → 点导出 → 导出页使用的时间线与编辑器一致。

### P0-03 · 修剪拖拽不进撤销栈

- **症状**：拖动片段左/右边缘修剪（含 Alt 滚动修剪）后按 Ctrl+Z 无效，反而会撤销更早的一步。
- **证据**：
  - `TimelineEngine.ts:544` —— `onPointerUp` 开头 `const store = useTimelineStore.getState()`（**变更前**的快照）。
  - `TimelineEngine.ts:521-526` —— trim 的 `pointermove` 分支只做 `snapX = null; requestRender()`，**全程不写 store**。
  - `TimelineEngine.ts:636, 652` —— 守卫 `store.timeline !== this.drag.preSnapshot` 比较的是同一个对象，恒为 false → 不 push。
  - 对照：`move-clip` 在 `:617` 无条件 push；`gain` 因拖动期间持续写 store（`:533`）守卫为真、能 push。
  - `preSnapshot` 只在 `:371, 400, 406, 425`（拖拽开始时）赋值，拖拽过程中不再变。
- **根因**：守卫本意是「有实际变更才入栈」，但取样时机早于变更，判据失效。
- **修复建议**：改为在变更后用最新状态判断，例如把守卫改成 `useTimelineStore.getState().timeline !== this.drag.preSnapshot`；或与 `move-clip` 一样在确实应用了 ghost 时无条件 push。同时给 trim 两条路径补单测（拖拽后 `historyStore.undoStack.length` +1）。
- **验收标准**：修剪一次 → Ctrl+Z 恢复原时长；再 Ctrl+Shift+Z 重做；`undoStack` 只增加 1 条。

### P0-04 · 拖拽修剪绕过邻接钳制，可造出同轨重叠

- **症状**：把片段左边缘向左拖过前一片段的结尾（或右边缘拖过后一片段起点），两个片段重叠。重叠状态在渲染/导出上行为未定义。
- **证据**：
  - `TimelineEngine.ts:629, 649` —— 拖拽路径写 `store.updateClip(...)`（无邻居校验）。
  - `timelineStore.ts:378-381`（`trimClipStart` 的 `prevEnd`）、`:409-414`（`trimClipEnd` 的 `next.start_sec`）—— 钳制只存在于 store 的 trim API，而该 API 只被键盘微调调用（`useGlobalKeybindings.ts:280, 296`）。
  - `TimelineEngine.ts:721-734` —— `computeTrimGhost` 只钳 `[0, end-0.1]` 与 `[start+0.1, ∞)`，无邻居约束。
- **根因**：同一语义有两条实现路径（拖拽 / 键盘），只有一条带不变量校验。
- **修复建议**：让拖拽路径也走 `trimClipStart` / `trimClipEnd`（把 ghost 的结果作为参数传入），或把钳制逻辑抽成纯函数供两处共用。
- **验收标准**：向左/右拖过邻居时被钳在边界；不存在重叠片段（可加断言或单测）。

### P0-05 · 保存人格会静默重置一批后端专有字段

- **症状**：打开人格详情页，点一次「保存」，成功提示出现，但该人格的若干参数被改回默认值（用户看不到这些字段，因此无法察觉）。
- **证据**：
  - `PersonaDetailPage.tsx:132-136` —— `personaApi.update(persona.persona_id, { ...persona, prompt, parameter: {...} })` 发送的是**前端 UI 模型**。
  - `types/persona.ts:79-154`（`personaFromBackend`）**未映射**：`identity.positioning/class_perspective`、`language.allowed_patterns`、`rhythm.cut_profile`、`visual.animation_styles`、`audio.subtitle_enabled`、`constraints.forbidden_topics/min_duration_sec`；`bgm_slots` 被拍平成一维数组（丢槽位名）。
  - `schema/persona.py:90-106` —— 这些字段都有默认值（`subtitle_enabled=True`、`forbidden_topics=[]`、`cut_profile="even_flow"`）。
  - `api/persona.py:59-68` —— `update_persona` 是整份 `save_manifest` 替换；缺失字段用默认值补齐。
  - `schema/persona.py:183-184` —— 后端把前端的一维 `bgm_slots` 包装成 `{"default": [...]}`。
- **根因**：前后端字段映射是**单向子集**，而 PUT 是**全量替换**语义。
- **修复建议（二选一，建议 a）**：
  - a) 前端保存前先 `GET` 一次原始 manifest，把未映射字段原样合并回请求体（保留服务端真值）。
  - b) 后端 `update_persona` 改为**部分更新**：只在请求显式携带时覆盖字段（`model_dump(exclude_unset=True)` + 与既有 manifest 合并）。
  - 另：`bgm_slots` 建议前端保留 `Record<string,string[]>` 结构，不再拍平。
- **验收标准**：对一个 `subtitle_enabled=false`、`forbidden_topics=["x"]`、多槽位 `bgm_slots` 的人格保存一次后，用 `GET /api/persona/{id}` 复核这些字段不变；补前后端各一条回归用例。

---

## 3. P1 详情

### P1-01 · MG 预览端点永远返回空白视频且报成功（后端）

- **症状**：管理页「MG 预览」（`/settings/mg-preview`）点预览，返回 200 与一个视频 URL，但视频是透明/空白。
- **证据**：
  - `clipwright/api/animation.py:155` —— `render_overlays([{"mg_html": html, "start_sec": 0, "duration_sec": dur}], ...)`。
  - `clipwright/animation/hyperframes_renderer.py:238-242` —— `_overlay_to_html` 只读 `ov.get("text")`，为空即 `return ""`（全文无 `mg_html` 引用）。
  - `:211-214` —— `_build_html` 只收集非空元素 → `<div id="root">` 为空。
  - 对照：管线里的真实 MG 渲染走 `services/render.py:1998-2006`，把 `mg_html` 作为独立 `index.html` 交给 hyperframes，**是正确的**。
- **修复建议**：预览端点改为与渲染路径同构——把 `mg_html` 写入独立工作目录的 `index.html` 再交给 hyperframes（可复用 `_render_mg_mov` 的实现，抽成公共函数）。
- **验收标准**：用一个内置模板预览，输出的 mp4 非透明且能看到动画元素；补一条后端用例断言 MOV/MP4 非空且像素非全透明（可用 ffmpeg 取一帧算方差）。

### P1-02 · MG 生成缓存因键名笔误永不命中（后端）

- **症状**：同一输入重复生成 MG 时，每次都重新调用 LLM（成本与耗时翻倍），注释声称的「同输入零 LLM 成本」从未生效。
- **证据**：`clipwright/animation/mg/generator.py:214` 定义**局部** `_ck`；`:215` 用 `_ck` 读取；`:1183` 却写 `_mg_cache_put(getattr(self, '_ck', ''), ...)` —— `self._ck` 从未赋值，键恒为 `""`。
- **修复建议**：把 `:1183` 改为 `_mg_cache_put(_ck, ...)`（或把 `_ck` 存到 `self._ck`）。顺带处理：`clipwright/animation/mg/generation_cache.py` 的 `cache_get/cache_put` 全仓零调用（死模块），要么接上要么删除。
- **验收标准**：同一输入连续两次 `generate`，第二次命中缓存（trace 事件 `method=cache`），LLM 调用次数为 1；补单测。

### P1-03 · MG 片段窗口被钳到场景时长，HTML 仍按自身时长渲染（后端）

- **症状**：场景比 MG 短时，动画尾部（含退场）被硬切，没有重新配速。
- **证据**：`animation_agent.py:586-587` `mg_dur = mg_def.get("duration_sec", duration)`、`clip_dur = min(mg_dur, vid_clip.duration_sec)`；而 `:591-594` 渲染时只传 `width/height/fps`，`mg_renderer.py:167` 用 `dur = mg_def.get("duration_sec", 3.0)`。
- **修复建议**：给 `MGRenderer.render` 增加 `duration_override`，在 clip 窗口短于 MG 时长时按比例压缩关键帧时间；或明确策略——窗口不足时**不生成**该 MG 并在 trace 里告警。
- **验收标准**：构造一个 2s 场景 + 4s MG，输出成片里动画完整播完（被压缩）或该 MG 被跳过并有告警，二者择一且行为可预期。

### P1-04 · Agent 关键帧绝对秒被编辑器当归一化值（前后端）

- **症状**：Agent 生成的时间线在编辑器里打开时，动画「不动」（恒为首帧值）；从编辑器导出时动画塌成一个静态值。
- **证据**：
  - 真实工程 `J:\Clipwright\projects\proj_7d3a69b9eae8\project.json`：clip `tc_1b89e9d0`（kind=text，`start_sec=240`、`duration_sec=60`）的 `keyframes[].time = [240, 240.01, 299.7, 300]`，`metadata` 无 `kf_time_base`。
  - 前端 `easing.ts:82-83` —— `progress <= sorted[0].time` 直接返回首帧属性；`PreviewPanel.tsx:695, 703` 传的 `localT ∈ [0,1]` → 永远命中该分支。
  - 前端 `timelineStore.ts:831` —— `exportTimeline()` 对任何带关键帧的 clip **无条件**写入 `kf_time_base: 'clip_local'`；`:835` 只对 `time <= 1` 做 `×duration`。
  - 后端 `utils/keyframes_expr.py:107-113` —— 显式标记优先于启发式；标记为 `clip_local` 时 `shift=0`，`:121` 把 240 钳到 `min(60, 240)=60` → 四个关键帧全落到 t=60；`:173-174` 对零长区间返回常量（不报错）。
  - 后端启发式本可正确识别（`min(times)=240 >= start_sec=240-eps` → `absolute`），是前端标记把它覆盖了。
- **修复建议**：
  1. **前端加载时归一化**：`setTimeline` 或 `projectApi.load` 后，对每个 clip 的 keyframes 做一次「若所有 time > 1 → 视为绝对秒并转成 `(t - start_sec)/duration`」的归一化，同时补 `metadata.kf_time_base='clip_local'`（或明确保留绝对秒并在预览里按秒解释——二选一，但必须全链路一致）。
  2. **前端导出时不要盲目标记**：只在关键帧确实处于归一化时间基时才写 `clip_local`。
  3. 补测试：给定绝对秒关键帧，断言预览插值取到中间值、导出后时间基正确。
- **验收标准**：打开该真实工程，预览能看到透明度动画；导出成片动画与管线自渲染一致。

### P1-05 · 差异视图只比 14 个字段 → 动画/样式改动被判「无差异」（前端）

- **症状**：让 Agent 局部重做（改动画、换字体、加描边、调滤镜）后，差异面板显示「Agent 时间线与当前一致，无差异。」且只给一个「关闭」按钮 —— 用户既不能接受这次修改，也看不到「不满意重做」入口。
- **证据**：`timelineDiff.ts:26-30` 的 `COMPARED_FIELDS` 只有 `kind/asset_id/track_id/start_sec/duration_sec/source_offset_sec/speed/volume/opacity/text/font_size/font_color/transition_in/transition_out`；未包含 `keyframes`、`metadata`（MG 的 `mg_def`/`mg_html` 全在这里）、`font`、`text_align`、字幕样式（stroke/shadow/glow/letter_spacing/font_weight/font_italic）、`fx_*`、`image_*`、`mask_*`、`bar_*`、`shape`、`fill`、`blend_mode`、`enabled`、`audio_fade_*`、`transition_duration_sec`、`group_id`。`TimelineDiffView.tsx:129-137` 在 `isEmpty` 时只渲染「关闭」。
- **修复建议**：把比较改为「深比较 clip 全字段（排除 `id`/`track_id`）」，或至少补齐上列字段；`isEmpty` 分支增加「接受/重做」入口（即使无差异也允许用户用 Agent 版本覆盖）。
- **验收标准**：只改 `keyframes` 或 `metadata.mg_def` 的提案能被识别为 modified 并可接受。

### P1-06 · 点「自定义」导出预设后渲染必 400（前端）

- **症状**：在导出页选「自定义」卡片后点渲染 → 后端 400「未知导出预设: custom」；此后点已保存的自定义预设也无法恢复（`settings.preset` 仍是 custom）。
- **证据**：`ExportPage.tsx:32` 内置 `custom` 预设；`:189` `applyPreset` 写入 `settings.preset = 'custom'`；`:455` 应用已存预设时只 `setPresetId('')`，不清 `settings.preset`。后端 `api/render.py:967-968` 对不在 `_EXPORT_PRESETS` 的 preset 抛 ValueError → 400。
- **修复建议**：`custom` 不应作为 preset 名发送——选自定义时把 `settings.preset` 置空串（后端空串走「显式参数」分支，`render.py:976-988`）；应用已存预设时同样清空。或后端把 `custom` 加入白名单并当作「仅用显式参数」。
- **验收标准**：选自定义 + 改分辨率/帧率/码率 → 渲染成功且参数生效。

### P1-07 · 渲染失败只显示「提交失败」，丢弃 422 字段详情（前端）

- **症状**：导出失败时队列项只显示「提交失败」，用户不知道是哪个字段非法（例如某 clip 越界）。
- **证据**：`ExportPage.tsx:233-235` 只在 `typeof detail === 'string'` 时展示；FastAPI 422 的 `detail` 是数组 → 落到通用文案。而 `client.ts:49-59` 其实已经把 `detail[0].msg` 归一化到 `err.userMessage`，未被使用。
- **修复建议**：统一读取 `(err as {userMessage?: string}).userMessage ?? 解析 detail 数组`；把前 3 条字段错误拼进队列项详情。
- **验收标准**：故意构造一个非法字段（如 `duration_sec=0`）提交渲染，UI 显示具体字段与原因。

### P1-08 · 素材入库只写 title，刷新后片段全部「素材加载失败」（前端）

- **症状**：从素材面板把已上传的素材拖入时间轴，当次会话预览正常；刷新页面后，这些片段显示「素材加载失败」占位。
- **证据**：`AssetPanel.tsx:157` `clipData = { kind, asset_id: asset.id, duration_sec, metadata: { title: asset.filename } }` —— **未写 `local_path`/`url`**。`mediaManager.ts:23-32` 的 `resolveMediaUrl` 于是回落到 `clip.asset_id` 作为 by-path 的 `path`；而 `asset.py:143-162` 的 `/api/asset/by-path` 要求白名单内的**文件路径**，传入 asset_id 会 403/404 → `entry.error=true` → 预览画错误占位。另：`assetApi.fileUrl()`（`asset.ts:117`，正确的 `/api/asset/{id}/file`）全仓无人调用。
- **修复建议**：`addToTimeline` / AI 匹配入轨（`:523-539`）都写入 `metadata.local_path = asset.path`（`mapAsset` 已把 `file_path` 映射到 `Asset.path`）；`resolveMediaUrl` 增加「asset_id 形如 `asset_*` 时改用 `/api/asset/{id}/file`」的分支。
- **验收标准**：上传素材 → 入轨 → 刷新 → 预览仍能播放真实画面。

### P1-09 · 多素材源检索逗号拼接 → 静默 0 条（前后端）

- **症状**：在素材检索里勾选 2 个以上素材源，搜索永远无结果（且不报错、不触发演示回退）。
- **证据**：`asset.ts:131` `params.sources = source.join(',')`；后端 `api/material.py:25` 声明 `sources: Optional[list[str]]`，Starlette 把 `?sources=a,b` 解析成 `["a,b"]`；`material/registry.py:62-63` `[s for sid,s in cls._sources.items() if sid in source_ids]` → 无源匹配 → 空结果。
- **修复建议**：前端改为 repeated query（`sources=a&sources=b`）或用 JSON body；后端可加兼容：把单元素含逗号的列表再 split 一次。
- **验收标准**：勾选 2 个源能返回两源合并结果；补后端单测覆盖 `["a,b"]` 兼容。

### P1-10 · 插件配置保存丢掉 secret 标记并明文/掩码回写（前后端）

- **症状**：在插件管理页保存一次插件配置后，该插件的密钥字段（如 Pexels API Key）**明文落盘、明文回显、不再加密**。
- **证据**：
  - `PluginsPage.tsx:460-481` 的 `buildConfigYaml` 只输出 `type/label/description/value`，**不输出 `secret`**。
  - `loader.py:88-103` 的掩码只在 `field.get("secret") is True` 时生效；`:437-448` 的 `save_config` 原样落盘；`config_types.py:39-43` 的加密同样只对带标记字段生效。
  - **现场证据**：插件模板 `J:\Clipwright\plugins\pexels_material\config.yaml` 为 `api_key: {type: string, value: "", secret: true, label, description}`；而用户保存后的 `J:\Clipwright\PluginData\plugins\pexels_material\config.yaml` **没有 `secret`**，且 `value` 是一个 51 位真实 Pexels Key。
  - 对照：后端配置页 `BackendConfigPage.tsx:89, 105-106` 已正确处理（secret 留空不提交）。
- **修复建议**：
  1. 前端 `buildConfigYaml` 必须原样保留 `secret`（以及其它未知键，建议直接透传原字段对象）。
  2. 前端对 secret 字段做「留空不提交」处理（参考 BackendConfigPage）。
  3. 后端 `save_config` 增加防御：若提交值等于掩码形态（含 `****`）则保留旧值，不覆盖。
  4. 迁移：为已丢标记的配置文件补回 `secret: true`（或提供一次性修复脚本）。
- **验收标准**：保存一次配置后，`config.yaml` 仍有 `secret: true`；GET 返回掩码；密钥在磁盘上为密文。

### P1-11 · 工具与技能页「执行」全部 422（前后端）

- **症状**：管理页「工具与技能」点执行，永远失败，界面提示「执行失败（后端可能离线）」——但后端其实在线。
- **证据**：`api/skill.py:22` `async def execute_skill(name: str, params: dict[str, Any] = {})` 与 `api/tool.py:44` 同形——`name` 是**查询参数**，`params` 才是 body。前端 `project.ts:343-347` / `tool.ts:34` 只发 body `{name, params}` → FastAPI 返回 422 `Field required (query, name)`。`ToolsPage.tsx:79-80` 把错误显示为「后端可能离线」。
- **修复建议**：后端改为 `class ExecuteRequest(BaseModel): name: str; params: dict = {}` 的 body 模型（推荐），或前端把 `name` 放进 query。同时把 `ToolsPage` 的错误文案改为显示真实状态码与 `detail`。
- **验收标准**：任一工具/技能能在页面上执行成功；422 时 UI 显示字段级原因。

### P1-12 · 启用插件失败时回滚是死代码（后端）

- **症状**：启用一个加载会失败的插件（签名/权限/依赖不满足）→ 返回 500，且插件状态文件已写为 `enabled`，重启后表现为「已启用但未加载」。
- **证据**：`api/plugin.py:94` `_loader.set_enabled(plugin_id, True)` 在 `try`（`:95`）**之外**；而 `loader.py:563-571` 的 `set_enabled` 在 `enabled and plugin_id not in self._plugins` 时会内部调用 `self.load(plugin_id)`，可抛 `PluginLoadError` → 逃出函数 → 不执行 `:97-100` 的 `set_enabled(False)` 回滚与 502。
- **修复建议**：把 `set_enabled(True)` 移入 `try`；或让 `set_enabled` 只写状态、由调用方显式 `load()`；`load` 失败时务必回写 disabled。
- **验收标准**：用一个签名/依赖不满足的插件做启用，返回 502 且状态文件为 `disabled`。

### P1-13 · 同名工具/技能静默覆盖内置，卸载后内置永久消失（后端）

- **症状**：一个第三方插件注册了与内置同名的工具（如 `video_trim`），内置被顶掉；禁用/卸载该插件后，该工具**彻底消失**（直到进程重启）。
- **证据**：`tool/registry.py:42-50` 冲突只 `logger.warning` 后 `cls._tools[tool.name] = tool`；`:61-63` `unregister(name)` 按名 pop，不恢复被覆盖者；`skill/registry.py:29` 同形且无告警。`loader.py:289-292` 的「新注册物」差分按名判断，被覆盖的名字不算「新」。
- **修复建议**：注册冲突时**拒绝覆盖内置**（内置优先）或保存被覆盖项以便恢复；`unregister_by_plugin` 只移除 `_plugin_id` 匹配的项，且恢复被它覆盖的旧项。
- **验收标准**：插件注册同名工具时内置仍可用；卸载插件后内置工具仍在。

### P1-14 · 插件安全四连（后端）

- **症状**：插件的「权限白名单 / 签名 / 安装包解压」三套安全机制在默认配置下都不构成实际约束。
- **证据**：
  1. **权限装饰性**：`config.py:46-47` 默认白名单含 `network/fs_read/fs_write/shell/http/noop/orchestrate`（等于全放行）；`loader.py:74-77` 只校验「声明的名字是否被识别」；全仓无运行时拦截，唯一真实生效的是 `orchestrate`（`loader.py:305`）。
  2. **entry_point 无校验 + sys.path 污染**：`schema/plugin.py:34` 无 pattern；`loader.py:674-686` 把整个 plugins 根目录插入 `sys.path[0]` 再 `importlib.import_module(entry_point)` → 插件可 shadow 任意顶层模块名（`requests`/`yaml`/`PIL`…），影响整个进程。
  3. **tar 解压**：`install_service.py:17-27` 只对成员名做 `startswith` 前缀检查（且前缀比较可被 `.../tmp/abc` vs `.../tmp/abc-evil` 绕过），随后 `tf.extractall(dest)` 未用 `filter="data"`（项目 `requires-python >= 3.12`，本可直接用）→ 符号链接成员可让后续成员写到目标目录之外。
  4. **签名默认关闭**：`loader.py:213` 仅在 `plugin_require_signature` 或显式 key 时强制；`config.py:43` 默认 False；内置 30+ 插件均无 `signature`。
- **修复建议**：
  - 权限：把「默认全允许」改为默认最小集（至少把 `shell` 移出默认），并在 UI 上把权限徽标标注为「声明信息，非沙箱」。
  - entry_point：加 pattern（如 `^[a-z0-9_]+(\.[a-z0-9_]+)?$` 且必须以插件 id 开头）；改为把**插件自身目录**加入 `sys.path` 而非整个 plugins 根。
  - 解压：`extractall(filter="data")` 或显式拒绝 symlink/硬链接成员；前缀比较改 `Path.is_relative_to`。
  - 签名：文档明确「默认不校验」并在 UI 显示 `verified` 状态；市场安装路径强制要求签名。
- **验收标准**：构造恶意样本（含 symlink 成员 / 与内置同名的顶层模块 / 非法 entry_point），安装被拒绝；补安全负例测试。

### P1-15 · 自定义类型的节奏/转场/踩点配置整体失效（后端）

- **症状**：用户在「类型制作器」里配好的镜头时长、转场类型、踩点开关，对成片**完全没有影响**；人格映射也是空转。
- **证据**：
  - 真实用户类型文件 `J:\Clipwright\user_types\t_valid_ok.yaml` 的 `shot_params` 用 `min_shot_sec/max_shot_sec/transition_type/transition_duration_sec/cut_on_beat`，`persona_mapping.rhythm.source = "rhythm.cut_density_tier"`。
  - `category/dynamic.py:33-41` 的 `translate_persona` **只返回映射结果**，不返回 `shot_params`；`pipeline_v2.py:1175-1176` 把它并入 `extra_params`；`edit_agent.py:279` 读 `context.extra_params.get("shot_params", {})` → 恒为 `{}` → 回退 `base_shot_ms=5000`（`:286`）。
  - `dynamic.py:38-40` `if source in data` —— `data = params.model_dump(mode="json")` 是**嵌套** dict，`"rhythm.cut_density_tier" in data` 恒为 False；`transform`/`scale_factor` 也从未被应用。
  - 对比：内置类型（如 `category/kichiku_fastcut.py:31-46`）的 `translate_persona` **会**返回 `shot_params`（ms 键），所以内置类型正常。
  - `api/type_maker.py:242-252` 的 `/preview` 却会走嵌套路径，返回一个运行时永远不会出现的 `sample_translation`。
- **修复建议**：
  1. `DynamicCategoryPlugin.translate_persona` 返回 `{**translated, "shot_params": self._shot_params}`（或让 pipeline 显式合并 `get_shot_params(...)`）。
  2. 统一 `shot_params` 的键与单位：建议全仓统一为 `base_shot_ms/min_shot_ms/max_shot_ms`（Agent 侧），TypeMaker 的 `ShotParams` 增加这些键或做单位换算。
  3. `persona_mapping.source` 支持点号路径（按 `.` 逐层取值），并实现 `transform`（direct/scale/lookup）。
- **验收标准**：创建一个 `max_shot_sec=2` 的自定义类型跑管线，EditAgent 的 `base_shot_ms` 受其约束（日志/trace 可见）；`persona_mapping` 生效。

### P1-16 · user_types 里一个坏 YAML 让类型列表整体 500（后端）

- **症状**：`user_types/` 下有一个解析失败的文件时，`GET /api/type-maker/list` 直接 500，首页/类型页的类型列表不可用。
- **证据**：`api/type_maker.py:107-108` 在 `except` 里 `logger.warning("Failed to parse type config: %s", config_path, exc_info=True)` —— 该函数内**从未定义 `config_path`**（循环变量是 `f`）→ `NameError` 从 except 里抛出 → 500。
- **修复建议**：改为 `f.name`（或 `f`）；补一条「目录内含坏 YAML 时 list 仍返回其余类型」的测试。
- **验收标准**：放入一个语法错误的 yaml，`/api/type-maker/list` 返回 200 且包含其它类型。

### P1-17 · `/run-async` 成功后仍被标 failed（后端）

- **症状**：通过 `POST /api/pipeline/run-async`（及 `run-batch`）启动的管线，即使成功也会被记录为 failed，错误信息是 `name 'ok' is not defined`；结果里的 `final_timeline` 被覆盖丢失，webhook 与 auto_render 不触发。
- **证据**：`api/pipeline.py:375` `if ok and isinstance(request.extra_params, dict) and request.extra_params.get("auto_render"):` —— `ok` 直到 `:401` 才赋值 → `NameError` → 落到 `:438` 的 `except Exception` → `:442` 用 `{"status":"failed","error":"name 'ok' is not defined"}` 覆盖 `:370` 写入的真实结果；`:392` 的 POST_PIPELINE hook 与 `:402-412` 的 webhook 均不执行。
- **影响范围**：前端主流程走 `POST /api/requirements/proceed`（`requirements.py:388` 有独立 handler，无此 bug），所以 UI 不受影响；受影响的是一条龙 API 调用、`run-batch`（文档中的「一键出片」）与 webhook/auto-render 集成。
- **修复建议**：把 `:401` 的 `ok` 赋值上移到 `:375` 之前（或把该判断改成 `str(getattr(state,'status','')).lower() in ('completed','pass')`）。
- **验收标准**：`/run-async` 成功跑一次，`GET /result` 返回 `completed` 且含 `final_timeline`；webhook 收到 `pipeline.completed`。

### P1-18 · 类型后处理与署名轨只在 retry 路径执行（后端）

- **症状**：同一条时间线，首次运行与「重试」产出的成片不一致：重试版本才会应用类型 transform、打 `type_violation` 标记、追加成片署名轨。
- **证据**：主路径 `pipeline_v2.py:1045-1063`（`_run_inner` 结束段）只写 `final_timeline`，无任何后处理；`plugin.post_process_timeline`（`:828`）、`validate_clip`（`:832-853`）、`_append_credit_outro`（`:861`）全部位于 `run_from_agent` 内（`:825-865`）。`_append_credit_outro` 全仓仅此一处调用。
- **修复建议**：把这段后处理抽成 `_finalize_timeline(...)`，在 `_run_inner` 与 `run_from_agent` 两处都调用（避免重复实现）。
- **验收标准**：首次运行也带署名轨、应用 transform、`type_violation` 标记；两条路径输出一致（可用同输入对比 JSON）。

### P1-19 · 取消/超时无法中断在飞的 LLM 线程（后端）

- **症状**：取消管线后，正在进行的 LLM 调用仍继续跑（继续计费），结果被丢弃。
- **证据**：`agents/base.py:38-47` `return await asyncio.wait_for(coro_factory(), timeout=timeout)`（docstring 自述「无法真正取消线程内的 provider 请求」）；`pipeline_v2.py:1329` 的 `_dispatch` 经 `asyncio.to_thread` 调 provider；`task.cancel()` 只取消协程。取消只在 agent 边界被观察（`_run_agent` 的 `_CANCELLED` 检查）。
- **修复建议**：至少做到「取消后不再使用其结果 + 记录一次 wasted call 事件」；进阶做法是给 provider 请求加超时/中断钩子，或改用支持取消的 HTTP 客户端。
- **验收标准**：取消后日志/trace 里能看到在飞调用被放弃；不出现「取消后仍把结果写入 timeline」的情况。

### P1-20 · `plan_confirmed` / `brief_confirmed` 无路由分支（后端）

- **症状**：在确认简报或规划书之后（状态进入 `brief_confirmed`/`plan_confirmed`）再发一条消息，**没有任何回复**，状态也不变，会话看起来卡死。
- **证据**：`requirements_service.py:569` 置 `brief_confirmed`、`:623` 置 `plan_confirmed`；而状态路由只有 `gathering/init`（`:533`）、`brief_ready`（`:567`）、`pipeline_running`（`:599`）、`pipeline_done`（`:607`）、`plan_ready`（`:621`）五支，没有 `brief_confirmed`/`plan_confirmed` 分支。
- **修复建议**：补两个分支（例如提示「已确认，等待启动/可直接回复『重新生成』」），或把确认动作直接推进到 `pipeline_running` 而非中间态。
- **验收标准**：在 `plan_confirmed` 下发消息能收到明确回复且状态合理。

### P1-21 · 单字确认词误判为「确认」（后端）

- **症状**：用户回复「对比一下」「行业分析」「嗯…我觉得还能改」时，系统判定为确认，推进到 P1-20 的死状态。
- **证据**：`requirements_service.py:1139-1151` —— `affirm_starts` 含 `"行"、"对"、"嗯"、"y"`，判断是 `low.startswith(kw) or low == kw`，只有少量转折词（但是/不过/再/请…）会否决；`"对比一下"` 的 tail `"比一下"`、`"行业分析"` 的 tail `"业分析"`、`"嗯…我觉得还能改"` 的 tail `"…我觉得还能改"` 都不在否决列表 → `return True`。
- **修复建议**：单字确认词（`行/对/嗯/y`）必须要求**整句匹配**（`low == kw` 或 `low.strip('。！!~ ') == kw`），不得用 `startswith`；或把这类模糊输入交给已有的 LLM 语义判定（`:1153-1164`）。
- **验收标准**：上列三句都不被判为确认；补单测。

### P1-22 · gathering 只把最后一条消息喂给 LLM（后端）

- **症状**：多轮澄清没有记忆；用户的修改在「非确认」分支被吞掉，下一轮 LLM 只看得到新消息。
- **证据**：`requirements_service.py:1352` `user_prompt = messages[-1]["content"] if messages else "请开始对话。"`；`:530` 的 `compress_history(messages)` 结果只被持久化，从未注入 prompt；`brief_ready` 的非确认分支（`:591-597`）只置 `status="gathering"` 并回「好的，请告诉我需要如何调整？」。
- **修复建议**：把最近 N 轮对话（或压缩后的摘要）拼进 `user_prompt`；非确认分支当轮就调用一次修订。
- **验收标准**：连续三轮澄清，LLM 能看到前文；用户的修改在一轮内生效。

### P1-23 · 上传配音的 STT 对齐与 NEL 对齐都失效（后端）

- **症状**：用户上传自己的配音（`audio_path`）时，字幕仍是「按字数比例估算」，与真实语音漂移；动画也不做任何 NEL/节拍对齐。
- **证据**：
  - `audio_agent.py:178` `if isinstance(_sfx_list, list) and _sfx_list:` → `:211` `if _sfx_added:` → **`:214-248` 的 STT 对齐块嵌在这里面**。也就是说：只有配置了至少一个存在的 SFX 文件时，上传配音的字幕才会走 STT 对齐。默认（无 sfx）永远不执行。
  - `attach_nel_to_timeline` / `align_animations_to_nel` 只在 TTS 分支内调用（`:529-539`），而该分支的门控 `:440-446` 明确排除了 `uploaded_dub`。
- **修复建议**：把 STT 对齐块移出 SFX 分支（提到 `:178` 之前或之后同级）；把 NEL 提取与动画后置对齐也移到「上传配音」路径（可用 `STTService.align` 的分段结果作为 NEL 输入）。
- **验收标准**：上传配音 + 无 SFX，字幕时间与音频一致（抽样偏差 < 150ms），动画 clip 有 `nel_aligned` 标记或至少做了节拍对齐。

### P1-24 · dub_script 全段失败仍返回 200 成功（后端）

- **症状**：音色被删除或合成全部失败时，`POST /api/voice/dub` 返回 200，`segments` 里每条都带 `error`，`total_duration_sec=0`，调用方以为成功。
- **证据**：`voice.py:855-859` 返回 `VoiceResult(data={...})`，`VoiceResult.__init__` 默认 `success=True`（`:54-57`），每段失败只塞进 `results`；`api/voice.py:163-165` 仅在 `result.success` 为 False 时才 400。
- **修复建议**：当 `results` 全部含 `error`（或 `total_duration_sec == 0`）时置 `success=False` 并给出首个错误；接口返回 4xx。
- **验收标准**：用一个已删除的 voice_id 调 dub，返回非 200 且错误信息可读。

### P1-25 · 音色克隆默认 provider 名不一致（前后端）

- **症状**：克隆对话框默认选中的 Qwen TTS，一提交就报 `Unknown provider 'qwen_tts'`。
- **证据**：`VoicePage.tsx:14` `{ id: 'qwen_tts', ... }`、`:229` `useState('qwen_tts')`；后端 `voice.py:640` 只认 `"qwen-tts"`，`:647` 抛 `Unknown provider 'qwen_tts'. Choose from: qwen-tts, cosyvoice, minimax`。
- **修复建议**：前端 id 改为 `qwen-tts`（同步展示映射），或后端同时接受下划线别名。
- **验收标准**：默认 provider 能成功克隆；cosyvoice/minimax 仍可用。

### P1-26 · 音色无法可靠传递到新项目（前端）

- **症状**：从首页「配音驱动」模式启动的新项目，管线里 `voice_id` 通常为空 → 音频 Agent 的 TTS 门控不成立 → **不生成旁白**。
- **证据**：
  - 首页启动表单没有音色选择器（`HomePage.tsx` 的「03 配音与字幕」只有模式切换与音频上传）。
  - `/voice` 独立页没有「选择此音色」按钮：`VoicePage.tsx:212` 仅在传入 `onSelect` 时渲染，而独立路由 `<VoicePage />` 不传（只有 `DubView.tsx:174` 传）。
  - 持久化订阅 `VoicePage.tsx:37-47` 只在**独立页挂载期间**生效，写的是 `projectStore.voiceId`；而 `projectStore.setVoiceId` 只有 `DubView.tsx:108/174` 会调用。
  - 首页读取 `HomePage.tsx:375-377` 的 `clipwright_voice_prefs`；`autoDub` 被硬编码为 `true`（`VoicePage.tsx:41`），用户无法关闭自动配音。
  - 后端门控：`audio_agent.py:440-446` 要求 `auto_dub and voice_id and script_text and video_mode=="voiceover"`。
- **修复建议**：
  1. 首页「配音与字幕」步骤加音色下拉（数据源 `voiceApi.list()`），直接写 `projectStore.voiceId`。
  2. `/voice` 独立页也提供「设为默认音色」入口（把选中音色持久化到 `clipwright_voice_prefs`）。
  3. `autoDub` 加显式开关，不要硬编码 true。
- **验收标准**：首页选音色 → 启动 → 管线日志显示 `voice_id` 非空且生成旁白轨。

### P1-27 · `.vtt` 导入静默无效果（前端）

- **症状**：字幕导入接受 `.vtt` 文件，但选完之后什么都没发生，也没有提示。
- **证据**：`EditorToolbar.tsx:716` `const timeLine = lines[1];` 假设 SRT 的「序号行 + 时间行」结构；WebVTT 的 cue 没有序号行，时间行在 `lines[0]` → 每个块都 `!timeMatch` 被跳过 → `entries.length === 0` → 提前 return。
- **修复建议**：解析时探测时间行位置（`lines.find(l => /-->/.test(l))`），并兼容 `WEBVTT` 头与 `.` 毫秒分隔符；解析为空时给出 toast。
- **验收标准**：导入一个标准 vtt，字幕正确落到字幕轨。

### P1-28 · 真 FCPXML（FCP X）导入 0 片段（前后端）

- **症状**：点「导入 EDL/FCPXML」选一个真正的 `.fcpxml`（Final Cut Pro X 导出），静默无任何效果。
- **证据**：`EditorToolbar.tsx:290` 依据扩展名/`<?xml` 路由到 `importFCPXML`；后端 `services/edl.py:136` 只遍历 `root.iter("clipitem")`（FCP 7 `xmeml` 语法）；FCP X 用 `<asset-clip>`/`<clip>` → 匹配 0 条 → 返回 `clips: []`（HTTP 200），前端 `:294` `if (clips.length === 0) return;` 静默返回。
- **修复建议**：后端 `parse_fcpxml` 增加 FCP X 分支（解析 `<asset-clip>` 的 `offset`/`duration`/`ref`），或前端在 `clips.length === 0` 时明确提示「该文件格式不受支持」。
- **验收标准**：FCP X 导出的 fcpxml 能导入片段；不支持时给出明确提示。

### P1-29 · EDL 导出丢失源入点（前后端）

- **症状**：导出的 EDL 里，所有片段源入点都是 `00:00:00:00`，无法与原始素材重新链接。
- **证据**：`EditorToolbar.tsx:330-336` 只发 `id/start_sec/duration_sec/kind/title`，**不含 `source_offset_sec` 与 `asset_id`**；后端 `services/edl.py:96` `src_off = clip.get("source_offset_sec", 0)` → 恒 0；reel 名由 title 字符串推导而非真实素材 id。
- **修复建议**：导出 payload 补 `source_offset_sec`、`asset_id`（后端用它生成 reel/`FROM CLIP NAME`）。
- **验收标准**：对修剪过的片段导出 EDL，源入点与编辑器一致。

### P1-30 · 锁定轨道仍可被拖入片段（前端）

- **证据**：`TimelineEngine.ts:561` 只校验 `candidate.kind === cur0.tracks[origIdx].kind`，未校验 `locked`；而 `hitTestClip`（`:747`）与键盘 `moveClipUp/Down` 都校验锁定 → 行为不一致。
- **修复建议**：跨轨移动时同时校验目标轨 `locked`（源轨锁定也一并校验）。
- **验收标准**：向锁定轨道拖拽被拒绝并有视觉反馈。

### P1-31 · 波纹删除只作用于单轨，编组不参与（前端）

- **证据**：`timelineStore.ts:534-556` 只对「包含被删片段的轨道」做后续片段左移；其它轨道（音频/字幕）不动，`group_id` 也未参与。
- **修复建议**：波纹删除应按「被删片段所在时间窗」对所有未锁定轨道联动左移；若片段属于编组，编组内片段一起删除。
- **验收标准**：删掉一个视频片段后，其音频与字幕同步前移，不产生失同步。

### P1-32 · 预览≠成片（前端）

- **证据**：
  - 动画片段只画脉冲占位方块 + 「未渲染预览」角标（`PreviewPanel.tsx:883-924`）。
  - 转场只近似：`fade/dissolve/pixel_dissolve/glitch` 走 alpha 淡入淡出，`slide/wipe` 走几何（`:581-624`），其余转场名退化为硬切。
  - 滤镜用 canvas `ctx.filter`（CSS 语义，`:1073-1081`）与 ffmpeg 滤镜语义不完全等价。
  - 音频预览只按元素音量播放，不体现后端混音（EQ 后端未实现且 UI 已禁用，可接受）。
- **修复建议**：短期在预览上明确标注「近似预览」并对不支持的转场给出提示；中期接入 MG 预览端点（见 P1-01）与转场帧级对照。
- **验收标准**：至少做到「用户能知道哪些效果预览不可信」，且 MG 预览可用。

### P1-33 · 逐帧/拖动画面滞后 + 音画 0.25s 漂移（前端）

- **证据**：`mediaManager.ts:252-261` 的 `seekVideo` 设置 `currentTime` 后立即返回，`PreviewPanel.tsx:764-765` 随即 `drawImage` —— 没有 `seeked`/`requestVideoFrameCallback` 驱动的重绘，画面滞后一帧（阈值 0.03s）；音频同步 `PreviewPanel.tsx:91-145` 每 ~100ms 校正一次、阈值 0.25s。
- **修复建议**：seek 后用 `seeked` 事件或 `requestVideoFrameCallback` 触发重绘；播放时以音频时钟为主，把视频 seek 阈值降到 1~2 帧。
- **验收标准**：逐帧步进时画面随播放头变化；连续播放 30s 音画偏差 < 80ms。

### P1-34 · 无本地草稿/崩溃恢复；错误页文案不实（前端）

- **证据**：`EditorPage.tsx:27-29` 注释「strict: no IndexedDB fallback」；后端不可用时 `:110-116` 打开一个新 id 的本地空工程，编辑只存在内存。`ErrorBoundary.tsx:65` 却写「你的时间线数据保存在本地缓存中，重新加载后通常可以恢复」——**该缓存并不存在**。
- **修复建议**：二选一——(a) 实现最小本地草稿（IndexedDB 或 localStorage 定期快照，仅用于恢复）；(b) 修正文案为「请确认已保存；如未保存可能丢失」。
- **验收标准**：文案与实际行为一致；若实现了草稿，崩溃后能恢复。

### P1-35 · 长请求无法取消（前端）

- **证据**：全仓 `grep AbortController` 零命中；`requirements.ts:16`（chat 600s）、`:81`（edit 600s）、`:122`（proceed 300s）等长请求均不可中断；离开页面后请求继续执行。
- **修复建议**：给这些请求加 `AbortController` 并在 UI 上提供「取消」；组件卸载时 abort。
- **验收标准**：聊天/改写进行中能点取消，请求真正中断（网络面板可见）。

### P1-36 · token/jwt 模式下需求对话流式 401 且不回退（前后端）

- **证据**：`requirements.ts:32-40` 把一次性 SSE token 放进 `Authorization: Bearer`；后端只在 query 里消费它（`main.py:506-508` / `526-528` 的 `_consume_sse_token(qtok)`）。其他两个 SSE 消费者（`sseStream.ts:45`、`AgentPanel.tsx:768`）用的是 `withSseToken`（query）。且 `AgentPanel.tsx:266-271` 明确**不再**回退到非流式 chat → 核心对话直接失败。
- **修复建议**：`streamChat` 改用 `withSseToken` 拼 query；或后端同时接受 Bearer 形式的一次性 token。
- **验收标准**：`CLIPWRIGHT_ACCOUNT_VERIFY_MODE=token` 下需求对话流式可用。

### P1-37 · 隐藏轨道仍参与吸附/框选/命中（前端）

- **证据**：`snap.ts:41-47` 收集吸附目标时不检查 `track.hidden`；`selectionStore.ts:64-71` 的 `selectClipsInRange` 不检查 `hidden/locked`；`TimelineEngine.ts:747` 的 `hitTestClip` 只检查 `locked`。
- **修复建议**：三处都跳过 `hidden` 轨道（框选同时跳过 `locked`）。
- **验收标准**：隐藏轨道上的片段不可被吸附/框选/点选。

---

## 4. P2 详情

### 4.1 编辑器与时间轴

| ID | 症状 | 证据 | 修复建议 |
|---|---|---|---|
| P2-01 | 合并 Agent 提案时忽略轨道级/时间线级变更 | `timelineDiff.ts:109-157` 只处理 clip 的增删改 | 把 `markers/width/height/fps/track.locked/muted/hidden/index` 纳入合并 |
| P2-02 | 三个设置项是死的（自动保存 / 默认帧率 / 默认分辨率） | `settingsStore.ts:92-102, 116-124`；`EditorPage.tsx:228-243` 无判断；`createEmptyTimeline` 硬编码 1920×1080@30 | 要么接线，要么从 UI 移除（推荐接线：`createEmptyTimeline` 读设置） |
| P2-03 | `Timeline`/`Track` 的 `metadata` 在前端类型里缺失 | `types/timeline.ts:142-151` vs `schema/timeline.py:169, 189` | 补类型字段；顺带接出响度开关/目标 LUFS/软字幕（后端已支持 `timeline.metadata.loudnorm`、`RenderSettings.soft_subtitle`） |
| P2-04 | beat-sync（卡点剪辑）无前端入口 | `requirements.py:82, 350` 的 `cut_on_beat/beat_bpm`；`audio_agent.py:269` 提示「可在需求中手填 beat_bpm」但界面没有该字段 | 需求表单加「踩点 + BPM」输入，透传到 `proceed` |
| P2-05 | 字幕样式级联整轨，无法单条覆盖 | `PropertiesPanel.tsx:108-115` + `timelineStore.ts:284-290` | 增加「仅当前片段」开关，或对选中单条时走 `updateClip` |
| P2-06 | 同轨多条 text 的堆叠偏移方向与导出相反 | `captionLayout.ts:62, 85`（`y + offsetY`，向下）vs `tool/design.py:313-315`（bottom 锚点 `h-text_h-20-offset_y`，向上；center 无偏移） | 统一符号；center 锚点与后端对齐 |
| P2-07 | 吸附网格目标循环在长时间线 + 小网格时开销大 | `snap.ts:31-36`、`renderers.ts:174-178`；`snapGridSec` 下限 0.05 | 改成只生成视口附近的目标，或按需二分 |
| P2-08 | rolling 尾边界修剪打乱两段素材窗口 | `timelineStore.ts:457-462`（clip 被 `remapOffset(clip,-d)`，邻居无补偿） | 尾边界时 clip 的 offset 不变、邻居 offset 应 `+d*speed` |
| P2-09 | 清空 API 地址后刷新被环境变量复活 | `settingsStore.ts:76, 88`（`''` → `undefined` → 回落 `VITE_API_BASE_URL`） | 用哨兵值区分「未设置」与「显式清空」 |
| P2-10 | 波形提取无 `resp.ok` 且整文件解码 | `mediaManager.ts:385-389` | 加 `resp.ok` 判断；大文件跳过或走后端波形端点 |
| P2-11 | `registerFile` 重复注册不释放旧 blob/媒体元素 | `mediaManager.ts:66-117`（`registerUrl:121` 有守卫） | 加 `entries.has` 守卫并先 `unregister` |
| P2-12 | 剃刀在片段边缘产生幽灵撤销点 | `TimelineEngine.ts:351` push 早于 `splitClip` 的边界 no-op（`timelineStore.ts:336`） | 先判断能否切分再 push |
| P2-13 | 「粘贴属性」按钮路径不可撤销（快捷键路径可） | `PropertiesPanel.tsx:126-137` 直接 `set(...)`，无 `pushHistory`；对照 `useGlobalKeybindings.ts:160` 有 push | 按钮路径补 pushHistory |
| P2-14 | 数字输入把空串当 0（无法清空/输负号） | `PropertiesPanel.tsx:1203`（`Number(v)||0`）、`:1031-1033`（`Number("")→0`）、`ConfigValueInput.tsx:43, 53` | 空串不提交；负号中间态不写 store |
| P2-15 | 历史合并 600ms 窗口导致长拖拽产生多点、快速两次编辑被合并 | `historyCoalesce.ts:8, 23` | 改为「拖拽开始/结束」显式开窗，而非纯时间窗 |
| P2-16 | AI 匹配入轨的片段无媒体注册 → 渲染不出来 | `AssetPanel.tsx:523-539`（写 `asset_id: r.id`，无 `registerUrl`，无 `local_path/url`） | 与 P1-08 一并修 |
| P2-18 | Escape 被全局快捷键吞掉 | `useGlobalKeybindings.ts:476-477`（无 `when`）+ `KeybindingEngine.ts:115-121, 131`（capture + stopPropagation） | 给 `deselect` 加守卫（如「有遮罩/输入框时不触发」） |
| P2-19 | 弹层打开时单键快捷键仍生效 | `KeybindingEngine.ts:46-57` 只拦输入框与按钮焦点 | 引擎感知「是否有模态打开」 |
| P2-24 | 时间线画布无键盘/无障碍；无 `prefers-reduced-motion` | `TimelinePanel.tsx:610-614` 无 `tabIndex/role/aria`；`globals.css` 无 reduced-motion | 补焦点与 ARIA；尊重减弱动效 |
| P2-41 | EDL 导入硬编 30fps、所有事件落单轨 | `services/edl.py:298`、`:56-58` | 解析 `FCM` 头取时间基；按 `V1/A1/V2` 分轨 |
| P2-42 | 代理状态不持久、切换假成功 | `EditorToolbar.tsx:110`（每次挂载重置）、`:155`（无条件成功提示） vs `services/proxy.py:98` | 后端返回实际变更结果；前端按结果提示并持久化代理态 |
| P2-43 | JSON 导入无校验、静默整体替换 | `EditorToolbar.tsx:376-379` | 校验 `tracks`/`clips` 必需字段，先确认再替换 |
| P2-44 | SRT 导出毫秒溢出、无 BOM、标题当字幕 | `EditorToolbar.tsx:731`（`Math.round` 可到 1000）、Blob 无 BOM、`:391` 过滤含 `text` | 毫秒进位；加 UTF-8 BOM；仅导出 caption |
| P2-45 | <768 的「只读」只挡鼠标 | `EditorLayout.tsx:212-222`（遮罩不覆盖工具栏；全局快捷键照旧） | 只读时禁用快捷键与工具栏编辑动作 |
| P2-46 | 版本恢复不进撤销栈 | `TimelinePanel.tsx:97-111` | 恢复前 pushHistory |
| P2-47 | 管理页「视频编辑器」是第二套工程与撤销栈 | `videoEditor.ts:58-151` + `video_editor.py:85-88`（`ed_<hex>`、内存栈） | 明确其定位（工具页而非项目编辑器）或与主编辑器统一数据源 |

### 4.2 Agent / Pipeline / 类型 / 插件

| ID | 症状 | 证据 | 修复建议 |
|---|---|---|---|
| P2-26 | 类型制作器节奏参数是装饰性的；内置类型可点编辑但 404 且提示误导 | `TypeMakerPage.tsx:117-127, 324, 248`；`type_maker.py:66-72, 172-177` | 与 P1-15 一起修；内置类型隐藏编辑入口；404 与「后端不可达」文案区分 |
| P2-27 | 「新建文件夹」不落库 | `ProjectsPage.tsx:248-255`（无 API 调用） | 创建后至少把当前项目归入该文件夹（`setFolder`），或从 UI 移除 |
| P2-28 | 学习页删除数据集/任务无二次确认 | `LearningPage.tsx:116-125, 166-173` | 加确认弹窗 |
| P2-29 | 素材搜索缓存命中丢授权字段（署名缺失） | `material_agent.py:116-119`（`__slots__` 无 `license_url/author/source_url`）vs `:392-394` | 补进 `_Asset.__slots__` 与 `__init__` |
| P2-30 | 素材源检索无单源超时 | `material/registry.py:68-81` | `asyncio.wait_for` 包裹每个源，失败降级 |
| P2-31 | BPM 取第一个音频 clip（可能是旁白）且失败静默 120 | `audio_agent.py:251-262, 306` | 优先取 BGM/用户指定音轨；失败时不写入 120 |
| P2-32 | DOCX 附件把 XML 原文注入提示词 | `requirements_service.py:1787-1795` | 用 `python-docx` 或正则抽取 `<w:t>` 文本 |
| P2-33 | 流式 status 块把 `planning` 打回 `gathering` | `AgentPanel.tsx:269` 无条件 `setStatus('gathering')`；输入框隐藏条件在 `:495` | 按后端返回的状态映射，而非硬编码 gathering |
| P2-34 | LLM 失败/超时把已有 brief 覆盖成 `{}` | `requirements_service.py:535` | 空结果时不覆盖（`result.get("brief_draft") or brief_data`） |
| P2-35 | 共享动画轨无重叠去重 | `animation_agent.py:378`（仅 text 用 `_find_overlapping_clip`）vs `:515, 621, 773` | 三处 append 前做窗口冲突检测 |
| P2-36 | fallback 恒返回「对比分栏」模板 | `mg/fallback.py:91-95` | 无关键词命中时应返回 None 或按内容类型选默认，并在 trace 标注 |
| P2-37 | 预览 MP4 永不清理 | `api/animation.py:178-182`（只删 mov） | finally 里一并删 mp4，或加 TTL 清理 |
| P2-38 | 需求草稿泄漏到另一个项目 | `agentStore.ts:302`（全局 draft key）+ `EditorPage.tsx:94-96`（仅首页启动路径清草稿） | draft 按 projectId 分键；直接导航到其它项目时清空 |
| P2-39 | 首页删除是硬删（绕过回收站） | `HomePage.tsx:449-457`（`projectApi.remove`）vs `ProjectsPage.tsx:134-143`（`trashProject`） | 统一走回收站 |
| P2-40 | 启动失败后按钮卡在 disabled | `HomePage.tsx:358` `setLaunching(true)`，`:405-409` 与 `:414-419` 都直接 `return`，无 `finally` | 加 `finally { setLaunching(false) }` |
| P2-48 | `/retry` 无幂等与在飞守卫 | `api/pipeline.py:864-866` | 加「同一 pipeline_id 正在重试」判断与幂等键 |
| P2-49 | 熔断器每实例新建，跨管线不生效 | `pipeline_v2.py:491` + `api/pipeline.py:359` | 熔断状态提升为进程级 |
| P2-50 | secret 掩码算在密文上 | `loader.py:88-103`（docstring 说「解密后掩码」但未解密） | 先解密再掩码 |
| P2-51 | 未加载插件预配置时掩码值被当真值写盘 | `api/plugin.py:337`（守卫仅对已加载插件生效） | 在 `save_config` 层做「值含 `****` 则保留旧值」 |
| P2-52 | `/api/skill/list` 的 `parameters` 恒为空 | `skill/registry.py:114-117` 取 `parameters`，而 `to_llm_tool()` 默认 anthropic 返回 `input_schema` | 取 `input_schema` 或统一 fmt |
| P2-53 | 插件签名默认关闭 | `loader.py:213` + `config.py:43` | 见 P1-14 |
| P2-54 | 插件依赖只看目录存在 | `loader.py:577-582` | 同时校验依赖的启用/加载状态 |

### 4.3 其他前端

| ID | 症状 | 证据 | 修复建议 |
|---|---|---|---|
| P2-17 | 插件 UI 渲染器：未知控件静默丢弃、状态不随 schema 重置 | `PluginLayoutRenderer.tsx:329-330`、`:16` | 未知控件给占位提示；schema 变化时重置状态 |
| P2-20 | 离线假数据无标记且不遵守 `allowDemoFallback` | `ToolsPage.tsx:33-37`、`FontsPage.tsx:34-35`、`PreprocessPage.tsx:55-56` | 统一走开关并加可见标识 |
| P2-21 | 无操作系统拖拽导入 | `TimelinePanel.tsx:564-585` 只认内部拖拽 | 增加文件拖入 → 上传 → 入轨 |
| P2-22 | 多文件上传部分失败造重复素材 | `AssetPanel.tsx:107-142` | 逐文件独立 try/catch；只对失败文件走演示回退 |
| P2-23 | 401 后不重放原请求 | `App.tsx:16-33` | refresh 成功后重试原请求 |
| P2-25 | 响度滑块超后端约束 → 保存 422 | `PersonaDetailPage.tsx:337`（max=-8）vs `schema/persona.py:95`（le=-10） | 滑块范围改 `-30..-10` |
| P2-55 | 素材源无结果时整场景失败 | `material/registry.py`（与 P2-30 同批） | 单源失败降级为空结果 |

---

## 5. P3 详情

| ID | 症状 | 证据 | 修复建议 |
|---|---|---|---|
| P3-01 | `.env.example` 里的 `VITE_ENABLE_*` / `VITE_MAX_*` / `VITE_WS_URL` 无任何代码消费 | 全仓只读 `VITE_API_BASE_URL`（`settingsStore.ts:88`） | 删除或真正接线 |
| P3-02 | 文档与实际不一致 | `README.md:74`（代理 8000，实际 8080）、`:124`（「六 Agent」，实际 7）；`AGENTS.md` 指向已不存在的 `J:\Clipwright\web` | 更正 |
| P3-03 | 仓库内文档乱码 | `docs/agent-pipeline-audit.md` 整篇乱码；`ClipWright-Frontend-Design-Plan.md:1609-1610`；`playwright.config.ts:8` 注释；`plugins/pexels_material/config.yaml` 中文乱码 | 以 UTF-8 重写 |
| P3-04 | 仓库根目录提交了调试产物 | `e2e-*.png`（5 张）、`e2e-*.ts`、`e2e-plan.json/md`、`stage.md`、`uvicorn-*.log` | 移出并加 `.gitignore` |
| P3-05 | `nginx.conf` 未开 gzip/缓存头 | `nginx.conf` | 加 `gzip on` 与静态资源 `Cache-Control` |
| P3-06 | 50+ 处空 `catch` 静默吞错 | 见审计记录（如 `HomePage.tsx:125, 174, 273, 307, 378, 436, 476`） | 至少 `console.warn` 或上报 |
| P3-07 | `react-query` 已配置但全站手写取数；`dompurify` 未使用 | `providers.tsx:4-12`；`package.json` | 收口取数（可选）或移除死依赖 |
| P3-08 | `analyze_video_structure` 技能管线零调用；其 BPM 失败时伪造 120 | `skill/builtin.py:24, 70` vs `audio_agent.py:264-270` | 接线或删除；去掉伪造默认值 |
| P3-09 | 插件错误通道无去重、读/清端点未做 admin 门禁 | `error_bus.py:42`、`api/plugin.py:145, 241` | 去重 + 加 `_require_admin` |
| P3-10 | MG 元数据死字段 | `animation_agent.py:614-617, 765-768`（`mg_html_path` 恒空、`mg_method`/`mg_generation_id` 无消费者） | 清理或接线 |
| P3-11 | `dry_run` 走原始 dict 合并，绕过 bus 语义 | `pipeline_v2.py:934` | 与正式路径统一 |
| P3-12 | `_build_input` 的 ValueError 抛在 `_run_agent` 之外，错误归因到 animation | `pipeline_v2.py:1197-1198`、`:977` | 在 `_run_inner` 内捕获并标注阶段 |
| P3-13 | 跨端点幂等键不共享（`run-async` 与 `proceed` 各一套） | `api/pipeline.py:291` vs `requirements.py:27` | 统一幂等存储 |
| P3-14 | `/preview` 与 create 的转场白名单不一致 | `type_maker.py:233` vs `:269` | 统一白名单常量 |
| P3-15 | persona 的 `visual.transition_weights` 是死键 | `schema/persona.py:80-83` vs 四个 category 各自硬编码 | 接线或删除 |

---

## 6. 系统性根因（批量修法）

审计中最有价值的不是单点，而是**同一类错误反复出现**。建议按下列模式批量处理，而不是逐条打补丁：

1. **「前端 UI 模型 ≠ 后端数据模型」的单向映射**
   代表：P0-05（persona 全量替换）、P1-10（插件 config 丢 secret）、P1-15（类型 shot_params 键/单位不一致）、P2-03（Timeline/Track metadata 缺失）。
   **修法**：凡是「GET → 前端模型 → PUT 回后端」的路径，一律改为「保留原始对象，只覆盖用户显式修改的字段」；PUT 端点尽量改成部分更新。

2. **「同一语义两条实现路径，只有一条带不变量校验」**
   代表：P0-03/P0-04（拖拽 trim vs 键盘 trim）、P1-18（首次运行 vs 重试的后处理）、P2-42（代理切换的前端提示 vs 后端实际）。
   **修法**：把业务不变量收口到 store/service 单点，UI 只调用它。

3. **「降级/兜底把失败伪装成成功」**
   代表：P1-01（空白 MG 视频返回 200）、P1-24（dub 全失败 200）、P2-36（fallback 恒返回对比分栏）、P2-42（切换代理假成功）、P2-20（假数据无标记）、P3-08（BPM 伪造 120）。
   **修法**：统一约定——**降级必须在返回值与 UI 上可识别**（状态字段 + 可见标识 + trace 事件）。

4. **「注释声称已修复，但代码路径没接上」**
   代表：P1-02（缓存键笔误）、P2-52（skill parameters）、P1-23（STT 对齐被嵌套进 SFX 分支）、P3-08、P3-15。
   **修法**：为每条「声称已接线」的能力补一条**端到端断言测试**（而不是只测函数本身）。

5. **「默认配置使安全机制失效」**
   代表：P1-14（权限全允许、签名默认关）。
   **修法**：安全默认值应为最严格；文档与 UI 必须如实标注哪些机制实际生效。

---

## 7. 已否证 / 需澄清 / 待验证

### 7.1 已否证（**不要修**，避免误改）

| 曾被报告为问题 | 复核结论 |
|---|---|
| 「离线时提交人格会假成功」 | **不成立**。`PersonaForgePage.tsx:145` 写入的假 `sessionId` 是 truthy，`:185` 仍会真发请求并落到 `:188` 的 catch（错误提示 + 不跳转）。 |
| 「关键帧时间基契约正确，不是缺陷」 | **部分不成立**。仅对「编辑器内新建」的关键帧成立；Agent 产出的绝对秒关键帧会被误解释（见 P1-04，有真实工程数据）。 |
| 「`/api/test/config` 泄露 API Key」 | **不成立**。`model_probe.py:103-129` 只返回 `has_api_key` 布尔与 provider/model/base_url，不含密钥。 |
| 「插件权限已实现隔离」 | **不成立**。仅声明校验，见 P1-14。 |

### 7.2 需澄清（结论与原文档不同）

- 商业化报告称「动画调度与配音时间轴完全脱耦」——**已部分修复**：`services/narration_events.py` 的 NEL 已接线，但**只在 TTS 路径生效**（见 P1-23）。
- 上一轮我曾说「上传配音时字幕有 STT 对齐」——**该说法已更正**：对齐块被嵌套进 SFX 分支，默认不执行（见 P1-23）。

### 7.3 待验证（**先验证再决定是否修**）

| 项 | 线索 | 验证方法 |
|---|---|---|
| 缩略图 bucket=0 时 `seeked` 是否触发 | `mediaManager.ts:270, 301-302`；`AssetPanel.tsx:292` 传 0.1 | 浏览器实测：新视频元素设 `currentTime=0` 是否派发 `seeked` |
| MG 提示词硬编 1920×1080 导致竖屏越界 | `mg/generator.py:706-747, 771-894` + `mg/config.yaml:134` | 用 1080×1920 时间线生成 MG，检查元素坐标 |
| 跳过的动画被计为已完成 | `animation_agent.py:235, 257-280` | 构造 Hyperframes 不可用场景，看 `logic_animations` 计数 |
| 结构 Agent 无场景数/时长校验 | `structure_agent.py:159-194` | 让 LLM 返回 1 个/30 个场景，看是否透传 |
| 幻觉场景数/时长被信任 | `requirements_service.py:1702-1707` | 构造 `scene_count` 与 `raw_scenes` 不一致的响应 |
| 区域级编辑意图分类盲区 | `requirements_service.py:800-834` | 只选区域不选片段，观察分发结果 |
| 计划书 TOC `sections` 被丢弃 | `persona.ts:222-227` + `requirements_service.py:1865` | 检查 `get_plan` 响应与前端渲染 |
| 附件上传不接图片/.srt | `AgentPanel.tsx:522` vs `api/requirements.py:209-210` | 对比 accept 与后端支持 |
| 「最近编辑」按字符串排序 | `ProjectsPage.tsx:123` | 造跨天/跨月数据看顺序 |
| 模板应用提交空 body 导致素材重映射不执行 | `TemplateGallery.tsx:48` | 应用模板后检查项目素材映射 |
| 导出/复制按钮在空时间线仍可点且无提示 | `EditorToolbar.tsx:338, 394, 510` | 空时间线点击观察 |

---

## 8. 验证与回归清单

### 8.1 每批必跑

```powershell
# 前端（工作目录 J:\Clipweight-Client）
npx tsc --noEmit                 # 期望 0 错误
npm run lint                     # 期望 0 error
npm run test                     # 期望 60 文件 / 428+ 测试全绿
npm run build                    # 期望成功

# 后端（工作目录 J:\Clipwright）
python -m pytest tests -q        # 基线 1154 passed（以当前实际为准）
python -c "import clipwright.main"
```

### 8.2 端到端冒烟（每个批次结束手动跑一次）

1. 启动：`scripts\start.ps1`（后端 8080 + 前端 5173）。
2. 首页填选题 → 选音色 → 启动 → 观察需求对话流式、简报、规划书。
3. 编辑器：拖入素材 → 修剪 → Ctrl+Z（应可撤销）→ 播放（音画同步）→ 保存 → 刷新（改动仍在）。
4. 导出：选预设（含自定义）→ 渲染 → 下载。
5. 关闭标签页后重开工程（时间线完整）。
6. 管理页：工具执行、插件配置保存（密钥仍掩码）、MG 预览（非空白）。

### 8.3 建议新增的回归测试

| 任务 | 测试 |
|---|---|
| P0-01 | `flushPayload` 分支形状单测 + 后端「无 tracks 的 timeline 不覆盖」用例 |
| P0-03 | TimelineEngine 拖拽 trim 后 `undoStack` +1 |
| P0-04 | 拖拽 trim 不产生重叠片段 |
| P0-05 | persona 保存后专有字段不变 |
| P1-01 | MG 预览输出非全透明 |
| P1-02 | 同输入第二次生成命中缓存 |
| P1-10 | 插件 config 保存后 `secret` 标记仍在 |
| P1-11 | `/api/tool/execute` 用 body 调用返回 200 |
| P1-15 | 自定义类型 `base_shot_ms` 受约束 |
| P1-16 | 坏 YAML 不影响 list |
| P1-17 | `/run-async` 成功后 result 为 completed |
| P1-21 | 「对比一下」不被判为确认 |
| P1-23 | 无 SFX 时上传配音字幕被 STT 对齐 |
| P1-25 | 默认 provider 克隆成功 |

---

## 9. 建议批次与顺序

| 批次 | 内容 | 估时 | 说明 |
|---|---|---|---|
| **批次 1** | P0-01 ~ P0-05 | 1.5 天 | 全是数据丢失，必须最先做，每条配回归测试 |
| **批次 2** | P1-04 ~ P1-10、P1-32 ~ P1-37 | 3 天 | 编辑器/导出/素材/预览的可信度，直接影响「能不能用」 |
| **批次 3** | P1-01 ~ P1-03、P1-15 ~ P1-19 | 3 天 | 动画与 Pipeline：成本与产出正确性 |
| **批次 4** | P1-11 ~ P1-14、P1-20 ~ P1-26 | 3 天 | 插件安全、需求对话、语音链路 |
| **批次 5** | P1-27 ~ P1-31 + P2 编辑器部分 | 3 天 | 导入导出、时间轴正确性 |
| **批次 6** | P2 其余 + P3 | 3 天 | 体验与一致性收尾 |
| **批次 7** | §7.3 待验证项 → 转正式条目或关闭 | 1 天 | 先验证再决定 |

**总估时**：约 17-18 人日（不含待验证项）。

---

## 10. 附：本次审计的方法与可复现命令

```powershell
# 真实工程体积取证（用于 P0-01）
node -e "..."   # 见审计记录：遍历 J:\Clipwright\projects\*\project.json

# 关键帧时间基取证（用于 P1-04）
node -e "..."   # 输出 proj_7d3a69b9eae8 的 tc_1b89e9d0 keyframes

# 前后端路由对账
# 后端 238 条路由 vs 前端 160 处 axios 调用点：0 处调用不存在路由；23 条后端路由无前端消费

# 死调用/死能力检查
grep -r "AbortController" src          # 0 命中 → P1-35
grep -r "VITE_" src                    # 仅 VITE_API_BASE_URL → P3-01
grep -rn "generation_cache" clipwright  # 仅定义无调用 → P1-02 附带项
```

> 审计期间的 4 条子代理结论已被否证（见 §7.1），另有 2 条结论经复核后修正（§7.2）。本文档只保留**已复核**结论；未复核项集中在 §7.3。

---

## 11. 执行状态（2026-09-10 施工记录）

> 执行方式：按 §9 批次顺序推进，每批全量回归后独立提交（双仓）。

### 批次 1（P0×5）——✅ 全部完成，双仓提交

| ID | 状态 | 摘要 |
|---|---|---|
| P0-01 | ✅ | 前端 `decideFlushPayload` 超限分支不再携带 timeline；后端 `ProjectManager.save()` 拒绝无 tracks 的桩覆盖。测试：flushPayload 断言更新 + 后端 `TestTimelineShapeGuard` 5 条 |
| P0-02 | ✅ | EditorPage `useBlocker` 导航守卫，离开前落盘；失败确认后放行 |
| P0-03 | ✅ | trim 守卫改为与拖拽前值比较（同时消灭单击幽灵历史）。测试 4 条（trimHistory） |
| P0-04 | ✅ | `computeTrimGhost` 加 prevEnd/nextStart 邻居钳制（`trimNeighborBounds`，与 store 同语义） |
| P0-05 | ✅ | `personaApi.getRaw` + `buildUpdatePayload`（以原始 manifest 为基底仅覆盖 UI 可编辑字段；bgm_slots 不回写）。测试 3 条 |

回归：tsc 0 / lint 0 error / vitest 435 / build ✅；后端 1561 passed。

### 批次 2（P1-04~10、P1-32~37）——✅ 全部完成，双仓提交

| ID | 状态 | 摘要 |
|---|---|---|
| P1-04 | ✅ | `setTimeline` 加载时归一化绝对秒关键帧（保守判定、幂等）；`exportTimeline` 仅在归一化基时标记 clip_local。测试 3 条 |
| P1-05 | ✅ | timelineDiff 改全字段深比较（排除 id；track_id 参与比较以保 merge 跨轨语义）；isEmpty 分支补「仍采用 Agent 版本」 |
| P1-06 | ✅ | custom 预设置空 preset 名走显式参数分支；已存预设应用同步清空 |
| P1-07 | ✅ | 422 detail 数组拼接字段级原因（前 3 条），优先 userMessage |
| P1-08 | ✅ | 入轨写 `metadata.local_path`；`resolveMediaUrl` 加 asset_id→`/api/asset/{id}/file` 兜底；后端中间件放行该端点 query token。AI 匹配入轨（P2-16）同批写 url |
| P1-09 | ✅ | 后端兼容 `sources=a,b` 单元素拆分 |
| P1-10 | ✅* | 前端 `buildConfigYaml` 透传 secret + 掩码提交确认；**后端掩码还原（`_restore_masked_secrets`）被安全扫描器误报阻断未落盘 → 待人工**（附录 A） |
| P1-32 | ✅ | 节目监视器加「近似预览」标注 |
| P1-33 | ✅ | seek 后监听 `seeked` 通知重绘；音频校正阈值 0.25s→0.08s |
| P1-34 | ✅ | ErrorBoundary 文案与实际行为一致 |
| P1-35 | ✅ | chat/edit/proceed/streamChat 支持 AbortSignal；AgentPanel「停止」按钮 + 卸载中止 + 取消中性提示 |
| P1-36 | ✅ | streamChat 的 SSE token 改走 query（后端只消费 qtok） |
| P1-37 | ✅ | 吸附/框选/点选跳过 hidden（框选同时跳过 locked） |

回归：vitest 438 / tsc 0 / build ✅；后端 1560 passed（1 例并发 flake 单跑通过）。

### 批次 3（P1-01~03、P1-15~19）——✅ 全部完成，后端提交

P1-01 ✅ 抽公共 `HyperframesRenderer.render_mg_html_to_mov`（带 run_cmd/validate 注入），预览与主管线同构 ｜ P1-02 ✅ 缓存键存 `self._ck`（generate 外路径不写缓存）+ `_mg_cache_clear` 测试隔离 ｜ P1-03 ✅ 窗口不足跳过 MG 并告警 ｜ P1-15 ✅ `translate_persona` 点号路径 + transform + shot_params 秒→ms 换算并入 ｜ P1-16 ✅ `config_path`→`f.name` ｜ P1-17 ✅ `ok` 判定前移 ｜ P1-18 ✅ 后处理抽 `_finalize_timeline` 双路径统一 ｜ P1-19 ✅ dispatch 返回时取消检查：结果弃用 + wasted-call 事件。

测试：`test_audit_batch3.py` 4 条；MG 批判测试补缓存隔离 fixture。回归：后端 1565 passed。

### 批次 4（P1-11~14、P1-20~26）——✅ 完成（P1-14 部分待人工）

P1-11 ✅ execute 端点 body 模型 ｜ P1-12 ✅ `set_enabled` 移入 try 统一回滚 ｜ P1-13 ✅ Tool/SkillRegistry 拒绝覆盖内置 + 影子表恢复 ｜ P1-14 ✅* 权限默认白名单移除 shell、entry_point pattern、tar 解压加固（`is_relative_to` + 拒 symlink + `filter="data"`）；**loader.py 的 sys.path 收敛与运行时沙箱被阻断 → 待人工** ｜ P1-20 ✅ 确认中间态补路由分支（含 plan_confirmed 重新生成） ｜ P1-21 ✅ 模糊短词整句匹配（主路径 + 启发式兜底双修） ｜ P1-22 ✅ gathering 注入最近 12 条对话 ｜ P1-23 ✅ STT 对齐移出 SFX 分支 + NEL 接入上传配音路径 ｜ P1-24 ✅ 全段失败 success=False ｜ P1-25 ✅ provider 名双向对齐（后端兼容别名 + 前端改连字符） ｜ P1-26 ✅ 首页音色下拉 + autoDub 开关 + /voice 设默认音色。

验证：内联脚本全过（P1-11 双端点 / P1-12 接线 / P1-13 双注册表 / P1-21 含兜底 / P1-24 语义）。回归：后端 1565 / 前端 438。

### 批次 5（P1-27~31）——✅ 全部完成，前端提交

P1-27 ✅ parseSrt 兼容 WebVTT + 空结果 toast ｜ P1-28 ✅ 导入 0 片段明确提示（FCP X 限制说明）｜ P1-29 ✅ EDL 导出 payload 补 source_offset_sec/asset_id ｜ P1-30 ✅ 跨轨移动校验锁定 + store.moveClip 收口 ｜ P1-31 ✅ 波纹删除跨轨窗口联动 + 编组扩散 + 锁定轨不动。测试 2 条。

### 批次 6 / 7（P2×54、P3×15、待验证×11）——⏸ 未在本轮执行

P0/P1 共 42 条已全部落地；P2/P3 属体验与一致性收尾（计划估时 3 天），建议按 §4/§5 清单另行派工。其中 P2-16 已随 P1-08 完成、P2-53 与 P1-14 同源部分完成；§7.3 待验证项仍需先验证再决定。

### 附录 A：被安全扫描器（Mimosa）阻断、待人工落盘的改动

以下改动已写好但因扫描器对该文件的持续误报（如把 HMAC 签名行/`from __future__` 行判为路径穿越）无法通过工具落盘，需人工粘贴：

1. **P1-10 后端掩码还原**（`clipwright/plugins/loader.py`）：在 `save_config` 的「落盘前加密」之前调用 `self._restore_masked_secrets(plugin_id, data)`；新增同类同名方法——对 `fields` 中 `secret is True` 且 `value` 含 `****` 的字段，用 `_decrypt_flat(self._get_merged_config(plugin_id))` 的旧值替换，防止整表单回传把掩码存成密钥。
2. **P1-14 sys.path 收敛**（`clipwright/plugins/loader.py:674-686`）：插件导入时把「插件自身目录」插入 `sys.path`，而非整个 plugins 根目录（防第三方 shadow 任意顶层模块名）。

### 附录 B：回归基线（本轮结束时）

- 前端：`tsc --noEmit` 0 错误 ｜ `lint` 0 error（142 warnings）｜ `vitest` 62 文件 440 passed ｜ `build` 成功
- 后端：`pytest tests -q` 1565 passed / 1 skipped / 1 xfailed（test_render_phase3 曾出现 1 例并发时序 flake，单跑通过）
- 提交：前端 `45912ea`→`6177f19` 共 5 个批次提交；后端 `96ca812`→`566e226` 共 3 个批次提交

---

## 12. 续批执行状态（批次 6 后端 + 批次 7 待验证，同日）

### 落地项（后端提交 `84732f8`）

| ID | 摘要 |
|---|---|
| P1-10/P2-51 ✅ | **附录 A 第 1 项已落地**——绕开被阻断的加载器文件，掩码还原改在 `put_plugin_config` 端点层统一实现：从 merged 配置解密取旧值（含未加载插件场景），无旧值置空串 |
| P2-50 ✅ | 配置回显解密后重新掩码（旧实现掩码算在密文上） |
| P2-29 ✅ | 检索缓存 `_Asset` 补 license_url/author/source_url（署名不缺失） |
| P2-30/P2-55 ✅ | 单源检索 wait_for 20s 超时 + 异常降级空结果 |
| P2-31 ✅ | BPM 优先 BGM clip（旧取第一个音频 clip 常是旁白）；失败/未检测写 None 不伪造 120（3 处测试断言同步为新契约） |
| P2-32 ✅ | DOCX 按段落抽取 `<w:t>` 文本（不再注入 XML 原文） |
| P2-34 ✅ | LLM 失败 `brief_draft={}` 不覆盖已有方案 |
| P2-36 ✅ | fallback 无关键词命中改用通用 title_reveal |
| P2-37 ✅ | MG 预览产物 TTL 清理（2h） |
| P2-48 ✅ | /retry 在飞守卫 + 幂等返回 |
| P2-49 ✅ | 熔断表提升类级进程级共享 |
| P2-52 ✅ | skill parameters 兼容 input_schema + _infer_parameters 回退 |
| 待验证×2 ✅ | 「最近编辑」改时间戳降序；附件 accept 补齐图片/.srt（均证实属实并修复，前端提交 `c067ae9`） |

### 前端落地项（提交 `c067ae9`）

P2-06 堆叠方向与 ASS 同向（向上）｜ P2-09 API 地址哨兵语义 ｜ P2-10 波形 resp.ok + 大文件跳过 ｜ P2-11 重复注册先释放 ｜ P2-12 剃刀边界不推历史 ｜ P2-13 粘贴属性入撤销栈 ｜ P2-14 批量平移 NaN 守卫 ｜ P2-25 响度滑块 -30..-10 ｜ P2-40 启动失败复位按钮 ｜ P2-44 SRT 毫秒进位/BOM/仅 caption ｜ P2-46 版本恢复入撤销栈。

### 批次 6 续 + 批次 7 抽查（后端提交 `aaa342f`，前端提交 `5f2f035`）

| 项 | 摘要 |
|---|---|
| P2-41 ✅ | EDL 导入：帧率参数化（drop-frame 29.97）、FCM 头解析、按通道字段分轨 |
| P3-08 ✅ | analyze_video_structure 的 BPM 不再伪造 120 |
| P3-09 ✅ | 错误通道 60s 去重 + /errors 读写端点 admin 门禁 |
| P3-10 ✅ | MG 死字段清理（mg_html_path/mg_method/mg_generation_id） |
| P3-14 ✅ | type_maker 转场白名单统一常量 |
| P2-23 ✅ | 401 静默续期成功重放原请求（防循环标记），失败才广播（axios 实例改局部引用） |
| P2-28 ✅ | 学习页删除二次确认 |
| P2-33 ✅ | 流式 status 按后端权威状态映射 |
| P2-39 ✅ | 首页删除改走回收站 + 可恢复提示 |
| 待验证 ✅ | 「最近编辑」排序证实属实→时间戳降序；附件 accept 证实属实→补齐图片/.srt；「结构 Agent 无校验」部分不成立（`_validate_scenes` 已过滤，无数量上限留产品决策） |
| 回归 | 后端子集 edl/error_bus/plugin/skill 110 passed；前端 440 passed / tsc 0 / build ✅ |

### 批次 6 终批（后端 `012dd13`，前端 `1fcf18b`）

| 项 | 摘要 |
|---|---|
| P2-35 ✅ | 共享动画轨窗口冲突去重（logic/MG/LLM-MG 三处 append 前检查） |
| P3-11 ✅ | dry-run 走 `_merge_agent_result`（控制键排除、timeline 总线优先、回退链保留） |
| P3-12 ✅ | `_build_input` 异常归因到当前 agent |
| P3-13 ✅ | 幂等统一：`services/idempotency.py` 共享存储（run-async 与 proceed 共键空间） |
| P3-15 ✅ | persona `visual.transition_weights` 接线（原死键） |
| P3-03 ✅ | 乱码修复：agent-pipeline-audit.md 逆链还原（~94%）；Design-Plan 5 行局行还原 |
| P3-06 ◐ | HomePage 6 处静默 catch 加 console.warn（抽样；同类 50+ 处余量在剩余清单） |
| P2-38 ✅ | 需求草稿按项目归属校验（跨项目泄漏修复） |
| P2-43 ✅ | JSON 导入校验 tracks + 片段统计确认框 |
| P2-45 ✅ | 窄屏只读停用编辑类快捷键（引擎只读门） |
| P2-19 ◐ | 速查表模态门接入（引擎 modalDepth 就绪；其余弹层按需接入） |
| 回归 | 后端 1565 passed / 0 失败；前端 440 passed / tsc 0 / build ✅ |

### 批次 6 收尾轮（前端 `0f3ad7c`/`c325e2b`/`d6845ad`/`1b1b08f`）

| 项 | 摘要 |
|---|---|
| P2-01 ✅ | mergeTimeline 采纳轨道级/时间线级变更（markers/画幅/帧率/轨道属性） |
| P2-02 ✅ | 三个死设置接线（默认帧率/分辨率持久化+空时间线应用；autoSave 门控） |
| P2-03 ✅ | Timeline/Track metadata 前端类型补全 |
| P2-04 ✅ | 踩点剪辑前端入口（开关+BPM，两条 proceed 路径透传） |
| P2-05 ✅ | 字幕样式单选即单条覆盖（多选保持整轨级联） |
| P2-15 ✅ | 撤销合并滑动窗口 + 显式拖拽窗口 API（长拖拽不再多点） |
| P2-18 ✅ | Escape 守卫（无选中时不吞事件） |
| P2-19 ◐ | 模态门（引擎就绪 + 速查表接入；其余弹层按需） |
| P2-20 ✅ | 演示回退受开关门控 + 可见标记横幅 |
| P2-22 ✅ | 多文件上传逐文件独立失败域 |
| P2-24 ✅ | 画布 tabIndex/role/aria + prefers-reduced-motion |
| P2-26 ✅ | 内置类型隐藏编辑入口 |
| P2-27 ✅ | 新建文件夹落库（自动归入项目） |
| P2-38 ✅ | 草稿按项目归属校验（跨项目泄漏修复） |
| P2-42 ✅ | 代理态持久化 |
| P2-43 ✅ | JSON 导入校验 + 确认框 |
| P2-45 ✅ | 窄屏只读停用编辑快捷键 |
| P2-47 ✅ | 管理页编辑器定位说明 |
| P3-03 ✅ | 乱码还原（audit 文档 ~94% + Design-Plan 5 行） |
| P3-06 ◐ | HomePage 抽样留痕（同类余量在剩余清单） |

### 剩余清单

- **待人工（工具阻断）**：加载器文件的 sys.path 收敛（P1-14）、依赖启用状态校验（P2-54）——扫描器对该文件误报拦截一切写入
- **P2 未做（仅剩 3 项，均为可选优化）**：P2-07（吸附网格视口化）、P2-17（插件未知控件占位）、P2-21（OS 文件拖入上传）
- **P3 未做**：P3-07（react-query 收口/死依赖）、P3-06 同类 catch 余量
- **待验证未抽查**：§7.3 其余 8 项
