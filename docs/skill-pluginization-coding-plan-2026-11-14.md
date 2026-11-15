# Skill 自主调用与插件化 Coding Plan

- 配套：[skill-pluginization-prd-2026-11-14.md](skill-pluginization-prd-2026-11-14.md)
- 分支：`feat/zam_skill_pluginization`（从 `fix/zam_debug_tools_plugins_skills` 切出）
- 基线：后端 `fix/zam_debug_tools_plugins_skills`、前端同分支

## 里程碑

### M1 事件契约（后端，0.5d）

| 文件 | 改动 |
|---|---|
| `clipwright/tool/surface.py` | 暴露 `is_skill(name)`（包装 `_is_skill`），供 loop 与 API 复用 |
| `clipwright/services/agent_loop.py` | `tool_call`/`tool_result` detail 增 `kind` + `skill`（干净名）+ `capability` |
| `clipwright/schema/run.py` | tool_calls 采样键补 `kind/skill/capability/seq_in_run` |
| `clipwright/services/session_projection.py` | `detail.kind=="skill"` → `type="skill"`；`_meta_for` 白名单补 `capability/args_brief/seq_in_run` |
| `clipwright/agents/base.py` | `subagent_tool` detail 增 `skill`（干净名） |
| `clipwright/api/task.py` | runs 列表增 `skill_calls` 计数（从 tool_calls 里 kind=skill 统计） |

测试：`tests/clipwright/test_skill_events.py`（新）：
- kernel loop 调技能 → 事件 `kind=skill`、`skill=<name>`；
- session 投影产出 `type=skill`；
- run 采样保留 kind。

### M2 BaseAgent 技能面（后端，1d）

| 文件 | 改动 |
|---|---|
| `clipwright/agents/base.py` | `available_skills()` / `skill_schemas()`；`tool_schemas` 可选合并技能；`run_tool_loop` 的 schema 合并与执行器按 `SkillRegistry` 分派；`run_skill_round()`（有界、可降级） |
| `clipwright/agents/structure_agent.py` | 删除手工拼装（`:606-638`）改用 `self.skill_schemas()`；保留 `_tool_executor_for_llm` 或改用基类执行器 |
| `clipwright/agents/edit_agent.py` | 无需改动（自动获得技能）；补一行断言测试 |
| `clipwright/services/agent_kernel.py` | 系统提示补「技能是可组合能力，优先复用」；capability 目录里标 `(技能)` |
| `clipwright/config.py` | 新增 `skill_assist_enabled: bool = True`、`skill_assist_max_calls: int = 2` |
| 各阶段 Agent | 在 `execute()` 开头调用 `await self.run_skill_round(...)`（quality/material/animation/audio/requirements）；结果写入上下文/黑板 |

测试：`tests/clipwright/test_agent_skills.py`（新）：
- `run_tool_loop` 暴露技能名并执行（stub LLM 返回 skill 调用）；
- `run_skill_round` 无技能/超时/异常都 skipped；
- structure 的 schema 列表含技能且不再重复。

### M3 业务技能迁出 engine（1d）

| 文件 | 改动 |
|---|---|
| `plugins/skill_pack/plugin.yaml` | 新插件：`kind: capability`，`contributions: [kind: skill ×3]`，无额外权限 |
| `plugins/skill_pack/main.py` | 3 个 Skill 类（从 `clipwright/skill/builtin.py` 平移，逻辑逐字不变） |
| `clipwright/skill/builtin.py` | 删除 3 个类与注册列表；`register_builtin_skills()` 保留空实现（兼容调用点）并日志说明 |
| `clipwright/tool/surface.py` | 不再自动 `register_builtin_skills()`（改为无技能时优雅空面） |
| `docs/plugin_authoring.md` | 补「skill 由插件提供」示例与迁移说明 |
| `AGENTS.md` / `CLAUDE.md` | 核心不变量补「engine 不内置业务 skill」 |

测试：`tests/clipwright/test_skill_pluginization.py`（新）：
- 加载 `skill_pack` → SkillRegistry 有 3 个技能且 owner=skill_pack；
- 禁用插件 → 技能消失，surface 无技能且不报错；
- `override_builtin` 语义仍覆盖（用测试插件）。

### M4 前端（1d）

| 文件 | 改动 |
|---|---|
| `src/features/copilot/CopilotTaskRunTab.tsx` | CallRow 增 `kind/capability/skill`；主 agent `d.kind==='skill'` → origin=skill；显示干净名（capability 或去 `skill:` 前缀）；技能图标（🧠/Sparkles）；筛选沿用 |
| `src/features/task/TaskRunDetail.tsx` | 渲染 `run.tool_calls`（kind 标签、status、duration、seq_in_run；技能行高亮） |
| `src/features/task/TaskToolTrace.tsx` | 读 `call.kind`，技能行标签与干净名 |
| `src/features/copilot/TraceInspector.tsx` | kind=skill → 「技能 <name>」 |
| `src/features/sessionTimeline/SessionTimeline.tsx` | 优先读 `meta.kind`，技能图标/标签；名字去前缀 |
| `src/features/copilot/CopilotSubagentTab.tsx` | foldRunDetail 保留 `tool_calls`/`tool_calls_path` |

测试：`CopilotToolTransparency.test.tsx` 增主 agent skill 用例；`EventFeed.semantics` 保持；新增 `TaskRunDetail` 渲染测试。

### M5 回归与交付（0.5d）

- 后端：`py -3.14 -m pytest tests -q -k "skill or plugin or tool or event"`、`py -3.14 -c "import clipwright.main"`
- 前端：`npx tsc --noEmit`、`npm run test`、`npm run build`
- 文档：PRD/Plan 落地 + `docs/api_reference.md` 事件字段变更
- 推送分支、创建 PR（base 视 fix 分支合并情况：默认 main / 父分支）

## 依赖与顺序

M1 → M2 →（M3 可并行）→ M4 → M5。M1 是 M4 的前置；M3 与 M2 无代码依赖。

## 验收映射

见 PRD §5；每个里程碑的测试文件必须覆盖对应验收项，PR 描述中逐条勾选。
