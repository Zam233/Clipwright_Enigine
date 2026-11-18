# Skill 自主调用与插件化 PRD

- 版本：v0.1（2026-11-14）
- 仓库：`J:\Clipwright`（引擎）、`J:\Clipweight-Client`（前端）
- 分支：`feat/zam_skill_pluginization`

## 0. 一页结论

| 需求 | 现状（有代码证据） | 本 PRD 的裁定 |
|---|---|---|
| a. 主 agent 自主调用 skill | 已具备：`tool/surface.py::_skill_surface_entries` 把 Skill 并入 Kernel 工具面，`surface.execute` 经 `_execute_skill` 分派 | 保留；补事件 `kind=skill` 让调用可观测 |
| a. 子 agent 自主调用 skill | 部分：`StructureAgent` 手工把 `SkillRegistry` 拼进 `with_tools`；`EditAgent` 走 `BaseAgent.run_tool_loop` **不含 Skill**；material/animation/audio/quality 没有 LLM 工具轮 | 统一到 `BaseAgent`：技能 schema 并入工具轮 + 执行器可派发 skill；无工具轮的阶段增加**有界技能轮** |
| b. Skill 插件化 | 已有 `kind: skill` 贡献契约（进程内 + 子进程 `skill_adapter.py`），`SkillRegistry` 支持 `override_builtin` 与卸载恢复 | 保留契约；补齐子进程技能的可观测性与文档 |
| c. engine 只提供接入能力 | 仍有 3 个业务 Skill 内置在 `clipwright/skill/builtin.py`（analyze_video_structure / generate_captions / analyze_audio_rhythm） | 迁到 `plugins/skill_pack`；engine 的 `skill/builtin.py` 删除业务实现，只留注册入口 |
| d. 前端副驾驶显示 skill | 部分：`subagent_tool` 的 `kind=skill` 有标签；主 agent 的 `tool_call` 无 `kind`；run 详情 `tool_calls` 未渲染；会话时间线/全量轨迹不区分 | 后端补事件字段，前端按 `kind` 渲染（技能图标、干净名字、筛选、run 详情列表） |

## 1. 现状证据

### 1.1 主 agent
- `clipwright/tool/surface.py:430 _skill_surface_entries()` 把 `SkillRegistry` 中可用技能以裸名加入工具面；`surface.py:941 _is_skill(name)` 决定走 `_execute_skill`。
- 但 `services/agent_loop.py:382` 的 `tool_call` detail 只有 `{tool,args,shadow}`，`:543` 的 `tool_result` 没有 `kind` → 前端无法区分技能与工具。

### 1.2 子 agent
- `StructureAgent`（`agents/structure_agent.py:606-638`）自己拼 `ToolRegistry.list_agent_callable()` + `SkillRegistry.list()` 的 schema，并走自己的 `_tool_executor_for_llm`（`structure_agent.py:1367`）。
- `EditAgent` 用 `BaseAgent.run_tool_loop`（`agents/edit_agent.py:995`），而 `BaseAgent.tool_schemas()`（`agents/base.py:337`）只解析 `ToolRegistry`，执行器（`base.py:413`）也只 `ToolRegistry.execute` → **技能不可见、不可调**。
- material / animation / audio / quality / requirements 没有 LLM 工具轮，只能程序化 `call_tool`；`call_skill` 存在但不会由模型自主选择。

### 1.3 插件技能
- `plugins/contribution_dispatch.py:200` 将 `kind=skill` 注册进 `SkillRegistry`（带 owner），`override_builtin` 可接管内置；
- 子进程插件经 `plugins/skill_adapter.py` 代理到 `skill.call` RPC（`plugins/runtime/worker.py:251/309`）；
- 但 engine 仍内置 3 个业务技能（`skill/builtin.py`），与「engine 只提供接入能力」冲突。

### 1.4 前端
- `src/features/copilot/CopilotTaskRunTab.tsx:321`：`origin = d.kind === 'skill' ? 'skill' : 'main'`，主 agent 事件永远没有 kind；
- `mergeCallRows` 显示 `skill:<name>` 原名（`CopilotTaskRunTab.tsx:428`），未用 `capability`；
- `src/features/task/TaskRunDetail.tsx:111-116` 不渲染 `run.tool_calls`；
- `services/session_projection.py:34` 把 `subagent_tool` 一律投影为 `tool`，`skill` 类型不可达；`_meta_for` 白名单缺 `capability`。

## 2. 目标与验收

