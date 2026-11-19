# 前端动画 Agentic 适配 PRD

- 状态：Draft
- 范围：`J:\Clipweight-Client`
- 关联：`docs/animation-agentic-tool-loop-prd.md`、`docs/animation-agentic-tool-loop-coding-plan.md`

## 1. 结论

**没有完全适配。**

当前前端已有活动流/上下文/黑板/导出闸门，但只是“能看”，还没有完全对齐后端 agentic 改动：

- 预览 canvas 不支持后端新增的若干字段；
- QA 报告没有独立面板；
- Style/Brief/ShotPlan 没有完整编辑/确认；
- SSE 活动流只合并了 `tool_result`，没有完整的 `tool_call`/`skill_call`/repair 生命周期；
- 黑板写入/恢复只有 API，没有前端入口；
- 旧 `mg_def` 字段白名单与后端 contract 可能漂移。

## 2. 前端现状

已存在：

```text
src/features/animation/contextApi.ts
src/features/animation/AnimationActivityFeed.tsx
src/features/animation/ContextPanels.tsx
src/features/animation/AnimationContextPanel.tsx
src/features/animation/AnimationContextMount.tsx
src/features/animation/exportGate.ts
src/layouts/EditorLayout.tsx
```

已挂载：

```text
EditorLayout Properties 下方 AnimationContextMount
```

已支持：

- 读 context / calls / blackboard；
- 活动流展示 tool/agent；
- 点击调用详情；
- QA fail 时导出按钮调用 `/api/animation/export-gate`。

## 3. 未适配清单

### 3.1 预览渲染

`mgRenderer.ts` 未覆盖：

- `text.width/height` 固定框与换行；
- `text_align`；
- `font_italic`；
- `text_stroke`；
- `font_stretch`；
- `mix_blend_mode`；
- `object_position`；
- `background_size/background_position`；
- `border_color` 的部分路径；
- `line` 中心锚点的一致性；
- `text.height` 的 bbox 影响。

结果：后端生成的 `mg_def` 在预览中可能与成片不一致，`mgFidelity` 只会报一部分。

### 3.2 活动流

当前只映射：

```text
task event type = tool_result
detail.agent = animation
```

缺少：

- `tool_call` 阶段（进行中）；
- `skill_call`；
- `repair`；
- `qa_gate`；
- `fallback_reason`。

活动流不能完整表达“Agent 为什么从 generate 回退”。

### 3.3 QA / 黑板

`ContextPanels` 只读 JSON：

- 没有 `QAReportPanel` 分项；
- 没有 repair 前后对比；
- 不能从黑板恢复/重放；
- 不能手动“按失败项修复”。

### 3.4 Brief / 风格 / ShotPlan

目前只有 brief 数据在 `AnimationPlan`，前端没有：

- Brief 编辑/确认；
- 3 风格帧选择；
- ShotPlan/DraftSpec 查看；
- Agentic 活动的用户确认点。

### 3.5 导出闸门

`ExportPage.submitRender` 已接 `/api/animation/export-gate`，但：

- 只检查第一个失败 MG；
- 没有展示具体 QA 明细；
- 后端直接调 render API 仍可绕过。

## 4. 目标

- 预览与 `mg_def` 字段契约一致；
- 活动流能表达 tool/skill/repair/qa；
- QA/黑板可读、可操作；
- Brief/Style 可选确认；
- 导出闸门有明确原因；
- 不执行 `mg_html`。

## 5. 验收

- 新增 `text_stroke` 等字段在预览中有结果或 fidelity 标记；
- 活动流出现 `generate_mg`、`qa_mg`、`repair_mg`；
- QA fail 时导出按钮禁用并展示原因；
- Brief/风格帧可选确认；
- 手机/窄屏侧栏不挤压画布；
- `tsc --noEmit`、前端测试通过。
