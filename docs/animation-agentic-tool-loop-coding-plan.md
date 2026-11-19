# 动画 Agentic Tool/Skill 循环 Coding Plan

- 关联 PRD：`docs/animation-agentic-tool-loop-prd.md`
- 状态：待实施
- 分支：`feat_zam_animation_improvements`

## M0 契约与注册表

### M0-1 Tool 契约

新增：

```text
clipwright/animation/agent_tools.py
```

- `AnimationToolSpec`：name/input_schema/output_schema/permission；
- `register_animation_tool()`；
- Engine 负责注册/查找，插件只能通过 Contribution 添加。

验收：注册 5 个工具后 `list_tools` 可见；同 id 冲突拒绝。

### M0-2 Skill 注册

现有 `prompt_skills.py`：

- 增加 `SkillDescriptor`（id/description/when/load）；
- `load_skill(id)` 返回 PromptSkill body；
- `compose_skills()` 保持动态。

### M0-3 PromptSkill 插件

- `load_plugin_prompt_skills` 已接 `PluginPromptRegistry`；
- 增加 `when` 元数据解析（category/shot_type）；
- 插件不可覆盖内置。

## M1 Tool 包装

新增 tools：

```text
build_animation_brief
research_sources
build_shot_plan
build_draft_spec
generate_mg
validate_layout
repair_mg
qa_mg
render_preview
inspect_reference
```

实现：

- 每个 tool 接受显式参数；
- 大对象写 artifact，返回 `artifact_ref`；
- `generate_mg` 复用 MGGenerator；
- `validate_layout` 只读，不归一化；
- `repair_mg` 走 LLM 修复；
- `qa_mg` 返回 QAReport。

验收：单测每个 tool 的 schema/失败/幂等。

## M2 Agent 循环

新增：

```text
clipwright/animation/agentic_loop.py
```

核心：

```python
while rounds < max_rounds:
    action = await agent.decide(context)
    if action.kind == "skill": load_skill(...)
    elif action.kind == "tool": call_tool(...)
    elif action.kind == "final": break
    context.record(...)
```

约束：

- round ≤3；
- tool call ≤ N；
- token 预算；
- 失败回退；
- 不强制阶段顺序。

接入 AnimationAgent：

- `animation_agent.execute` 在 `_llm_mg` 分支走 loop；
- 非 MG 文字动画保持旧路径；
- 无 tool 可用时降级旧 MGGenerator。

## M3 QA / 修复闭环

- `qa_mg` fail → action `repair_mg`；
- 修复最多 3 轮；
- 不通过写 blackboard `status=qa_failed`；
- `success=false`；
- 导出 gate 拒绝。

## M4 上下文 / 黑板

- Agent 每轮写 `record_call`；
- 写 `shot.last_qa` / `repairs`；
- 组装上下文只取相关 artifact；
- 支持 resume 从黑板恢复。

## M5 前端

- `AnimationActivityFeed` 展示 tool/skill；
- `ContextInspector` / `BlackboardPanel` 已建；
- 接 SSE `animation_activity`；
- QA fail 时导出禁用；
- 调用详情抽屉。

## M6 测试与验收

### 单测

- tool registry/冲突；
- skill loader；
- loop 决策（tool/skill/final）；
- QA fail → repair；
- max rounds；
- blackboard resume；
- prompt 动态 skill。

### 集成

- 5 个样本：至少 1 次 tool 调用 + 1 次 skill；
- 04 flow 必须调用 `validate_layout`；
- 05 quote 必须调用 `qa_mg`；
- 03 comparison 必须调用 `validate_layout`；
- QA fail 时 `success=false`；
- 前端活动流可见。

### 回归

```text
tests/clipwright -k 'animation or mg_'
```

## 文件清单

```text
clipwright/animation/agent_tools.py
clipwright/animation/agentic_loop.py
clipwright/animation/prompt_skills.py
clipwright/animation/context_store.py
clipwright/agents/animation_agent.py
clipwright/animation/mg/generator.py
clipwright/api/animation_context.py
src/features/animation/AnimationActivityFeed.tsx
src/features/animation/ContextPanels.tsx
```

## 风险与回滚

- loop 不调用工具 → 保留旧 `MGGenerator` 分支；
- 循环成本高 → max_rounds=3/max_calls；
- 前端事件过多 → 摘要 + 按需展开；
- 插件不兼容 → tool/skill 缺失降级；
- 回滚：关闭 `animation.agentic_loop.enabled`。