### A. 自主调用（主 + 子）
1. `BaseAgent` 新增统一技能面：`skill_schemas()` + `run_tool_loop` 合并技能、执行器按 `SkillRegistry` 分派。
2. `StructureAgent` 改为复用基类实现（行为不变，删除重复拼装）。
3. 无工具轮的阶段 Agent：新增有界 **skill round**（`skill_assist`），模型可选择 0–2 个技能；失败/超时降级不影响主流程；由设置 `skill_assist_enabled` 控制（默认 on，`max_calls=2`）。
4. 技能调用必须产生带 `kind=skill`、`skill=<干净名>`、`capability` 的轨迹（工具轮与 skill round 一致）。

### B. 插件化
5. `plugins/skill_pack/` 提供 3 个技能（manifest `contributions: kind: skill`），engine 删除内置业务实现。
6. 无该插件时，技能面为空且不报错（能力缺失是一等状态）。
7. 子进程插件技能可注册、可调用、可卸载恢复（已有路径补测试）。

### C. 前端
8. 主/子 agent 的技能调用在副驾驶轨迹中显示为「技能」：图标 + 干净名（`capability` 或去前缀），可筛选。
9. 子 agent run 详情渲染 `tool_calls` 列表（kind/status/duration/seq_in_run）。
10. 会话时间线、全量轨迹读取 `kind`，技能有独立标签。

## 3. 设计

### 3.1 事件契约（向后兼容）
- `tool_call` / `tool_result` detail 增加：`kind: "tool"|"skill"`、`skill?: string`（干净技能名）、`capability?: string`。
- `subagent_tool` detail 增加 `skill?: string`（保留 `tool:"skill:<name>"` 兼容）。
- `schema/run.py` 的 tool_calls 采样增加 `kind/skill/capability/seq_in_run`。
- `session_projection`：`detail.kind == "skill"` → `type="skill"`；meta 白名单补 `capability/args_brief/seq_in_run`。
- 不新增顶层 `skill` 事件类型（改动面大、收益小）。

### 3.2 BaseAgent 技能面
```
def skill_schemas(self, names=None) -> list[dict]   # SkillRegistry.list() 可用项 → to_llm_tool("openai")
def available_skills(self) -> list[SkillInfo]
run_tool_loop: schemas = tool_schemas(...) + skill_schemas(...)
_executor: if SkillRegistry.get(name): SkillRegistry.execute(name, **input) else ToolRegistry.execute(...)
```
注意：技能名与工具名冲突时以工具为准（注册表语义），并在 schema 描述里标注「技能」。

### 3.3 无工具轮阶段的 skill round
- `BaseAgent.run_skill_round(brief, context, max_calls=2)`：仅暴露技能 schema；返回调用轨迹与被调技能的 `output` 摘要。
- 由 `settings.skill_assist_enabled`（默认 true）与阶段白名单控制；超时/无 LLM 时直接 skipped。
- 结果写入该阶段 blackboard 的 `skill_outputs` 与 `last_tool_calls`（kind=skill）。

### 3.4 业务技能迁移
- 新建 `plugins/skill_pack/`（runtime 进程内即可）：`main.py` 定义 3 个 Skill 类；`plugin.yaml` 声明 `kind: capability` + `contributions: [skill]`。
- engine `clipwright/skill/builtin.py` 只保留 `register_builtin_skills()` 的空实现/日志，不再注册业务技能；`SKILL_CAPABILITY_ALIASES` 保留映射（插件提供实现）。
- 文档：`docs/plugin_authoring.md` 补 skill 迁移示例。

## 4. 风险与回滚

- 技能并入工具轮会改变模型可见工具集：用「技能 schema 描述前缀 + 可用性过滤」控制；出现异常可 `skill_assist_enabled=false` / 关闭插件。
- 迁移内置技能后，未装 `skill_pack` 的部署失去 3 个技能：文档明确「技能由插件提供」；CI 保留插件加载测试。
- 事件字段为增量，旧前端忽略未知字段，无破坏。

## 5. 验收清单

- [ ] 主 agent 调技能产生 `tool_call.kind=skill` 且前端显示「技能」。
- [ ] EditAgent 在工具轮中可见并调用技能（测试桩 LLM 断言 schema 含技能）。
- [ ] 无工具轮阶段（如 quality）skill round 可调用并降级。
- [ ] 卸载 skill_pack 后技能面为空、调用返回 NOT_FOUND，不抛异常。
- [ ] 前端 run 详情展示 tool_calls；会话时间线 skill 类型可达。
- [ ] 后端 pytest（插件/技能/事件）与前端 vitest 全绿。
