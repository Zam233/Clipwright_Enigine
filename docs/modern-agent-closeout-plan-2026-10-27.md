# Clipwright × 现代 Agent 收口开发计划（2026-10-27）

## 0. 一页结论

- 先收口监督派工和 dry-run，尤其将旧 pipeline 派工明确退役，阻止看似成功但实际 410 与预览误扣费。
- 全计划 11 批，估算 12 人日；各批在独立提交中完成，后端每批末尾执行全量 pytest。
- 监督派工不迁入 agent_run：agent_run 是显式委派运行契约，旧端点绑定 pipeline 阶段/重试语义，硬迁会伪造不存在的阶段宿主。
- 最大风险是跨进程记忆持久化和历史 Task 观测 API 的兼容性；分批先有格式契约、再挂接读取与前端。
- 自动化基线以 Python 3.14 的 `python -m pytest tests -q` 与前端 `npx tsc --noEmit`、`npm run test -- --run` 验收。

## 1. 缺口清单与处置决定

| 编号 | 缺口 | 处置：修复/迁移/删除/加测试 | 一句话依据 |
|---|---|---|---|
| A1 | supervisor dispatch/accept-suggestion 依赖已删除的 pipeline retry 宿主 | 删除旧执行语义，保留只读/登记语义并明确返回 retired | agent_run 接受 RunSpec，不表达旧阶段前置结果、pipeline 重试及产物回写；假迁移会产生误导性成功 |
| A2 | proceed 的 dry_run 未传 Task | 修复为 Task 只预览执行模式 | 用户选择必须由 Task 执行路径持有且在运行前可验证 |
| A3 | 记忆跨重启 | 迁移至稳定持久化存储并补加载 | 现有 TTL/撤回只在进程状态有效，重启会丢失语义 |
| A4 | trace 因果信封半成品 | 修复事件产生与因果字段赋值；无消费者 RunEvent 删除 | 一个标准事件源即可覆盖消费者，避免保留无引用类型 |
| A5 | aborted 未计入 task 指标 | 修复所有终态统一记录 | 任务成功率分母必须包含取消、暂停和触闸 |
| A6 | pipeline_enabled / agent_mode 死开关仍展示 | 删除配置项及 UI 控件/迁移旧配置忽略告警 | 旧管线已退役，继续展示会暗示用户可切换不存在的通路 |
| B7 | M4 评估闭环缺失 | 新增 | 需要可重复候选评估与离线门禁，避免 prompt 改动无量化证据 |
| B8 | Task 运行观测 UI 缺事件卡片、SSE、成本/步数等 | 迁移 | Task 原生事件已是运行事实源，继续轮询 pipeline 是错误数据源 |
| B9 | stage/subagent capability 薄适配层 | 删除 | 调用方清零后能力名表重复语义，没有独立消费者价值 |
| C10 | Mongo persist 文档形状无测试 | 加测试 | 持久化形状是重启恢复与历史兼容的契约 |
| C11 | stage_inputs 音频/人格字段映射无对照 | 加测试 | 纯字段映射应有显式输入输出对照，防止迁移回归 |
| C12 | dry-run 语义无测试且实现错误 | 修复并加测试 | 先由 A2 修正传递，再以预览不执行/不扣费断言锁定行为 |
| C13 | 插件 Agent deps 无消费者（SA-3） | 删除 deps 字段及无效说明 | AgentDAG 已删除且无新执行图契约；保留字段会制造虚假依赖支持 |
| C14 | pipeline quality helper 无调用方、Task 路径无 quality summary | 删除旧 helper；在 Task 评估结果中实现 quality summary | quality 摘要属于 Task 运行结果，保留 pipeline 私有函数无消费价值 |
| C15 | PipelineAdminPage 查询旧 runs 表，Task 路径永远空 | 迁移到 Task 运行列表 | 管理页面向现行产品线，旧表不再产生日志 |

## 2. 批次总览

| 批次 | 目标 | 人日 | 依赖 | 可并行 | 验收类型 |
|---|---|---:|---|---|---|
| B1 | 退役旧 supervisor 派工执行语义 | 1.0 | 无 | B2、B3 | 自动化测试/命令输出 |
| B2 | dry-run 贯通 Task 并验证不执行 | 1.0 | 无 | B1、B3 | 自动化测试 |
| B3 | aborted 任务纳入评估分母 | 0.5 | 无 | B1、B2 | 自动化测试 |
| B4 | Task trace 因果信封闭环 | 1.0 | B3 | B5 | 自动化测试/grep |
| B5 | memory 跨重启读写与恢复 | 2.0 | 无 | B4 | 自动化测试/人工核对 |
| B6 | 配置死开关和插件 deps 清理 | 1.0 | B1 | B7 | 自动化测试/grep |
| B7 | persist 与 stage_inputs 覆盖回补 | 1.0 | B2 | B6 | 自动化测试 |
| B8 | Task quality summary 与管理列表迁移 | 1.5 | B4 | B9 | 自动化测试/人工核对 |
| B9 | 前端 Task SSE 运行观测面板 | 1.5 | B8 | B10 | 自动化测试/命令输出 |
| B10 | M4 replay CLI、candidate registry、长任务事实保持 | 2.0 | B4、B5 | B9 | 自动化测试/命令输出 |
| B11 | capability 适配层退役及全量收口 | 0.5 | B6、B8、B9、B10 | 无 | 自动化测试/grep/人工核对 |

## 3. 批次详情

### B1 退役旧 supervisor 派工执行语义（1.0 人日）

