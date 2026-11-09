# ADR：动效视频插件的通用宿主契约（M0）

> 状态：Accepted（M0 首批裁决；后续里程碑可增补，但不推翻已冻结项）
> 关联：[PRD](agent-motion-video-plugin-prd.md) · [Coding Plan](agent-motion-video-plugin-coding-plan.md)
> 范围：只记录 engine 侧通用契约的裁决与理由；插件领域内容不在本 ADR。

## ADR-1 插件工程文件放在项目工作区的插件命名空间

**决定**：插件工程文件位于 `<project-root>/plugins/<plugin_id>/<project_id>/`，由插件自行组织子目录（例如 `motion/`）。engine 的项目根快照 `<project-root>/project.json`、`<project-root>/timeline.json` 保持原位，插件不得读写。

**理由**：
- 用户产物需要随项目备份/迁移，放 `PluginData` 会变成“看不见的孤岛”；
- engine 已有 `services/workspace.py` 的项目工作区模型（materials/renders/cache/logs），复用其路径解析比新建第二棵树成本低；
- 但现有 `permissions.py` 只允许写 `PluginData/plugins/<id>`，因此 M2 必须引入 scope 化的 workspace 代理，不能直接放开 `fs_write`。

**后果**：M2 需要同时支持“项目内插件命名空间”和“插件数据根”两类授权根，并保证 root 快照与插件文件物理隔离；测试必须覆盖覆盖写保护。

## ADR-2 runtime 由管理员登记，插件只引用 runtime_id

**决定**：engine 维护不透明 runtime registry：`runtime_id / version / digest / platform / executable / env_allowlist / io_policy / approval_state`。插件 manifest 只声明引用与所需能力，不携带可执行路径。JobSpec 只接受已批准 runtime。

**理由**：插件不能自行把任意 executable 注入宿主；engine 也不应认识 Node/Remotion/Python 等品牌。

**后果**：M3 需要 runtime 注册/审批接口与集成测试；未批准的 runtime 在提交 job 时返回 `UNSUPPORTED_RUNTIME`，不静默降级。

## ADR-3 artifact 由宿主计算元数据、插件显式登记

**决定**：插件通过 `artifact.register` 申请登记；宿主计算并写入 `mime/size/sha256`，校验 owner（plugin/task/project/job）与路径范围。artifact 记录 `role/status/retention`；MVP 状态为 `pending/ready/failed/cancelled/expired/orphaned`。

**理由**：插件自报 MIME/hash 无法防伪造；自动扫描输出目录会把临时文件误登记进产物列表。

**后果**：M2 需要 artifact registry 与 `/api/artifacts` 读取契约；`expired` 只由 retention 触发，不删除元数据。

## ADR-4 协议 v2：双向、分方向 ID、限流与深度上限

**决定**（M1 已实现）：
- 消息带 `direction`（`host`/`worker`），两个方向独立编号；v1 无 direction 按 host 处理。
- host 读循环把 worker 请求派发给独立 task，**不能**在同一 await 链里串行处理。
- 上限：单帧 8 MiB、单方向在途 32、嵌套深度 8；超限返回 `OVERLOADED`/`DEPTH_EXCEEDED`。
- 超时：worker 侧 `call_host(timeout=...)` 返回 `TIMEOUT`；宿主侧 handler 超时返回 `TIMEOUT`。
- 崩溃/卸载：两侧 pending 全部失败，worker death → `WORKER_DEAD`。

**理由**：单管道双向最易死锁；分方向 ID 让“响应冒充请求”不可能。

**后果**：M2/M3 的 workspace/job 方法都走这一通道；新方法只加 handler，不改协议形状。逐帧 `frame_provider.v1` 另立版本，不混入本契约。

## ADR-5 事件复用现有 progress/artifact，不新增 event 类型

**决定**：插件 job 的进度/产物通过既有 `progress`/`artifact` 语义对外表达；MVP 不新增 task event 类型，避免破坏 31 类事件契约与前端 `eventSchema.ts`。

**理由**：事件集合是前后端硬契约；新增类型需要同步改前端并走契约评审，成本高于收益。

**后果**：若现有载荷不足以表达 job 状态，先在 artifact/元数据内扩展，仍不改事件类型；确需新事件时另行评审。

## ADR-6 ClipKind：注册表 + 受限 schema + 卸载四态

**决定**：
- 内置 kind 保留枚举；插件 kind 以字符串注册，经 `ClipKindRegistry` 解析。
- `metadata_schema` 限定 JSON Schema 子集，带 `schema_version`；同 id 冲突拒绝。
- 消费者（quality/render/preroll/narration/trait 等）不得直接比较插件 kind 字符串，统一走 registry。
- 卸载后四态：读取保留并标 `kind_unavailable`；编辑只允许通用字段；保存原样保留；渲染失败或按 `partial_render` 生成可识别占位。

**理由**：只放宽 Pydantic 字段会让插件 kind 流入只处理内置类型的代码路径，产生静默错误。

**后果**：M4 必须先盘点消费者再改 schema；卸载四态各需测试。

## ADR-7 渲染时回调，engine 只合成标准媒体

**决定**：engine 在渲染到插件 clip 时通过 `clip.render` RPC 调用插件；插件返回 `media_overlay.v1`（MVP：单个带 alpha 的透明视频）。engine 校验尺寸/帧率/时长/alpha 后按现有 overlay 链合成，不执行插件代码、不解析插件工程文件。

**理由**：满足“插件执行、返回结果”，同时避免 engine 在渲染期执行任意脚本。

**后果**：M5 的容差表即验收；帧序列与逐帧回调推迟到 `frame_provider.v1`。

## 当前实施状态（M0/M1）

| 项 | 状态 | 证据 |
|---|---|---|
| 协议 v2 消息与版本兼容 | 已实现 | `clipwright/plugins/runtime/protocol.py` |
| worker→host 请求 + 重入 | 已实现 | `worker_rpc.py`、`host.py::_dispatch_worker_request` |
| 插件侧调用桥 | 已实现 | `clipwright/plugins/runtime/bridge.py` |
| v2 验收测试 | 已通过 | `tests/clipwright/test_plugin_rpc_v2.py`（6 例） |
| 既有 subprocess 回归 | 已通过 | `test_plugin_runtime_subprocess.py`（17 例） |
| scoped workspace（路径/revision/root 隔离） | 已实现 | `clipwright/services/plugin_workspace.py` |
| artifact registry（宿主算 hash/mime） | 已实现 | 同文件 `PluginArtifactRegistry` |
| workspace/artifact RPC handler + scope 下发 | 已实现 | `services/plugin_rpc.py`、`host.py`、`worker_rpc.py` |
| M2 测试 | 已通过 | `test_plugin_workspace.py`（9）、`test_plugin_workspace_rpc.py`（3） |
| runtime 登记 + Job Broker（argv/cwd/env/超时/取消/幂等/恢复） | 已实现 | `clipwright/services/plugin_jobs.py` |
| task 取消 → job 取消 | 已接线 | `agent_kernel.cancel()` → `cancel_for_task()` |
| M3 测试 | 已通过 | `test_plugin_jobs.py`（11）、`test_plugin_job_rpc.py`（1） |
| ClipKind 注册 / 渲染回调 / artifact UI | 未开始 | 按 M4–M6 推进 |
