"""轮63 收尾：看板段落 + fix-plan 记录。执行后删除。"""
from pathlib import Path

# ── 看板：追加轮63段落 ──
p = Path("docs/remediation-master-plan.md")
src = p.read_text(encoding="utf-8")
anchor = "## 批次 8（后续独立任务，不在本轮）"
addition = """## 轮 63：前端审计修复 + 轮 62 缺口补齐（2026-09-08）

| # | 修复项 | 状态 |
|---|--------|------|
| A-后端 | R1 audio_agent 补 import json（上传配音实测曾静默失效）；R2 规划失败分支误粘贴成功消息→500；R3 失败/取消/超时会话回 plan_ready（单一 finally + 状态守卫）；R4 空洞填充同步 segments（转场不再错位）；R5 dry-run 检查全部 step；R6 移除废弃端点 /run、/run-v2（无 owner/预算治理）及 V1 编排器死导入；R7 空镜头探测受 quality_depth 门控；R9 有 error 无 redo_agent 如实 FAILED；R13 glow/stroke 并存 bord 取较大值；R10 下游闭包纳入插件 Agent（get_full_deps）；R11 持久化单线程串行化（修并发竞态）；R14 无声音告警块裸 except 改日志 | ✅ |
| B-前端流程 | 静默演示降级移除（AgentPanel startSession/sendChat/sendEdit/confirmBrief + autostart 两处 + HomePage，HTTP 错误展示后端 detail）；SSE 终态修正（error 非终态→建议列表、done 按 result.status 判定、补 timeout 分支、cancelled 中性化）；proceed 传 project_id；忽略时间线二次确认；ReviewPanel 超时公式对齐 + sessionStorage 持久化；confirmPlan 防重复启动；取消确认框；startSession 键名统一+补字段；streamChat 失败不二跑；EditorPage 降级换新 id；Markdown 表格渲染；进度条公式修正 | ✅ |
| C-前端导出/编辑器 | ExportPage 提交 P0 崩溃修复（presets[presetId]?.name）；移除前端幽灵预设 bilibili_4k，ProRes/H.265/720p/480p 对齐后端；weibo 磁贴对齐竖屏；输出名扩展名随预设+时间戳；渲染队列取消按钮 + SSE cancelled/error 分支 + 类型补 cancelled；插件 toggle/loadAll 反映真实结果（502 回滚可见）；完成事件展示 result.warnings 与 cover_paths；嵌套序列加渲染端未实现警告 | ✅ |
| D-次级 | 见批A（R10/R11/R14 合并实施）；semantic QA 读 skeleton.brief 延后（proceed 流 creative_brief 直达） | ✅（D3 延后） |

回归：后端 1462 passed / 0 失败（移除 1 个废弃端点用例）；前端 tsc 0 错误 + vitest 379/379。

""" + anchor
assert src.count(anchor) == 1
src = src.replace(anchor, addition)
p.write_text(src, encoding="utf-8")
print("board round63 added")

# ── fix-plan §10 轮 63 ──
p = Path("docs/fix-and-feature-plan.md")
src = p.read_text(encoding="utf-8")
anchor = "- ✅ 回归：后端 1463 passed / 1 skipped / 1 xfailed（含批1 新增 10 项）· `python -c \"import clipwright.main\"` 通过"
addition = anchor + """

### 执行轮次 63（前端全面审计修复 + 轮 62 缺口补齐）
- ✅ 后端缺口：audio_agent 补 import json（R1：上传配音 ffprobe 实测曾因 NameError 静默失效）；requirements_service 规划失败分支删除误粘贴的成功消息（R2：plan_result=None 时 AttributeError→chat 500）；失败/取消/超时会话回写 plan_ready（R3：单一 finally + 状态守卫，成功态不回滚）；空洞填充同步插入 segments 占位段（R4：转场不再错位）；dry-run 检查全部 step（R5）；移除废弃端点 /run、/run-v2 与 V1 编排器死导入（R6：无 owner/预算治理）；空镜头探测受 quality_depth 门控（R7：basic 跳过省最多 30×30s）；有 error 无 redo_agent 如实 FAILED（R9）；glow/stroke 并存 bord 取较大值（R13）；无声音告警块裸 except 改日志（R14）；下游闭包纳入插件 Agent（R10 get_full_deps）；持久化单线程串行化（R11 并发竞态）
- ✅ 前端流程（J:\\Clipweight-Client）：移除 5 处静默演示降级（AgentPanel startSession/sendChat/sendEdit/confirmBrief + useRequirementsAutoStart 两处 catch——旧实现把 4xx/5xx 伪装成假成功简报/规划）；SSE 终态修正（error 非终态→建议列表、done 按 /result status 判定成败、补 timeout 分支、cancelled 中性化）；proceed 传 project_id + 忽略时间线二次确认；ReviewPanel 超时公式对齐后端（×6/×360）+ sessionStorage 持久化 pipeline id；confirmPlan 防重复启动；取消确认框；startSession 键名统一 material_source_ids 并补 voice_id/auto_dub/dub_segments/video_mode；streamChat 失败不再自动整跑第二次；EditorPage 降级换新 id 防覆盖真实项目；HomePage 创建失败显式报错；Markdown 表格渲染（规划书场景表）+ 进度条宽度公式修正
- ✅ 前端导出/编辑器：ExportPage 提交 P0 崩溃修复（presets[presetId]?.name）；移除幽灵预设 bilibili_4k、ProRes/H.265/720p/480p/微博竖屏对齐后端；输出名扩展名随预设 + 时间戳防覆盖；渲染队列取消按钮 + SSE cancelled/error 分支 + 类型补 cancelled；插件 toggle/loadAll 反映真实结果（502 回滚可见）；完成事件展示 result.warnings 与 cover_paths；嵌套序列加渲染端未实现警告
- ✅ 回归：后端 1462 passed / 0 失败（移除 1 个废弃端点用例）· 前端 tsc 0 错误 + vitest 379/379"""
assert src.count(anchor) == 1
src = src.replace(anchor, addition)
p.write_text(src, encoding="utf-8")
print("fix-plan round63 appended")
