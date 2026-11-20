# B21 删除项审计（2026-11-20）

目标终态要求「计划内删除项清空」。本文件记录已完成与**仍存在**的项，避免把
“还能跑”误当成“已收口”。

## 已删除（有回归锁，见 `tests/contracts/test_deletion_audit.py`）

| 项 | 状态 |
|---|---|
| `agent_kernel._emit_report`（旧阶段汇报卡死代码） | 已删除 |
| `stage_report._section_text`（固定四段解析） | 已删除 |
| 报告 `next_for` / `headline` / `preview` 字段 | 已删除（前端 F01 同批） |
| `read_report.section` 功能 | 已删除；参数仅保留为**显式拒绝**（传了报错，不静默忽略） |
| 阶段内 `_IDEM`（旧幂等字典） | 已删除，改用 `run_store`（task-scoped idem） |
| 重复的 `run_stage` 入口/空 `_execute_stage` | 已删除（B21b 第一步）；现在只剩 `stage_adapter.run_stage`（唯一入口）+ `stage_subagent.run_stage`（领域执行） |
| `StageResult` 模型 + `stage_subagent._RUNS` 镜像 + `import_runs/list_stages` | 已删除（B21b）：`run_stage` 直接返回 `AgentResult`（assembler 单一铁厂），阶段历史只读 `run_store`；adapter 只负责盖章身份 |
| 固定格式交接文档 | 已删除，报告正文自由 Markdown，机器事实只在 `AgentResult` |
| `FailureReport.needs` 镜像 | 已删除（B21c）；缺项只在 `StageResult.needs` / `AgentResult.needs`，`test_agent_result_v2` 断言该字段不存在 |
| `_quality_issues` 隐式注入（`quality_issues_hint`） | 已删除（B21d）；没有任何生产者写该键，helper 与两处调用一并移除 |

## 仍存在（记录在案，未清空）

| 项 | 为什么还在 | 收口路径 |
|---|---|---|
| `AgentKernel` 内部工具循环（`agent_loop.run_loop`） | **已有公共驱动路径**（B13b：`with_tools` 在 `agent_loop_shared_driver=true` 时由 `drive` 控制轮次），默认 false 保留旧路径作回退 | 灰度确认 provider 边界后翻转默认、再删旧循环 |

## 结论

- 计划内删除项（StageResult、`_RUNS`、`FailureReport.needs`、固定格式解析、`read_report.section`、`_quality_issues` 注入、`_emit_report`、重复入口）已清空并加测试锁；
- **唯一未清项**是 `AgentKernel` 内部工具循环与公共 `drive` 并存（B13b），属架构级批处理，需要单独 PR。
