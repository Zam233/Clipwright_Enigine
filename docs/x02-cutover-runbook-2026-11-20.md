# X02 维护窗口 Runbook（阶段 Agent agentic 化 v2）

> 适用：PRD v0.4 / Coding Plan v1.2 的 P0-4 双仓原子切换。
> 前置裁定（用户 2026-11-20）：**生产环境不维护旧数据**——不归档、不迁移、不设保留期；
> 窗口只覆盖「部署 + 探针 + 观察」，目标 **≤ 1 小时**。

## 0. 冻结与预检（T-30min）

| 检查 | 命令 / 位置 | 通过标准 |
|---|---|---|
| 双仓 fixture 同字节 | `python scripts/verify_contracts.py`（后端） | `[OK] 9 份 fixture 双仓同字节` |
| 后端契约 | `python -m pytest tests/contracts tests/test_event_contract.py tests/test_task_api.py -q` | 全绿（基线已知红除外） |
| 后端可导入 | `python -c "import clipwright.main"` | 无异常 |
| 前端类型/测试/构建 | `npx tsc --noEmit && npm run test && npm run build` | 全绿 |
| 版本指纹 | `GET /health` | `contract_version="2"`、`contract_schema="agent_result_v2"` |
| 探针清单（离线可跑） | `python scripts/x02_probe.py` | `5/5 probes passed`（health / 关键路由 / task.runs / reports / eval） |

- 停止接新 Task；等待活跃 Task 收敛或走既有取消路径；记录当前二进制版本与 `/health` 指纹。

## 1. 清理旧运行数据（不备份、不归档）

删除（仅运行产物）：

- `<workspace>/reports/`（含 `INDEX.md`、`tool_calls/`）
- `<workspace>/checkpoints/`、旧 state sidecar
- 旧 RunStore 目录（`<workspace>/runs/`）

**保留**：`materials/`、`docs/`、`renders/`（成片）、用户文档、项目元数据。

要求：脚本先 `dry-run` 打印命中清单，人工对照排除清单后才执行；只接受白名单子路径，禁止通配根目录。

## 2. 部署序列（串行，不可只发一边）

1. 部署后端 v2（暂不接单）。
2. 探针：`GET /health` → `contract_version=2`、`status` 非 degraded（已知 `no_key/checking` 豁免）。
3. 跑 §0 的后端契约与冒烟子集。
4. 同批部署前端 v2。
5. 清客户端 `localStorage` 的 task/run/event 缓存；刷新后 REST 水合（`since_seq` 接流）。
6. 开放接单，观察 30 分钟。

## 3. 冒烟清单（逐项勾选）

| # | 路径 | 预期 | 观测 |
|---|---|---|---|
| 1 | 新建制作 → 规划 → 结构 | 任务进入 `plan_ready/pipeline_done` | `agent_run_end.terminal_reason` 非空 |
| 2 | 结构 / 素材 / 剪辑 / 动画 / 音频 / 质检 | 每阶段有 `AgentResult` 与自由正文报告 | `step_end.report_state=written` |
| 3 | 报告 Tab | 无标题正文可读；`unavailable` 显式；无 `next_for` 字段 | `GET /reports` 返回 v2 DTO |
| 4 | 运行详情 | 终态原因 / 交接摘要 / 轨迹状态可见 | `trace_state` 与 `tool_calls_total` |
| 5 | 轨迹 | 按 `run_id` 读取并分页 | `GET /tool-calls?run_id=…` |
| 6 | 评估面板 | capped 单列、0 成本显示 0 | `eval-capped-banner` |
| 7 | SSE | `subagent_round`/`step_end`/`doc_write` 新字段可展开 | 事件详情含 v2 键 |

## 4. 回滚

- 触发：任一冒烟项失败、`protocol_error` 非零、或前端出现“协议错误”大面积。
- 动作：冻结写入 → 保全 v2 收据与费用 → 双仓同时回退到切换前二进制（旧运行数据已按裁定丢弃，**不提供回读**）→ 已产生的付费写操作人工对账。
- 策略级回退（不回退 wire）：`settings.stage_loop_<stage>_enabled=false`、`agent_loop_shared_driver=false`。

## 5. 窗口后

- 记录 `/health` 指纹、耗时、冒烟结果到发布记录。
- 按 PRD §10 的放量阶梯观察 `protocol_error / 双计费 / 跨任务轨迹`，任一非零自动关 `shared_driver`。