- 目标：旧 pipeline 工单端点不再声称可启动 retry，清晰保留可用的登记/接受降级行为。
- 改动面：`J:\Clipwright\clipwright\api\supervisor.py`；`J:\Clipwright\tests\` 中 supervisor 路由测试（按现有文件名定位，不新造旧语义测试）。前端调用点由 `grep` 定位后同步。
- 实施步骤：
  1. 查明前端对 `/dispatch`、`/accept-suggestion` 的调用及其响应字段依赖。
  2. 将 `dispatch` 改为只登记，不再调用 `_try_retry`；响应明确 `execution_status: "retired"` 与替代操作提示，保留已有 order/audit 字段。
  3. `retry_with_model`、`redo_stage` 返回稳定的退役响应，不再调用 `start_agent_retry`；`accept_degraded` 与 `manual` 保持原有记录语义。
  4. 删除 `_try_retry` 和无法到达的 retry 相关分支；不动 `agent_run` 契约。
- 可证伪验收：
  - `python -m pytest tests -q`，结果必须为全绿，基线 4388 passed / 4 skipped / 1 xfailed 不得降低。
  - 对应 supervisor 测试断言：dispatch 返回登记成功但 `execution_status == "retired"`；mock `start_agent_retry` 后断言未调用。
  - `rg "start_agent_retry|_try_retry" clipwright/api/supervisor.py` 无结果；对前端目录 `rg 'pipeline/.*/dispatch|accept-suggestion'` 结果逐项核对 UI 文案/行为。
- 回滚：单独 revert B1 提交；保留测试一起回滚，禁止只恢复旧 retry 调用。
- 风险：旧客户端可能依赖 `retry.retried`；兼容期保留该字段为 false 并增加 reason，避免字段缺失造成解析异常。

### B2 dry-run 贯通 Task（1.0 人日）

- 目标：用户选择只预览时，Task 以预览模式执行且不进入真实制作/计费工具路径。
- 改动面：`J:\Clipwright\clipwright\api\requirements.py`、`clipwright\schema\task.py`、`clipwright\services\agent_kernel.py` 及对应 requirements/task 测试。
- 实施步骤：
  1. 在 `_proceed_as_task` 构造 `TaskRequest` 前从 `pipeline_req.dry_run` 归一化 bool，并写入 `extra["dry_run"]`；不要仅保留在已失效的 PipelineRequest 字段。
  2. 在 Task 执行器入口定义 dry-run 的允许动作集合：产出预览快照，禁止真实执行/扣费工具；若现有 plan_only 可表达同一语义，复用并验证，不另造运行时。
  3. 通过任务事件/结果明确标注 `dry_run=true`，UI 可区分预览与正式执行。
- 可证伪验收：
  - 新增 `tests/test_requirements_dry_run.py`：分别提交 dry_run true/false，断言 Task extra 与执行策略对应。
  - 预览路径断言真实制作工具调用 mock 次数为 0、LLM 计费记录为 0；正式路径对照断言仍可调用。
  - `python -m pytest tests -q` 全绿；失败即不得进入下一批。
- 回滚：revert B2 单提交；新增测试随代码回滚。若 schema 字段已被前端使用，先回滚客户端同名展示再回滚后端。
- 风险：`plan_only` 与 dry-run 语义可能不等价；实施第 2 步必须先读执行分派逻辑并以工具调用断言证明边界。

### B3 aborted 指标闭环（0.5 人日）

- 目标：所有终态任务（含 aborted）恰好写入一次 task 评估记录。
- 改动面：`J:\Clipwright\clipwright\services\agent_kernel.py`；`tests/test_agent_kernel.py` 或现有 evaluation/kernel 终态测试文件。
- 实施步骤：
  1. 将 `_record_task_metrics` 的唯一调用移动/归并到统一终态收敛处，保留调用幂等保护。
  2. aborted 的暂停、取消、触闸路径均携带终止原因与 final status。
  3. 确保完成/失败分支仍只写一次，异常路径不重复记账。
- 可证伪验收：`python -m pytest tests -q`；新增参数化测试覆盖 completed/failed/aborted，每类 `evaluation.record_task` 恰好调用一次，并断言 summary 分母含 aborted。
- 回滚：单独 revert B3；测试一起回滚。
- 风险：并发取消与完成竞态可能双写；测试须对同一 Task 重复触发 finish 验证幂等。

### B4 Task trace 因果信封（1.0 人日）

- 目标：每个 TaskEvent 可追溯触发源，agent_run_update 事件真实产生并有单一事件契约。
- 改动面：`J:\Clipwright\clipwright\schema\task.py`、`clipwright/schema/run.py`、`clipwright/services/agent_run.py`、`clipwright/services/agent_kernel.py`、`clipwright/api/task.py`、相关 schema/event 测试。
- 实施步骤：
  1. 明确 TaskEvent 的 task_id/run_id/causation_id 来源及父子事件继承规则。
  2. agent run 状态变更处发出 `agent_run_update`，事件携带 run_id、状态、时间和 causation_id。
  3. 订阅 SSE 的 Task 接口输出该事件；序列化字段保持向后兼容。
  4. 若 RunEvent 仍无调用者，删除 schema 并更新导出；由 TaskEvent 作为唯一公开信封，不双轨维护。
- 可证伪验收：`python -m pytest tests/test_agent_run.py tests/test_task_events.py -q`（测试文件名先按仓库确认并调整）；断言父 Task → delegate → run update causation 链完整；`rg "RunEvent" clipwright tests` 无结果（删除后）。
- 回滚：单提交 revert，确保 SSE 订阅者可忽略新增事件类型。
- 风险：旧 SSE 客户端的事件过滤可能丢弃未知类型；不得改写既有事件名称或字段。

### B5 记忆持久化（2.0 人日）

- 目标：semantic 与 episodic 记忆在进程重启后按 TTL、撤回和来源约束恢复。
- 改动面：`J:\Clipwright\clipwright\services\memory.py`、Mongo/持久化接入模块（以 `context.mongo` 实际接口为准）、应用 lifespan 初始化文件、memory 测试。
- 实施步骤：
  1. 读取 `memory.py` 当前数据结构与 Mongo 初始化顺序，确认启动加载时机；定义带 `schema_version` 的文档形状。
  2. semantic 使用已实现 `load_semantic(root)` 接入启动加载；写入/撤回/压缩时原子持久化。
  3. 为 episodic 增加序列化、load/save，沿用现有 TTL、provenance、撤回字段，不引入新运行时或独立数据库。
  4. 存储不可用时采用显式降级日志；禁止将未持久化状态报告为跨重启成功。
- 可证伪验收：memory 测试使用临时 Mongo collection/fake store：保存→清空进程缓存→重新 load，断言条目、TTL、provenance、撤回状态一致；过期条目重启后仍过滤。`python -m pytest tests -q` 全绿。
- 回滚：独立 revert B5；新格式带版本号，旧版本忽略未知字段；先停写再回滚代码，不删除已落库文档。
- 风险：启动时 Mongo 尚未连接；将加载挂接到连接成功后的明确生命周期钩子，避免 import-time 读库。

### B6 配置死开关与 deps 清理（1.0 人日）

- 目标：删除用户可见的失效模式开关及插件 Agent 无消费者的 deps 字段。
- 改动面：后端 config/settings/schema 与 `clipwright/agents/registry.py`；前端设置页（由 `rg "pipeline_enabled|agent_mode"` 精确定位）；配置测试。
- 实施步骤：
  1. 全仓检索两个配置的声明、读取、写入、API 暴露和前端展示，记录唯一仍需兼容的持久化入口。
  2. 后端停止读取/展示死配置；旧配置加载时忽略并给出一次性兼容日志，不让配置文件启动失败。
  3. 移除注册表 `deps` 与相关验证/文档，只在确认无消费者后执行；如外部插件 manifest 仍发 deps，忽略未知字段并测试兼容。
  4. 同步设置 UI 与项目文档。
- 可证伪验收：`rg "pipeline_enabled|agent_mode" clipwright` 仅允许兼容迁移测试/日志命中；`rg "\.deps|deps:" clipwright/agents` 无执行消费者；全量 pytest 全绿，前端 tsc 与测试全绿。
- 回滚：同一提交同时回滚后端与前端；如配置被新版本重写，回滚前备份 settings 文档并确认旧字段可被忽略。
- 风险：历史配置持久化会重放；迁移须验证旧配置 fixture 的启动行为。

### B7 拆除遗留覆盖回补（1.0 人日）

- 目标：以新模块行为测试代替被删除老管线测试所覆盖的持久化和输入映射。
- 改动面：`J:\Clipwright\clipwright\services\stage_inputs.py`、Task/Mongo persist 写入实现文件（经 grep 确认）、`tests/` 中对应模块测试。
- 实施步骤：
  1. 从 `git log` 找回历史 `test_pipeline_v2_persist_shape`、`test_pipeline_v2_audio_config`、`test_pipeline_persona_flow` 断言意图，不恢复老测试文件或老管线引用。
  2. 新增 Task persist shape 对照：必填标识、状态、时间戳、结果快照字段类型与 Mongo `_id` 兼容。
  3. 对 `stage_inputs` 的音频字段、voice/persona、duration 和默认值建立表驱动正面对照测试。
  4. 执行全量 pytest；若旧测试需要删除，测试迁移表明确逐条等价断言位置。
- 可证伪验收：新测试对缺字段、错误类型和完整输入分别断言；`python -m pytest tests -q` 全绿；不出现 `pipeline_v2` import。
- 回滚：新增测试可独立 revert；不得通过删掉新断言回滚生产代码。
- 风险：旧字段默认值可能来自调用方而非 stage_inputs；先追踪真实调用链再确定期望值。

### B8 Task quality summary 与管理列表迁移（1.5 人日）

- 目标：Task 运行结果包含可查询质量摘要，管理页读取现行 Task 运行列表。
- 改动面：`J:\Clipwright\clipwright\api\pipeline.py`（旧 helper 删除）、Task schema/API 文件（现有 `/api/tasks` 路由）、`J:\Clipweight-Client\src\pages\admin\PipelineAdminPage.tsx` 及测试。
- 实施步骤：
  1. 核验 Task 结果中 quality 工具/评估输出形状，定义 `quality_summary` 字段与空值语义。
  2. Task 完成时从结果快照汇总质量项；记录摘要进入 Task 持久化文档，不从已退役 pipeline 表读取。
  3. 提供管理员 Task runs 列表查询（分页、状态、开始时间、摘要），遵守现有 owner/admin 鉴权模式。
  4. PipelineAdminPage 改接 Task 列表，空列表状态与加载失败分别展示；移除 `_extract_quality_summary`、`_aggregate_agent_notes`，确认无引用后删除。
- 可证伪验收：后端 API 测试断言分页和质量摘要；前端测试 mock 新端点并断言行数据显示 Task id/status/摘要；`rg "_extract_quality_summary|_aggregate_agent_notes" clipwright` 无结果。全量两端测试通过。
- 回滚：后端新端点和前端切换在一个可回滚发布单元；若需分提交，先发布兼容新端点，再切 UI，最后删旧 helper。
- 风险：历史 pipeline 表数据不能代表新 Task；UI 明确展示数据源，避免混合统计。

### B9 前端 Task SSE 运行观测（1.5 人日）

- 目标：Task 运行页通过 SSE 展示模型轮次、工具调用、审批、子代理、成本和步数等真实运行信息。
- 改动面：`J:\Clipweight-Client\src\features\copilot\CopilotTaskRunTab.tsx`、`RunningSubagents.tsx`、对应 vitest 测试；后端 Task SSE 在 B4 提供事件。
- 实施步骤：
  1. 将 RunningSubagents 的 pipeline 轮询替换为 Task SSE 订阅与断线恢复；取消/结束时关闭订阅。
  2. 新增 model-turn、tool-call、approval 卡片，未知事件安全忽略，事件按 event id 去重。
  3. 增加成本、步数、重规划次数、上下文来源面板；数据缺失显示“尚无数据”，不推测。
  4. 保留现有任务状态和取消交互，补断线/重复事件测试。
- 可证伪验收：`npx tsc --noEmit`；`npm run test -- --run`；测试断言 SSE EventSource 建立、收到三类卡片、断线重连不重复、组件卸载关闭连接；`rg "api/pipeline/.*/subagents" src/features/copilot/RunningSubagents.tsx` 无结果。
- 回滚：独立 revert 前端提交；若 B4 事件尚未发布，先用 feature flag 隐藏新卡片，不恢复 pipeline 轮询。
- 风险：SSE 重连可能重放事件；以 event id/时间游标做幂等，限制历史缓冲大小。

### B10 M4 评估闭环（2.0 人日）

- 目标：候选 prompt/few-shot 可离线比较、门禁并重放，且长任务关键事实保持有回归集。
- 改动面：`J:\Clipwright\clipwright\services\evaluation.py`、新增/扩展 `clipwright/services/replay.py`、评估数据目录（以 PRD §5.5 指定目录为准）、`tests/`、`docs/modern-agent-prd-2026-10-27.md` 对应条款。
- 实施步骤：
  1. 新增 golden 选题集版本与三次评估报告格式，报告记录候选、基线、运行参数、指标及方差；不把一次随机运行当门禁。
  2. 建立 prompt/few-shot candidate registry（版本、变更说明、适用任务类型、回滚指针）；离线门禁比较成功率、事实保持和成本阈值。
  3. 实现 `python -m clipwright.services.replay --bundle PATH`：输入 bundle 校验、逐步重放、非零退出码表示格式/断言失败。
  4. 加入 ≥30 步任务测试，逐步更新关键事实并在末步验证；固定随机种子/模拟模型输出，避免外网依赖。
- 可证伪验收：`python -m clipwright.services.replay --bundle tests/fixtures/replay/sample_bundle.json` 返回 0；损坏 bundle 返回非零；三次报告含三次独立 run id；候选低于门槛时门禁非零；长任务测试覆盖第 1、15、30+ 步事实。
- 回滚：registry 和 CLI 单独可回滚；保留旧 bundle 只读解析，不覆写 golden 基线文件。
- 风险：指标噪声导致误门禁；固定数据集、重复三次并报告离散度，阈值配置需代码审查。

### B11 capability 薄适配层退役与收口（0.5 人日）

- 目标：调用方不再依赖两份能力名表/条件分支，删除已无独立价值的薄适配模块。
- 改动面：`J:\Clipwright\clipwright\services\stage_capability.py`、`subagent_capability.py` 与所有调用方（以 `rg` 结果列出）；文档和对应测试。
- 实施步骤：
  1. 搜索两个模块全部 import/符号调用，逐处改用现存统一 capability resolver 或显式类型契约。
  2. 删除无调用的模块、注册表与重复 `if`，更新测试和文档。
  3. 最终全量后端与前端验证，记录 grep 清零结果。
- 可证伪验收：`rg "stage_capability|subagent_capability" clipwright tests` 无结果；`python -m pytest tests -q` 全绿；前端 `npx tsc --noEmit`、`npm run test -- --run` 均通过。
- 回滚：单提交 revert；不得回滚已验证的 B1/B2 关键修复。
- 风险：resolver 与旧表的名称别名可能有差异；删除前做枚举对照测试，覆盖每个历史能力名。

## 4. 测试迁移与新增清单

| 文件 | 动作：新增/改/删 | 理由 | 替代覆盖 |
|---|---|---|---|
| `tests/test_requirements_dry_run.py` | 新增 | 锁定 Task dry-run 传递和零真实执行 | dry_run true/false 对照、工具调用/费用断言 |
| 现有 supervisor API 测试文件（以 `rg "post_pipeline_dispatch" tests` 定位） | 改 | 删除旧 retry 成功预期 | 登记成功 + retired 响应 + retry mock 未调用 |
| 现有 agent kernel 终态测试文件（以 `rg "record_task|aborted" tests` 定位） | 改 | 覆盖 aborted 漏记 | completed/failed/aborted exactly-once 参数化断言 |
| `tests/test_task_events.py`（如不存在则新增） | 新增 | trace 因果链和 agent_run_update | 父子事件 causation_id + SSE 事件断言 |
| 现有 memory 测试文件（以 `rg "load_semantic|semantic" tests` 定位） | 改/新增 | 跨进程持久化、TTL、撤回 | 保存→清缓存→加载、过期过滤、provenance 比对 |
| `tests/test_task_persist_shape.py` | 新增 | 替代被删除的 persist shape 测试 | Task Mongo 文档字段、类型与序列化断言 |
| `tests/test_stage_inputs.py` | 新增 | 替代 audio/persona flow 旧管线测试 | 输入字段→XInput 正面对照表 |
| Task runs API 测试（跟随实际 `/api/tasks` 测试布局） | 新增 | 新管理数据源分页与质量摘要 | API 鉴权、分页边界、摘要缺省 |
| `src/features/copilot/CopilotTaskRunTab.test.tsx` | 改 | 增补事件卡片及观测指标 | SSE 消费、去重、断线和卸载清理 |
| `src/features/copilot/RunningSubagents.test.tsx`（如当前测试名不同，扩展实际对应文件） | 改/新增 | 移除 pipeline 轮询 | SSE 订阅、状态渲染、取消清理 |
| `src/pages/admin/PipelineAdminPage.test.tsx` | 改 | Task runs 列表接入 | 新端点数据、空状态、错误状态 |
| `tests/test_replay_cli.py` | 新增 | M4 replay CLI 与离线门禁 | 有效/损坏 bundle、门禁退出码、30+ 步事实保持 |
| 原 `test_pipeline_v2_persist_shape` | 不恢复/不新增旧名 | 老管线已删除，重建会重新绑定错误实现 | `test_task_persist_shape.py` 等价覆盖 |
| 原 `test_pipeline_v2_audio_config` | 不恢复/不新增旧名 | 映射责任现属 `stage_inputs` | `test_stage_inputs.py` 音频字段对照 |
| 原 `test_pipeline_persona_flow` | 不恢复/不新增旧名 | 验证新 Task 输入映射，不复活旧宿主 | `test_stage_inputs.py` persona 对照 + Task 集成测试 |
| 原 `test_dry_run` | 不恢复旧断言 | 旧实现验证不了 Task 行为 | `test_requirements_dry_run.py` 验证真正执行路径 |

任何删测试的提交必须在同一提交新增等价断言；不允许以测试名更换掩盖覆盖缺口。

## 5. 完成定义（DoD）

- [ ] 后端全量 `python -m pytest tests -q` 通过；基线不低于 4388 passed / 4 skipped / 1 xfailed，任何计数变化附测试增删解释。
- [ ] `python -c "import clipwright.main"` 成功。
- [ ] 前端 `npx tsc --noEmit` 无错误，`npm run test -- --run` 全绿（原基线 983 passed）。
- [ ] `pipeline_v2.py` 不存在且全仓无恢复引用；`rg "pipeline_v2|AgentDAG" clipwright tests` 仅允许历史迁移说明。
- [ ] `rg "start_agent_retry|_try_retry" clipwright/api/supervisor.py` 无结果，supervisor 端点测试明确退役结果。
- [ ] Task dry-run 有端到端测试证明预览不执行制作工具、不计真实费用。
- [ ] aborted task_success_rate 分母有自动化断言，所有终态仅记一次。
- [ ] TaskEvent causation 链完整，SSE 实际发送 agent_run_update；`RunEvent` 已删除且无引用。
- [ ] memory 重启恢复测试覆盖 semantic/episodic、TTL、provenance 和撤回。
- [ ] PipelineAdminPage 展示 Task 运行列表，不再请求旧 `/api/pipeline/runs`。
- [ ] 两个 capability 适配模块、旧 quality helper、死配置 UI 清零并附 grep 输出。
- [ ] replay CLI 有效 bundle 退出 0、无效 bundle 非零，candidate 门禁可重复执行。

## 6. 明确不做

- 不恢复老管线、pipeline_v2、AgentDAG 或 `start_agent_retry`：它们与 Task 原生执行宿主并存会制造第二套事实源。
- 不把 supervisor 派工强行映射为 agent_run：旧工单阶段语义无法由 RunSpec 无损表达。
- 不引入新数据库、队列、调度运行时：Mongo 与现有 Task/SSE 基础设施足以承载本计划。
- 不建设通用 prompt 平台：M4 只做本仓库可追溯的 candidate registry 和离线门禁。
- 不混合历史 pipeline 与新 Task 的管理统计：字段语义不同，合并会造成误读。
- 不在同一批同时改记忆格式、SSE 协议和前端页面：逐层交付便于隔离回滚。

## 7. 时间安排建议

| 自然日 | 执行安排 | 串并行要求 |
|---|---|---|
| 第 1 日 | B1、B2、B3 | 三项互不依赖，可三人并行；各自跑后端全量回归后合并 |
| 第 2 日 | B4、B5 | 可并行；B4 完成后 B8/B9 可开始，B5 独立开发与验证 |
| 第 3 日 | B6、B7 | 可并行；B6 等 B1 接口处置定稿，B7 等 B2 Task dry-run 字段稳定 |
| 第 4 日 | B8 后端端点/摘要、B10 replay 基础 | 可并行；B8 接 B4 事件与 Task 存储，B10 接 B4/B5 契约 |
| 第 5 日 | B8 前端列表、B9 SSE 面板 | B9 必须依赖 B4；B8 前后端可按兼容端点并行 |
| 第 6 日 | B10 长任务 golden 与门禁 | 串行完成重复运行及报告核对，避免未稳定指标并入门禁 |
| 第 7 日 | B11、全量回归与验收留证 | B11 串行；最后冻结测试数据，保存前后端命令输出与 grep 结果 |

总估算 12 人日；若团队仅 1 人，按 7 个自然工作日安排并在每批提交后执行全量后端回归，不把失败跨批遗留。

---

## 8. 审校注记（由实施方核对后追加，不改动上文原文）

收到计划后逐条核对仓库，结论如下。

**核对通过（计划的事实前提成立）**
- `api/supervisor.py` 确实仍引用 `_try_retry` / `start_agent_retry` / `retry_with_model` / `redo_stage` / `accept_degraded`，B1 的"退役执行语义、保留登记语义"方向成立。
- `record_task` 只有一处调用点（`agent_kernel._record_task_metrics`），指向的两条收尾路径成立；`aborted` 分支确实不记录，B3 成立。
- `agent_run_update` 只在 `schema/task.py` 的 `EVENT_TYPES` 里出现一次（从未发出）；`schema/run.py::RunEvent` 零引用；`TaskEvent.causation_id` 无赋值点，B4 成立。
- `memory.load_semantic` 零调用点，B5 成立。
- `stage_capability` / `subagent_capability` 仍在，B11 成立。

**需要修正的两处（实施时按这里执行，不要按原文）**
1. **B5 的存储介质**：现有记忆持久化是**文件**（`memory.dump_semantic()` → `<root>/memory/semantic.json`、
   `dump_episodic()` → `episodic.jsonl` 追加），不是 Mongo。所以 B5 的最小实现是
   "lifespan 里调 `load_semantic(root)` + 给 episodic 补 load 与游标恢复"，
   **不需要**引入 Mongo collection 或 `schema_version` 文档形状；只有将来真迁 Mongo 才需要版本字段。
2. **C13 / B6 的 `deps` 删除**：`clipwright/agents/registry.py` 的 `deps` 是**插件 manifest 的公共契约字段**，
   删除属破坏性变更。B6 应先降级为"标记废弃 + 不再校验 + 文档标注"，确认无第三方插件依赖后再删；
   计划原文"直接移除字段"风险偏高。

**两处补强建议**
- B2（dry-run）建议顺带把 `pipeline_req.dry_run` 这个已失效字段一并处理，避免留下"看起来能设、实际没用"的第二处死开关。
- B10 的 replay CLI 建议复用 `evaluation.replay_bundle()` 已有产物形状，别另起一套 bundle 格式（当前 bundle 含 events / runs / memory / metrics 四段）。

---

## 9. 执行记录（2026-10-27，实施方）

> 纪律：每批只写"做了什么 + 怎么验收"，未做的项在 §9.2 明确列出，不用"剩余与本批无关"结案。

### 9.1 已完成批次

| 批次 | 状态 | 关键改动 | 新增/改写的验收 |
|---|---|---|---|
| B1 supervisor 派工退役 | ✅ | `_try_retry` 改为"只登记不执行"；`dispatch` 返回 `execution_status="retired"` + `alternative`；`retry_with_model`/`redo_stage` 不再执行 | `tests/test_supervisor_retry_wiring.py` 重写为退役契约（6 条） |
| B2 dry-run 贯通 Task | ✅ | `_proceed_as_task` 把 dry-run 写进 `Task.extra`；`agent_loop.run_loop(dry_run=)` 让原子工具走 `surface.execute(dry_run=True)` 预检、派发类工具直接拒绝；kernel 收尾记 `dry_run_preview` | `tests/test_dry_run_task.py`（4 条，含"预览不计入成功率"） |
| B3 aborted 指标闭环 | ✅ | abort 拆三类：pause 保持 paused（不记）/ cancel→cancelled（记）/ 触闸→failed+`gate:*`（记）；`record_run` 默认 kind 由 `task` 改 `run` | `tests/test_task_metrics.py`（6 条） |
| B4 trace 因果信封 | ✅ | `tool_result.causation_id` 指向对应 `tool_call.seq`；幂等复用与等审核发 `agent_run_update`；删除死代码 `RunEvent` | `tests/test_trace_envelope.py`（5 条） |
| B5 记忆跨重启 | ✅（按审校注记用文件介质） | 新增 `memory.load_episodic` / `memory.load`；kernel 首次进 loop 恢复、所有终态收尾写任务情景记忆并落盘 | `tests/test_memory_persistence.py`（5 条） |
| B6 死开关与 deps | ✅（deps 只降级） | `agent_mode` / `pipeline_enabled` 标注失效并改准说明；`agents.registry.deps` 标废弃、不删 | 设置/注册表回归 357 passed |
| B7 覆盖回补 | ✅ | `stage_inputs` 的音频/人格/质量/override 优先级断言；`PipelineModel` 落库形状 + `get_run_records` 映射 | `tests/test_legacy_coverage_backfill.py`（8 条） |
| B8 Task 质检摘要 | ✅（后端部分） | 新增 `task_results.quality_summary` / `remember_from_board`，kernel 收尾写含质检的结果快照；删除老管线两个死 helper | `tests/test_task_quality_summary.py`（5 条） |
| B11 适配层退役 | ✅ | 只读工具策略迁入 `agent_run`（`enforce_tool_policy`）、阶段名校验迁入 `agent_run._run_stage`、`list_runs` 按 `task_id` 过滤、`surface` 保留 `refused` 语义并透出 `failure`；删除 `stage_capability.py` / `subagent_capability.py` | `tests/test_m3_removals.py` +2 条删除证明；相关用例改走 `surface`/`agent_run` |

### 9.2 本批**未完成**（如实列出）

| 批次 | 状态 | 原因与建议 |
|---|---|---|
| B9 前端 Task SSE 运行观测 | ❌ 未开始 | 属前端仓库（`J:\Clipweight-Client`）一批：`RunningSubagents` 改 SSE、`CopilotTaskRunTab` 补 model-turn/tool-call/approval 卡片、`PipelineAdminPage` 数据源换 Task。改动面在 UI，需单独一轮并跑 `tsc` + vitest |
| B10 M4 评估闭环 | ❌ 未开始 | replay CLI、golden 三次对比、prompt candidate registry、≥30 步事实保持测试是独立一批（约 2 人日），依赖 B9 的可视化面板才有观察面 |
| B2 的 `pipeline_req.dry_run` 字段清理 | ⚠️ 部分 | dry-run 已贯通 Task（行为已正确）；`PipelineRequest.dry_run` 字段本身仍留着，删它会动 `schema/pipeline.py` 与若干构造点，建议与 B9 一起做（前端也在传这个字段） |
| B8 的管理列表迁移 | ⚠️ 后端就绪 | 结果快照已带质检；"管理页列表"是前端的事，归 B9 |

### 9.3 本批后的基线

- 后端：**4420 passed / 4 skipped / 1 xfailed**（本批起点 4407，新增 13 条 + 改写若干）
- 前端：未改动，仍 983 passed
- 机械检查：`stage_capability|subagent_capability` → 仅 2 处历史注释；`pipeline_v2` 仍 0 import。

---

## 10. 执行记录（第二批：B9 / B10，2026-10-27）

### 10.1 本轮完成

| 批次 | 状态 | 关键改动 | 验收 |
|---|---|---|---|
| B9 前端 Task SSE 运行观测 | ✅ | `RunningSubagents` **改用任务事件**（`agent_run_start/update/end` 折叠成筹码，不再轮询 `/api/pipeline/{id}/subagents`）；`CopilotTaskRunTab` 新增三张卡片——模型轮次（intent/narration/thinking）、工具调用（call↔result 配对：状态/耗时/错误）、授权（`approval_request` 单列，等授权≠失败）；管理页空态改成"老管线已下线、新创作在上方任务列表" | 前端 **987 passed / 115 files**，`tsc --noEmit` 干净；`CopilotTaskRunTab.test.tsx` 新增 4 条（模型轮次 / 工具配对 / 授权卡 / 筹码来自事件） |
| B10 M4 评估闭环 | ✅ | 新增 `services/replay.py`（`python -m clipwright.services.replay --bundle …`：校验事件投影自洽——序号单调、工具 call↔result 配对与因果、子 run 有始有终、必须有 task_end；退出码 0/1）与 `services/prompt_registry.py`（候选注册表 + 离线门禁 + 三次对比报告） | 后端 **4431 passed / 4 skipped / 1 xfailed**（本批 +11 条）；`tests/test_eval_loop.py`（11 条：CLI 退出码 / 缺证据必报 / 候选元数据强校验 / **成功率下降永不放过** / 成本超限 / 三次运行波动告警 / ≥30 步事实保持） |

### 10.2 修正一处此前的判断（不是"补做"，是纠错）

`PipelineRequest.dry_run` **不是死字段**：前端 `AgentPanel` / `ReviewPanel` / `HomePage` 都在请求体里
传 `dry_run`，B2 之后 `_proceed_as_task` 正是读它写进 `Task.extra`。所以本轮**保留**该字段，
只把它确认为"唯一传输口径"。上一轮把它列入"待清理"是错的，这里更正。

### 10.3 仍未完成

| 项 | 状态 | 说明 |
|---|---|---|
| B10 的 golden 真实选题三次跑 | ⚠️ 机制就绪、数据未跑 | 报告/门禁/方差告警都已实现并有单测；真正"同一选题跑三次"需要模型凭据与耗时，属运营动作，不是代码缺口 |
| B9 的 `RunningSubagents` 旧轮询接口下线 | ⚠️ 前端已不调用 | 后端 `GET /api/pipeline/{id}/subagents` 仍保留（supervisor 抽屉在用），要不要删取决于 drawer 是否迁移 |

---

## 11. 三项遗留收口（2026-10-27 第三批）

### 11.1 golden 真跑：真的跑了，而且抓到一个真 bug

`golden_eval`（`python -m clipwright.services.golden_eval --topic … --runs 3`）用**真 Kernel +
真 provider（deepseek-v4-pro）+ 真工具**跑同一个选题三次，取指标出报告。
连跑三轮的结果（`_cache/eval/`）：

| 轮次 | max-turns | 结果 | 回合 | 工具调用 | 诊断 |
|---|---|---|---|---|---|
| 第 1 批 | 4 | 0/3 成功 | 8 ± 1 | 7 ± 1 | 全部卡在收尾守门：没有 `final_timeline` |
| 第 2 批 | 12 | 0/3 成功 | 6.3 ± 1 | 5.3 ± 1 | 同上 |
| 第 3 批（加提示词） | 12 | 0/3 成功 | 8.7 ± 4 | 7.7 ± 4 | 回合变多但仍在读工具里打转 |

**根因（不是提示词问题）**：任务没有工作区 → `list_dir` 报"路径不在白名单"、
`list_artifacts`/`list_reports`/`project_memory` 报"未找到工作区"，
模型把 10 次调用全花在这些 error 上，最后交不出时间线。证据：`task_95659a6aef`
的 `context_digest` 只有两条，`state.json` 的 `stages` 为 `[]`——**一步都没派出去**。

**第三个、也是真正卡住"出片"的那个 bug**（诊断脚本逐条打工具结果时发现）：
工具面是唯一入口，但 Task 的工作区信息只存在 `request.extra` 里，**模型不知道
`workspace_root` 该填什么** → `list_artifacts` / `list_reports` / `write_file` 一律报
"未找到工作区"，`list_dir` 报"路径不在允许目录"。逐条对比：

| 调用 | 注入前 | 注入后 |
|---|---|---|
| `list_artifacts` | error 未找到工作区 | **ok**（返回 workspace_root + 0 条） |
| `list_dir`（模型自报路径） | error 路径安全校验失败 | **ok**（9 项） |
| `list_reports` | error 未找到工作区 | **ok** |
| `read_file` | error 路径安全校验失败 | **ok**（读到 `.workspace.json`） |

修法：在 `tool/surface.execute` 的**唯一入口**补齐工作区上下文（只填 schema 里确实有、
且调用方没给的键），并把"环境类报错不要反复重试"写进提示词。
回归用例：`test_tool_surface_injects_workspace_context_from_task` /
`test_explicit_arg_beats_injection`。

其余两处修复：
1. **评估环境要像生产**：`golden_eval` 真跑前先 `ensure_workspace()` 并注入
   `workspace_root`/`workspace_project_id`（与 `proceed` 一致）；
2. **防抖**：`agent_loop` 对"同一工具 + 同一参数"连续失败 3 次直接中止，理由写进
   `stopped_reason`，让 Kernel 如实收尾——把回合烧在同一个 error 上是典型的静默失败。
   同时提示词加一条：环境类报错不要反复重试。
   回归用例：`test_repeated_identical_failure_aborts_loop` / `test_success_resets_failure_streak`。

### 11.2 `GET /api/pipeline/{id}/subagents`：不是老管线表，已改名归位

核实结论：它返回的是 **supervisor worker 树**（`spawn_subagent` 注册的 tracker 节点），
对研究型子 agent 是唯一视图——**不能删**；问题在路径里的 `pipeline` 是历史命名，
容易让人以为它是老管线残留。

处置：
- 新增规范路径 `GET /api/supervisor/{task_id}/subagents` 与
  `POST /api/supervisor/{task_id}/subagents/{sid}/cancel`（`admin_router`）；
- 前端三处消费方（`supervisorApi.getSubagents` / `TaskSubagents` / `CopilotSubagentTab`）
  全部迁到规范路径，并统一读 `tree`（此前有人读 `subagents`、有人读 `nodes`，是真 bug）；
- 旧 `/api/pipeline/{id}/subagents*` **路由已下线**，处理函数作为文档化别名保留（无路由）。

### 11.3 B10 可视化：管理页「评估」Tab

- 后端：`GET /api/eval/reports`（列表摘要）/ `GET /api/eval/reports/{name}`（全文）/
  `GET /api/eval/latest`；只允许读 `eval_report_dir`（默认 `_cache/eval`）内的 JSON，
  名字含 `/`、`..`、绝对路径一律 400；
- 前端：`src/features/pipeline/EvalPanel.tsx` + 管理页「运行记录 / 评估」切换；
  面板只展示均值、离散度、每次运行的状态/原因/回合/工具/成本/耗时，口径不在前端二次解释；
- 验收：`EvalPanel.test.tsx` 3 条（列表+详情、波动告警、空态给生成命令）。

---

## 12. 前端缺口调研（GPT-5.6 SOL，2026-10-27）

> 调研对象：modern agent 化（tool-use loop + 统一 AgentRun）之后的前端。
> 方法：读源码 + 逐调用链核对，不跑真实浏览器。实施方对每条独立复核，结论一致处不重复论证。

### 12.1 三个已报缺口的根部结论

| # | 现象 | 断在哪一层（复核结论） |
|---|---|---|
| 1 | 「正在思考」点开没有流式内容 | **后端根本没发**：`agent_loop.run_loop` 只发 `narration/tool_call/tool_result/report`，没有 `thinking`；`agent_kernel._emit_thinking`（`agent_kernel.py:1053`）是死代码；`llm.py` 的 `on_reasoning` 只在**需求对话**的 gathering 流里用（`requirements_service.py:1427-1465`）。前端 `ThinkingDisclosure` 的拼接逻辑是对的，只是永远没数据。 |
| 2 | 文案无法完整传入 agent | **两层截断**：① `context_loader.LIMITS["brief"]=400`、`plan=1400`（`context_loader.py:22-25,49-55`），主 agent 每次只看到 brief 前 400 字；② `context_loader` **不读 `request.extra["script_text"]`**，脚本虽进了 `TaskRequest.extra`（`requirements.py:536-555`）却不在主 agent 上下文里。阶段侧 `stage_inputs` 能从 `extra_params` 拿到 `creative_brief`/`script_text`，但主 agent 派活时手里没有全文，`delegate_agent` 也不会自动补（`stage_subagent.py:277-303` 显式 inputs 时 `board={}`）。 |
| 3 | 上传配音提示 `deduplicated` | **不是幂等命中，是素材 SHA-256 去重被当成错误**：`asset_manager.import_file` 命中同内容时返回原资产并设 `error="deduplicated"`（`asset_manager.py:135-156`），`api/asset.py:119-124` 只要 `info.error` 就抛 **HTTP 400**，前端 `HomePage.pickAudio` 把 `detail` 原样显示（`HomePage.tsx:418-423`）。文件其实已经入库、`used_count` 也加了。幂等服务 `services/idempotency.py` 与配音上传无关。 |

### 12.2 新发现（实施方已复核 N1/N3，其余待排期）

| 编号 | 缺口 | 证据 | 严重度 |
|---|---|---|---|
| N1 | `task_end.reason` 映射仍按旧口径：非 `plan_completed` 一律 failed → **成功任务显示失败** | `taskStore.ts:139-146`；后端已改用 `tool_loop_completed`/`dry_run_preview`/`gate:*`（`agent_kernel.py:720-749`） | **P0（已修）** |
| N2 | `PlanPanel` 仍把 Plan 当执行进度展示，而它已是「初始假设」（`hypothesis_only=True`） | `PlanPanel.tsx:63-107`、`taskStore.ts:49-97` vs `schema/task.py:81-95` | P1 |
| N3 | 子 agent 详情仍查 tracker 快照，不消费 `agent_run_*` 结果 | `CopilotSubagentTab.tsx:20-37` → `api/supervisor.py:387-398` | P1 |
| N4 | 审批只有日志，没有提交 `approval_id` 的入口 | `CopilotTaskRunTab.tsx:133-159` vs `agent_kernel.py:633-642`（paused + approval_id） | P1 |
| N5 | `context_loaded.trimmed` 后端传成 bool，loader 实际返回 list → 用户看不到"裁掉了哪段" | `agent_kernel.py:600-607` vs `context_loader.py:136-155` | P1 |
| N6 | 工具 call↔result 用**工具名**作 key 配对，同名并发会串 | `CopilotTaskRunTab.tsx:78-103` vs 后端每次调用都有 seq/causation | P1 |
| N7 | 事件在 store（400 条）与 EventFeed（120 条）二次截断，thinking 无独立缓冲 | `taskStore.ts:17`、`EventFeed.tsx:20-32` | P2 |
| N8 | `doc_write`/`report`/`context_loaded` 只有一句话，没有结构化详情与跳转 | `EventFeed.tsx:20-57` | P2 |
| N9 | 子 agent 筹码把 `replayed` 当状态，未消费 `AgentResult.failure/needs` | `RunningSubagents.tsx:27-33,49-55` | P2 |

### 12.3 本批已修（N1）

`src/stores/taskStore.ts` 新增 `terminalStatusOf(reason)` 并替换旧三元判断：

- `tool_loop_completed` / `plan_completed` / `task_completed` / `dry_run_preview` → `done`
- `cancelled` / `cancel` → `cancelled`；`paused` → `paused`
- 其余（`gate:*` / `tool_loop_error:*` / `tool_loop_unavailable:*` / 守门未过 / 空原因）→ `failed`

验收：`src/stores/taskStore.test.ts` 追加 5 条（原 15 条保留，共 20 条）；前端全量 995 passed / 116 files。

### 12.4 建议的修复顺序（未做）

| 批次 | 修什么 | 验收 |
|---|---|---|
| F1 | thinking 真流式：`with_tools` 暴露 reasoning 回调 → `agent_loop` 逐块发 `thinking`（按 task/run 缓冲，不共用全局单槽） | 模拟 reasoning 分片，SSE 收到多条 delta，前端连续拼接 |
| F2 | 文案 artifact 化：`docs/script.md` + `delegate_agent` 强制 `input_refs` + `context_loaded` 返回 artifact 长度/hash | 10k 字脚本 proceed，阶段拿到的 hash/长度与原文一致 |
| F3 | 素材去重改 200 复用：`deduplicated: true` + `status: "reused"`，前端显示“已存在，已复用” | 同一音频二次上传 200，asset_id/path 相同，used_count+1 |
| F4 | 子 agent 详情与审批：消费 `agent_run_*` 结果 + 真实 approval action | 回放 run 事件能看 result/failure/cost；审批能提交并恢复任务 |
| F5 | 事件详情/并发配对/长任务回看（N5–N9） | 同名工具并发不串；doc/report 可展开；>400 条仍可查摘要 |

