# 前端动画 Agentic 适配 Coding Plan

- 关联 PRD：`docs/frontend-animation-agentic-adaptation-prd.md`
- 目标：前端完全适配后端动画 Agentic Tool/Skill 改动

## F0 契约对齐

- 从后端 `mg/validator.py` + `MGRenderer` 生成字段清单；
- 前端 `mgRenderer.ts` 建立 `SUPPORTED_STATIC_FIELDS` / `SUPPORTED_ANIMATED_FIELDS`；
- 未知字段写入 `fidelity.unsupportedProps`，不静默忽略。

## F1 预览渲染

- `text.width/height`、`text_align`、`line_height`；
- `font_italic`、`text_stroke`、`font_stretch`；
- `mix_blend_mode`（不支持则 fidelity）；
- `object_position`、`background_size/background_position`；
- `line` 中心锚点；
- `border_color`；
- 单元测试覆盖每个字段。

## F2 活动流

- 订阅 `taskStore.events`；
- 映射事件：
  - `tool_call` → running；
  - `tool_result` → success/failed；
  - `skill_call` → skill；
  - `qa_gate`/`repair` → QA/repair；
- `AnimationActivityFeed` 增加状态色、耗时、artifact；
- 点击详情。

## F3 QA / 黑板

- `QAReportPanel`：text/phone/motion/determinism 分项；
- `BlackboardPanel`：version/shots/repairs；
- 增加“按失败项修复”按钮，调用后端 repair API；
- 增加 resume/重放入口（有权限）。

## F4 Brief / 风格

- `AnimationBriefCard`：topic/benchmark/style/signature/forbidden/sources；
- `StyleFramePicker`：3 张风格帧，选择后写回 plan；
- 不做硬闸门；用户要求确认时才出现。

## F5 导出闸门

- 遍历所有 MG；
- 展示每个失败原因和 retry_action；
- QA fail 时禁用导出；
- 显示“后端仍会校验”。

## F6 测试

- `tsc --noEmit`；
- 组件测试：活动流、QA fail、黑板、Brief；
- 旧 mg_def 无新字段仍可预览；
- 手机宽度侧栏不挤压；
- 不执行 `mg_html`。

## 文件

```text
src/features/preview/mgRenderer.ts
src/features/animation/AnimationActivityFeed.tsx
src/features/animation/AnimationContextPanel.tsx
src/features/animation/ContextPanels.tsx
src/features/animation/AnimationBriefCard.tsx
src/features/animation/StyleFramePicker.tsx
src/features/animation/QAReportPanel.tsx
src/features/animation/api.ts
src/features/animation/types.ts
```

## 验收

- 预览字段覆盖；
- 活动流完整；
- QA 可读可操作；
- 导出闸门；
- tsc/测试通过。
