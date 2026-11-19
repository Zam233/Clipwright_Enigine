# 动画 Agentic Tool/Skill 循环 PRD

- 状态：Draft / 待实施
- 日期：2026-11-19
- 范围：AnimationAgent、MG 生成、动画 QA、预览/审核
- 关联：`docs/animation-quality-improvement-plan.md`

## 1. 背景

当前动画生成不是完整的 Agent，而是确定性管线：

```text
AnimationAgent.execute
→ 解析动画标记
→ 组织 context
→ MGGenerator.generate
→ validation/repair/critique
→ 写 timeline
```

问题：

- 动画生成期间没有显式 `call_tool` / `load_skill`；
- 类型规则只能靠长 prompt；
- 修复靠 MGGenerator 内部逻辑，不是 Agent 自主选择；
- 校验失败仍可能 `success=true`；
- 无法按镜头选择必要规则/工具；
- 没有 Tool/Skill 调用的时间线与黑板记录。

目标是把动画阶段改成：

```text
LLM Agent
→ 自主判断
→ load_skill / call_tool
→ 看结果
→ 继续/修复/询问
→ 产出并 QA
```

## 2. 目标

- Agent 可自主调用动画相关 Tool；
- Agent 可按镜头加载 PromptSkill；
- 每次调用进入 trace/黑板；
- QA 失败必须回到具体动作，不静默通过；
- 规则从 core prompt 移到 PromptSkill，降低总提示词长度；
- 保持 Engine / Plugin 边界。

## 3. 非目标

- 不替换 HyperFrames；
- 不把 MGGenerator 删除（作为 Tool 实现）；
- 不引入任意 JS 前端插件；
- 不做 DAG 调度器；
- 不让 Agent 直接写文件系统/密钥；
- 不让布局归一化代替 LLM 设计。

## 4. 角色

- **AnimationAgent**：导演/编排者，决定下一步；
- **Tool**：原子能力，确定性输入输出；
- **Skill**：方法论/规则包，可被 Agent 加载；
- **Engine**：注册表、上下文、黑板、QA 阈值、渲染契约；
- **Plugin**：Tool/Skill/Style/Provider 的实现。

## 5. Agent 循环

```text
1. 读取 task/brief/shot context
2. 判断：需要哪些 Skill/Tool
3. load_skill(...) / call_tool(...)
4. 读取结果摘要 + artifact
5. 判断：继续、修复、降级、询问
6. generate_mg 或 repair_mg
7. qa_mg + validate_layout
8. 通过 → 交付；失败 → 回到具体 Tool/Skill
9. 最多 3 轮，超出留 unfinished
```

不强制固定阶段；Tool/Skill 是能力面。

## 6. Tool 契约

| Tool | 作用 | 输入 | 输出 |
|---|---|---|---|
| `build_animation_brief` | 生成 brief | description/category | brief artifact |
| `research_sources` | 查资料/来源 | query | sources[] |
| `build_shot_plan` | 镜头意图/承接 | scene/brief | ShotPlan |
| `build_draft_spec` | 结构草稿 | plan | DraftSpec |
| `generate_mg` | 生成 mg_def | draft/plan | mg_def + meta |
| `validate_layout` | 只校验，不改坐标 | mg_def | issues[] |
| `repair_mg` | 按 issues 修 | mg_def/issues | mg_def |
| `qa_mg` | text/phone/motion | mg_def | QAReport |
| `render_preview` | 单镜预览 | mg_def | preview artifact |
| `inspect_reference` | 参考片拆解 | video | cuts/sheet |
| `ask_user` | 需要确认 | question | answer |

规则：

- Tool 不读隐式全局；参数显式；
- Tool 返回 artifact_ref + summary；
- 失败返回结构化错误，不抛裸异常；
- 同一 idempotency_key 可复用。

## 7. Skill 契约

| Skill | 用途 |
|---|---|
| `motion-showreel` | 短片方法：brief/风格帧/审片 |
| `web-design-engineer` | 设计方向/反 AI 味/评审 |
| `text-animation` | 文字动画规则 |
| `data-story` | 数据动画规则 |
| `flow` | 节点/箭头/流程 |
| `comparison` | 卡片/对比 |
| `quote` | 多行/金句 |
| `image` | 图像资产/安全 |

规则：

- Skill 由 Engine `PromptSkillRegistry` 加载；
- 插件通过 `prompt` contribution 贡献；
- 内置不可覆盖；
- Skill 只提供方法与规则，不直接执行副作用。

## 8. Engine / Plugin 边界

Engine：

- Tool/Skill 注册表与选择；
- Agent 循环；
- context/blackboard/call record；
- QA 阈值、修复上限；
- 渲染/预览契约；
- SSE 事件。

Plugin：

- Tool 实现；
- Skill body；
- Provider（LLM/TTS/Renderer）；
- Style/QA check；
- UI slot。

## 9. 上下文与黑板

- 每个 task：`session_id + task_id`；
- 记录 brief/plan/draft/mg/qa/calls/artifacts；
- 大对象只存 artifact ref；
- 失败/修复写 `repairs`；
- Agent 每轮从黑板组装上下文，不重读全历史。

## 10. QA 门禁

- `qa_mg` 不通过：Agent 必须选择 `repair_mg` 或 `generate_mg`；
- 不允许把 fail 当 success；
- 最多 3 轮；
- 导出前 `export_gate`；
- 技术 QA 与审美审片分离。

## 11. 前端

- 活动流：tool_call/skill_call/artifact/qa/repair；
- 上下文检查器；
- 黑板面板；
- 调用详情抽屉；
- QA fail 禁止导出；
- 不执行 mg_html。

## 12. 验收指标

- 动画生成期间至少 1 次显式 Tool 调用；
- 复杂镜头至少 1 次 Skill 加载；
- 每次 Tool/Skill 有 call record；
- QA fail 不再 success=true；
- 03/04/05 三类问题在 QA 中被捕获；
- prompt 总长度 ≤6k；
- 同一输入可复现；
- 前端能展示完整调用链。

## 13. 风险

- Agent 循环失控 → 轮次/token 上限；
- 工具结果太大 → artifact ref；
- 插件同 id 冲突 → 内置保护；
- 模型不调用 Tool → prompt + 完成标准 + 测试；
- 前端过载 → 摘要/按需展开；
- 与现有 QualityAgent 重复 → 明确分工。
