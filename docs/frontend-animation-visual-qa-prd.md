# 前端动画视觉质检适配 PRD

## 现状

后端已改为：

```text
generate_mg → preview_html → capture_preview_frames → vision inspect_frames → revise_mg → final
```

但前端仍只消费旧的 context/calls/QA，未展示：

- 浏览器抽帧；
- vision findings；
- revise 前后对比；
- 视觉质检是否通过；
- Agent 是否在生成阶段尝试渲染 MP4（应禁止）。

## 目标

前端能回答：

1. Agent 看了哪些帧？
2. vision 发现了什么？
3. Agent 改了什么？
4. 最终交付的 mg_def 是否通过视觉质检？
5. 有没有在生成阶段偷偷出 MP4？

## 功能

### 1. 视觉质检活动流

事件：

```text
visual_preview
visual_capture
visual_inspect
visual_revise
visual_pass / visual_fail
```

展示：

- 帧缩略图；
- findings severity/category/observation；
- revise 前后版本；
- 是否 blocking。

### 2. QA 面板

新增 `VisualQAPanel`：

- 帧画廊；
- findings 列表；
- 每轮 revise 对比；
- final 证据：mg_def hash / inspect ref。

### 3. 导出闸门

如果：

```text
mg_def.agentic=true
且没有 inspect_pass
```

前端提示“未完成视觉质检”，允许用户查看证据，但不静默放行。

### 4. 禁止生成阶段 MP4

前端活动流对 `render_preview` / `render_final` 显示警告：

```text
Agent 在生成阶段请求了 MP4 渲染
```

并支持后端拒绝。

## 验收

- 活动流显示 5 帧 + findings；
- QA 面板可展开证据；
- final 需要 inspect 证据；
- 不执行 mg_html；
- tsc/E2E 通过。
