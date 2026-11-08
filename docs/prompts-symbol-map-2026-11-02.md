# `clipwright/prompts.py` 符号映射与搬运验证（PRD W4.5）

> 生成方式：手写映射 + 脚本核对（`python -m py_compile` 与 `import clipwright.main` 通过；
> 每条都做过逐字比对，见每行最后一列的说明）。**不含** Persona / Skill / Category 文案
> —— 它们经 `compose_type_prompt(s)` 注入，属覆盖层，按 PRD 裁定留在原处。

| 符号 | 原位置 | 用途 | 类型 |
|---|---|---|---|
| `CREATIVE_BRIEF_SYSTEM` | `agents/requirements_agent.py` | 需求简报对话与 JSON 约束 | 常量 |
| `PLAN_TRANSLATE_SYSTEM` | `agents/requirements_agent.py` | 规划书翻译 | 常量 |
| `STRUCTURE_SYSTEM_PROMPT_TPL` | `agents/structure_agent.py:SYSTEM_PROMPT_TPL` | 分镜/脚本结构生成 | 常量（.format 模板） |
| `STRUCTURE_TOOL_PROMPT` | `agents/structure_agent.py:TOOL_PROMPT` | 结构阶段工具约束 | 常量 |
| `STRUCTURE_MG_DYNAMIC_GUIDE` | `agents/structure_agent.py:MG_DYNAMIC_GUIDE` | MG 动态标记引导 | 常量 |
| `LOOP_PLAN_ONLY_SYSTEM` | `services/agent_loop.py:_PLAN_ONLY_SYSTEM` | 规划书任务系统提示 | 常量 |
| `LOOP_CLOSEOUT_BLOCK` | `services/agent_loop.py:_CLOSEOUT_BLOCK` | 收口模式规则 | 常量 |
| `LOOP_NORMAL_SYSTEM` | `services/agent_loop.py:_NORMAL_SYSTEM` | 主 Agent 创作系统提示 | 常量 |
| `MG_STRICT_JSON_OUTPUT` | `animation/mg/generator.py:_STRICT_JSON_OUTPUT` | MG 只输出裸 JSON | 常量 |
| `MG_NO_BACKGROUND_CONSTRAINT` | `animation/mg/generator.py:_NO_BACKGROUND_CONSTRAINT` | MG 背景遮挡约束 | 常量 |
| `MG_ORCHESTRATION_CONSTRAINTS` | `animation/mg/generator.py:_MG_ORCHESTRATION_CONSTRAINTS` | MG 节奏/构图约束 | 常量 |
| `EDIT_PROFILE_SYSTEM` | `agents/edit_agent.py:_llm_decide_edit_profile` | 剪辑节奏/转场权重 | 常量（函数内） |
| `QUALITY_SEMANTIC_QA_SYSTEM` | `agents/quality_agent.py:_check_semantic_qa` | 质检语义评估 | 常量（函数内） |
| `build_budget_note` | `services/agent_loop.py:_budget_note` | 回合预算提示 | builder |
| `build_plan_translate_system` | `agents/requirements_agent.py` | 规划书翻译（含参考方案 JSON） | builder |
| `build_bgm_match_system` | `agents/audio_agent.py:_llm_match_bgm` | BGM 情绪匹配 | builder |
| `build_anim_marker_system` | `agents/structure_agent.py:_enrich_scene_animations` | 补动画标记 system | builder |
| `build_anim_marker_user` | `agents/structure_agent.py:_enrich_scene_animations` | 补动画标记 user | builder |
| `build_material_queries_batch_prompt` | `agents/material_agent.py:_llm_search_queries_batch` | 批量搜索关键词 | builder |
| `build_material_queries_prompt` | `agents/material_agent.py:_llm_search_queries` | 单场景关键词 | builder |
| `build_material_english_prompt` | `agents/material_agent.py:_to_english_queries` | 关键词英译 | builder |

## 搬运与验证记录

| 批次 | 内容 | 验证方式 | 结论 |
|---|---|---|---|
| 首批（11 条） | requirements / structure / loop / MG 的模块级常量 | 搬运前后值快照 JSON 对比 | 8949 字符差异 0 |
| 第二批（2 条） | `EDIT_PROFILE_SYSTEM`、`QUALITY_SEMANTIC_QA_SYSTEM` | 同上（含函数内文本） | 差异 0 |
| 参数化第一批 | `build_budget_note`、`build_plan_translate_system` | 6 + 3 个用例与旧 f-string 对比 | 差异 0 |
| 参数化第二批 | `build_bgm_match_system`、`build_anim_marker_system/user` | 9 个用例对比 | 差异 0 |
| 参数化第三批 | material 三处 | **运行时捕获**：改动前后各跑一次真实代码路径，比对实际发给 LLM 的 prompt | 372 / 244 / 86 字符逐字一致 |

## 已知边界

- `compose_type_prompt` / `compose_many` 注入的 Category / Persona / Skill / 插件文案**不在本文件**，
  覆盖顺序为 `prompts.py` < Category < Persona < Skill < 插件。
- `STRUCTURE_SYSTEM_PROMPT_TPL` 是 `.format` 模板（`structure_agent._build_system_prompt` 负责填参），
  模板文案在本文件，填参逻辑属业务，留在 agent。
`

## 事故记录（两次，均已修复）

1. 用“括号配平”删字符串字面量 → 含 `{{BRIEF_TEMPLATE}}` 的字符串被截断（两个 agent `SyntaxError`）。
   改用 **AST `lineno/end_lineno`** 精确定位替换。
2. 替换函数内赋值丢掉行首缩进 → `IndentationError`。改为**沿用原语句缩进**再替换。
