# 动画 Agentic 改造与 DeepSeek 出片记录（2026-11-19）

> 记录可观测动作、命令、代码改动、测试结果与产物；不包含模型隐藏 chain-of-thought。

## 1. 目标

按 `docs/animation-agentic-failure-feedback-plan.md` 执行：

- Observation / Failure 契约
- 失败分类与定位
- blocking failure 禁止 final
- repair_mg 按失败修复
- native tool calling
- 不设 max_tokens / reason 魔法上限
- 03/05 结构化修复
- DeepSeek 5 个样片 + MP4

## 2. 执行时间线

| 阶段 | 动作 | 结果 |
|---|---|---|
| 计划 | 阅读失败反馈计划、Agentic runtime、MG generator/validator/semantic | 定位失败链路 |
| 契约 | 新增 `clipwright/animation/agentic/contracts.py` | Failure / Observation |
| 账本 | 新增 `agentic/ledger.py` | blocking / fingerprint / can_final |
| 适配 | 新增 `agentic/adapters.py` | 旧结果 → Observation/Failure |
| 语义 | `semantic_layout.py` 增加结构化 failures | 03/05 可定位 |
| 对比度 | 增加 `MG_TEXT_LOW_CONTRAST` | 01/05 近黑文字 |
| 循环 | runtime 接入 ledger | 未解决失败禁止 final |
| 决策 | native tool calling | 不再解析自由 JSON |
| 提示词 | keyframes 明确禁止 x/y/content | 02 根因 |
| 校验 | 不再静默丢弃 x/y/content | 失败可见 |
| 出片 | DeepSeek 5 个 | 5/5 success |
| 渲染 | 5 个 MP4 | 5/5 渲染成功 |

## 3. 根因记录

### 01 JSON 失败

- `decide_with_llm` 旧实现 `max_tokens=300`。
- 模型返回 389 字符决策 JSON，在 `reason` 中截断。
- `json.loads` 报 `Expecting ',' delimiter`。
- 被统一包装成 `MGGenerator` 错误，看起来像 MG 失败。

修复：

- 删除 `max_tokens=300/1200`；
- 删除 reason 40 字 / `[:200]`；
- 解析失败回退 `generate_mg`；
- 最终改为 native tool calling。

### 02 keyframes 失败

- 模型在 keyframes 里写 `x/y/content`。
- validator 旧实现直接 pop，Agent 看不到失败。
- 关键帧变空后又被静默补默认帧。

修复：

- prompt 明确：`x/y/content` 只能静态，位移用 `translate_x/translate_y`；
- validator 不再静默丢弃 `x/y/content`；
- 新增 `MG_KEYFRAME_LAYOUT_PROP`；
- `repair_mg` 接收这些错误。

### 03 字体失败

- `font_family` 含 `SFMono-Regular`。
- HyperFrames 无对应 `@font-face`。
- lint 失败 `font_family_without_font_face`。
- `success=false`，`qa_errors=null`。

修复方向：

- 允许字体白名单；
- 或移除未注册 family。

### 05 多行失败

- y = 170 / 326 / 482；
- font_size = 120 / 120 / 180；
- 语义行高 = font_size × 1.4；
- 156px 行距 < 168/252px 行高。

修复：

- `MG_LAYOUT_MULTILINE_OVERLAP` 带 element_indices、observed、expected.min_gap、suggested_y；
- prompt 要求 y 递增且间距 ≥ 最大行高。

### 视觉失败

- 01/05 透明画布 + 近黑文字不可见；
- 02 标签过小/柱标签脱节；
- 03 中间标签挤压；
- 04 节点过散。

修复：

- 增加 `MG_TEXT_LOW_CONTRAST`；
- 重新生成后 01/05 可见；
- 02/03/04 仍不合格。

## 4. 代码改动

```text
clipwright/animation/agentic/contracts.py
clipwright/animation/agentic/ledger.py
clipwright/animation/agentic/adapters.py
clipwright/animation/agentic_runtime.py
clipwright/animation/agent_tool_impl.py
clipwright/animation/mg/semantic_layout.py
clipwright/animation/mg/validator.py
clipwright/animation/mg/generator.py
clipwright/agents/animation_agent.py
clipwright/prompts/engine/animation_prompt.py
scripts/generate_agentic_demo.py
scripts/render_agentic_demo.py
```

## 5. 关键提交

```text
711be44 fix(animation): 决策 JSON max_tokens 提升...
158fe18 fix(animation): 移除决策 max_tokens 与 reason 长度上限
1220bb9 fix(animation): agentic capped 后补 generate_mg
3908a50 feat(animation): 透明画布文字对比度失败反馈
56b0d55 fix(animation): 不再静默丢弃 keyframes 的 x/y/content
b86d79e fix(animation): 明确 keyframes 禁止 x/y/content，位移用 translate
f2ef729 feat(animation): keyframes 静态字段结构化失败与修复入口
```

（另有 Observation/Failure 契约与 native tool calling 提交。）

## 6. 测试记录

### 前端

```text
npx tsc --noEmit          通过
vitest                    173 files / 1422 passed / 5 skipped
npm run build             通过
```

### E2E

```text
export-tail               1 passed（14.2MB, h264 1920x1080 57.6s）
agent-edit                2 passed（修 timeline mock 后）
全量                      63/68，后修复 agent-edit
preview-fixes             4 passed / 1 failed（UX3 播放态）
unified-stream            超时
final-flow                真实渲染等待
```

### DeepSeek

最终一轮：

```text
01_title    success
02_data     success
03_compare  success
04_flow     success
05_quote    success
```

5/5。

## 7. 产物

```text
renders/agent-demo/01_title-agentic.json
renders/agent-demo/02_data-agentic.json
renders/agent-demo/03_compare-agentic.json
renders/agent-demo/04_flow-agentic.json
renders/agent-demo/05_quote-agentic.json
renders/agent-demo/agentic-summary.json

renders/agentic-samples/01_title.mp4
renders/agentic-samples/02_data.mp4
renders/agentic-samples/03_compare.mp4
renders/agentic-samples/04_flow.mp4
renders/agentic-samples/05_quote.mp4
renders/agentic-samples/contact-sheet.png
```

## 8. 未完成

- `repair_mg` 尚未完全按 failure_id 驱动；
- 03/04/02 视觉仍不合格；
- 02 的 `%` 丢失、03 标签在卡片外、04 布局散；
- 全量 E2E 未全绿；
- push 仍 SSH 阻塞；
- 5/5 success 不等于 5/5 可交付。

## 9. 结论

```text
Agentic 技术链路：可运行、可失败、可回传、可出片
视觉质量：未达标
计划：未全部完成
```
