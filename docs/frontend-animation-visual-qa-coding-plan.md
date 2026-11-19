# 前端动画视觉质检 Coding Plan

## F0 事件契约

后端 `AnimationAgent` 的视觉 loop 发：

```text
visual_preview {path}
visual_capture {frames[]}
visual_inspect {findings[]}
visual_revise {before_hash, after_hash}
visual_pass / visual_fail
```

前端 `TaskEventType` 增加 `animation_visual`，detail 带 `agent=animation`。

## F1 类型/API

新增：

```text
src/features/animation/visualQaTypes.ts
src/features/animation/visualQaApi.ts
```

类型：

```ts
VisualFinding { severity, category, observation, frame, bbox? }
VisualInspect { mgDefHash, frames, findings, passed }
```

## F2 活动流

`AnimationActivityFeed` 支持：

- 帧缩略图；
- findings；
- revise 前后；
- pass/fail 徽标。

## F3 VisualQAPanel

`AnimationContextPanel` 新增 `visual` Tab：

- 帧画廊；
- findings；
- 每轮 revise；
- final 证据。

## F4 导出闸门

`exportGate.ts`：

```text
agentic && !inspect_pass => block
```

展示缺失的 inspect 证据。

## F5 生成阶段 MP4

检测 `render_preview` / `render_final`：

- 活动流 warning；
- 导出前检查；
- 后端应拒绝。

## F6 测试

- tsc；
- 组件测试：帧/findings/pass-fail；
- E2E：视觉 Tab、导出阻断；
- 不执行 mg_html。

## 文件

```text
src/features/animation/visualQaTypes.ts
src/features/animation/visualQaApi.ts
src/features/animation/VisualQAPanel.tsx
src/features/animation/AnimationActivityFeed.tsx
src/features/animation/AnimationContextPanel.tsx
src/features/animation/exportGate.ts
src/types/task.ts
```
