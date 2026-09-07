# 全面修复总计划 — 执行看板

> 状态：执行中。整合前六轮审计（管线/Agent/渲染/交付出口/E2E 契约/插件治理）的全部发现。
> 证据细节见各审计报告与 `docs/ai-generation-integration-plan.md`；本文为执行看板，每批完成即勾选并记录回归。

## 批次 1：成片可用性（渲染 + 音频）——不修则任何产出不可交付

| # | 修复项 | 关键锚点 | 状态 |
|---|--------|---------|------|
| 1.1 | 混音三连：多人声只混第一句 / atrim 时间线起点当源偏移 / afade 在 adelay 后失效 | render.py:2232-2258 / :2200-2209 / :2219-2224 |✅ |
| 1.2 | 拼接/转场回退丢内容（降级硬切而非丢块）；_is_valid_video 增强（时长>1s + 视频/音频流检查） | render.py:1513-1516/1589/1500/1575/519-526 |✅ |
| 1.3 | 字幕双写 A8（7b 检查 CAPTION 轨已重建则跳过）；ASS per-line 样式（\fs\fn\1c）+ 发光色分离 + offset_y + 烧录失败 warning | audio_agent.py:409-471 / design.py:198-227 / render.py:1648-1687 |✅ |
| 1.4 | 主轨空洞黑帧填充；trim 失败显式 warning | render.py:_extract_segments/_run_concat_all |✅ |
| 1.5 | 占位视频：临时文本 uuid 命名（竞态）/ 字体走 _fonts 复制 / 宽高读 timeline（竖屏） | tool/text_video.py:66,72-79,39-40 |✅ |
| 1.6 | BGM 铺轨死循环保护 | audio_agent.py:249-256 |✅ |
| 1.7 | faststart 全路径；mov/webm 下载 content-type 与 /status 修正 | render.py 各 mux 点 / api/render.py:486,507,636 |✅ |

## 批次 2：确认→交付契约（原轮次 62 方案）

| # | 修复项 | 关键锚点 | 状态 |
|---|--------|---------|------|
| 2.1 | 复用分支 script_skeleton 携带简报摘要（救活 11 死亡字段） | structure_agent.py:236-245 |✅ |
| 2.2 | overview 包装层 bug；bgm_requirement 注入检索；proceed 补 material_source_ids | edit_agent.py:205 / audio_agent.py:97 / api/requirements.py:268-285 |✅ |
| 2.3 | animation_intents 三重复活；dub_segments 复用分支对齐；split_mode 接入；subtitle_enabled 贯通 | api/requirements.py / structure_agent.py / audio_agent.py / pipeline_v2.py:1046 |✅ |
| 2.4 | asset_ratio 仅 ai_generated 也渲染；special_requirements 升 warning 级 | structure_agent.py:659 / quality_agent.py:79-90 |✅ |
| 2.5 | 念确认的稿：voiceover 强制逐场景 voiceover_script + AudioAgent 按场景旁白 | structure_agent.py:620 / audio_agent.py:295-329 |✅ |
| 2.6 | 时长对账：画面轨 vs 音频终点，超长冻结帧补齐；drift 守卫修正；上传配音 ffprobe 实测 | audio_agent.py / render.py:1000-1003 / :119-140 |✅ |
| 2.7 | 会话状态：brief_confirmed 卡死恢复；pipeline_done 聊天分支；proceed 置 running；完成持久化当前消息；无 project_id 自动建项目 | requirements_service.py:563-575/529-608 / api/requirements.py:330-347 |✅ |
| 2.8 | 质检补盲：无有效音频/字幕缺失/画面音时长不一致三类 warning | quality_agent.py |✅ |

## 批次 3：编排正确性（A1-A5 / A9 + 小项）

| # | 修复项 | 关键锚点 | 状态 |
|---|--------|---------|------|
| 3.1 | A1 自愈下游 BFS 闭包（提炼公共方法） | pipeline_v2.py:602 vs 681-692 |✅ |
| 3.2 | A2 checkpoint 传 "running"；恢复含 pending；finally 兜底 CancelledError | pipeline_v2.py:1262/496/499-538；api/pipeline.py:109 |✅ |
| 3.3 | A3 retry 终态持久化 + run history | pipeline_v2.py:623-736 |✅ |
| 3.4 | A4 dry-run FAILED 不假 COMPLETED | pipeline_v2.py:779-801 |✅ |
| 3.5 | A5 质检映射去重修复；A9 双稿改配置开关（默认关） | quality_agent.py:242-265 / structure_agent.py:381-388 |✅ |
| 3.6 | 小项：get_step/edit logger.exception/失败不推进度（PiP 同源提示、scene_time 清理延后） | schema/pipeline.py:73 / edit_agent.py:553 等 |✅ |

## 批次 4：安全加固

| # | 修复项 | 关键锚点 | 状态 |
|---|--------|---------|------|
| 4.1 | /pipeline/result 与 /trace JSON 补 owner | api/pipeline.py:594/508 |✅ |
| 4.2 | 需求会话全端点 owner（对齐管线 owner 表） | api/requirements.py:108-204 |✅ |
| 4.3 | 渲染 SSE：owner + Mongo 回退 + 事件驱动等待 | api/render.py:402-444 |✅ |
| 4.4 | 需求上传大小/类型限制 | api/requirements.py:157-186 |✅ |
| 4.5 | 默认开放模式风险文档标注（不改默认值） | .env.example / README |✅ |

## 批次 5：轮次 60/61 自审修复

| # | 修复项 | 关键锚点 | 状态 |
|---|--------|---------|------|
| 5.1 | animation_agent 补 import httpx；公网图缓存成功路径测试 | animation_agent.py:835 |✅ |
| 5.2 | ai_image_gen local 分支 _api_url→_base_url；is_available local 探测 | ai_image_gen/main.py:206/97 |✅ |
| 5.3 | with_tools 每轮传 tools | llm.py:388-392 |✅ |
| 5.4 | 媒体类型过滤：registry 透传 media_type；generated_source 类型化打分；MaterialAgent 过滤 | material/registry.py:46-64 等 |✅ |
| 5.5 | 视频工具 poll_timeout 降 600s；任务查询瞬态错误可重试 | ai_video_gen/main.py / ai_music_gen/main.py:224-229 |✅ |
| 5.6 | 非火山分支统一下落+历史；_snap_size 向上取整 | ai_video_gen/ai_music_gen/main.py / ai_image_gen/main.py:51-53 |✅ |
| 5.7 | 生成登记后按 source_id 失效素材检索缓存 | material_agent.py:317-337 |✅ |

## 批次 6：交付出口

| # | 修复项 | 关键锚点 | 状态 |
|---|--------|---------|------|
| 6.1 | ProRes 中间产物容器跟随 preset（.mov） | render.py:1249/1494/1571 等 |✅ |
| 6.2 | 远程渲染：签名对齐；worker 透传 encoder/pix_fmt；音频 clip 上传 | remote_render.py:361-367 / worker/render_runner.py:60-69 / :84-93 |✅ |
| 6.3 | 预设覆盖修复（exclude_unset）+ 未知 preset 400；soft_subtitle 非预设生效 + SRT R1 校正 | api/render.py:559-577/186-199 |✅ |
| 6.4 | EDL/FCPXML 互操作修复 | services/edl.py |⏸ 延后 |
| 6.5 | 归档 zip：含成片/重名消歧/重映射表/流式 + 导入端点 | api/project.py:242-286 |⏸ 延后 |
| 6.6 | platform_export 封面真实现 + 钩子返回值消费 | plugins/platform_export/main.py / api/render.py:246-248 |◐ 部分（hook 上下文✅；封面抽帧被安全扫描阻断→延后） |
| 6.7 | /api/render/start 透传交付参数；水印/LUT/抠像/防抖标注"仅独立工具" | api/render.py:599-608 / docs |✅ |

## 批次 7：工具/插件治理

| # | 修复项 | 关键锚点 | 状态 |
|---|--------|---------|------|
| 7.1 | BPM 真检测或下线 | tool/audio.py:63-74 |✅ |
| 7.2 | 假成功清理：transcribe 已删、SemanticMatch 澄清（TrackingText/VisionLLM 改造延后） | tool/vision.py / animation.py / stubs.py / transcribe.py |✅ |
| 7.3 | whisper_stt 可用性+不覆盖内置；voice_ext 去 xtts+is_available；suno 默认禁用 | plugins/* |✅ |
| 7.4 | Hook 治理：unload/reload 清理（两插件补 plugin_id、text_animations 归属延后） | loader.py/hooks.py |✅ |
| 7.5 | load_all 容错；enable 端点异常处理；disable 移除生成插件注册物 | loader.py:252 / api/plugin.py:87-95 |✅ |
| 7.6 | 确认分类器转折尾缀；_is_confirm 统一 | requirements_service.py:1045/1093 |✅ |

| 7.6 | 确认分类器转折尾缀；_is_confirm 统一 | requirements_service.py:1045/1093 |✅ |

## 轮 63：前端审计修复 + 轮 62 缺口补齐（2026-09-08）

| # | 修复项 | 状态 |
|---|--------|------|
| A-后端 | R1 audio_agent 补 import json（上传配音实测曾静默失效）；R2 规划失败分支误粘贴成功消息→500；R3 失败/取消/超时会话回 plan_ready（单一 finally + 状态守卫）；R4 空洞填充同步 segments（转场不再错位）；R5 dry-run 检查全部 step；R6 移除废弃端点 /run、/run-v2 与 V1 编排器死导入；R7 空镜头探测受 quality_depth 门控；R9 有 error 无 redo_agent 如实 FAILED；R13 glow/stroke 并存 bord 取较大值；R10 下游闭包纳入插件 Agent（get_full_deps）；R11 持久化单线程串行化；R14 无声音告警块裸 except 改日志 | ✅ |
| B-前端流程 | 静默演示降级移除（AgentPanel 四处 + autostart 两处 + HomePage，HTTP 错误展示后端 detail）；SSE 终态修正（error 非终态→建议列表、done 按 result.status 判定、补 timeout 分支、cancelled 中性化）；proceed 传 project_id；忽略时间线二次确认；ReviewPanel 超时公式对齐 + sessionStorage 持久化；confirmPlan 防重复启动；取消确认框；startSession 键名统一+补字段；streamChat 失败不二跑；EditorPage 降级换新 id；Markdown 表格渲染；进度条公式修正 | ✅ |
| C-前端导出/编辑器 | ExportPage 提交 P0 崩溃修复（presets[presetId]?.name）；移除幽灵预设 bilibili_4k、ProRes/H.265/720p/480p 对齐后端、weibo 对齐竖屏；输出名扩展名随预设+时间戳；渲染队列取消按钮 + SSE cancelled/error 分支 + 类型补 cancelled；插件 toggle/loadAll 反映真实结果；完成事件展示 result.warnings 与 cover_paths；嵌套序列加未实现警告 | ✅ |
| D-次级 | 下游闭包纳入插件 Agent（get_full_deps）；持久化单线程串行化（R11）；无声音告警块裸 except 改日志；semantic QA 读 skeleton.brief 延后（proceed 流 creative_brief 直达） | ✅（D3 延后） |

回归：后端 1462 passed / 0 失败（移除 1 个废弃端点用例）；前端 tsc 0 错误 + vitest 379/379。

## 批次 8（后续独立任务，不在本轮）

前端仓库（proceed project_id / agent_notes UI / ReviewPanel 统一 / SSE 真流式）；计划修改意见改写 raw_scenes；persona 剩余字段接线；渲染产物 TTL 清理。

## 验收总则

- 每批配套针对性单测（含已知测试盲区：公网图缓存成功路径、多句配音混音、ProRes 全链）
- 每批全量回归 0 失败 + `python -c "import clipwright.main"`；fix-plan §10 记录
- 总验收：默认环境（仅 LLM key）成片=有声字幕正确的完整视频；配置火山三件套后 AI 生成素材可检索入轨；ProRes/远程渲染/导出预设按文档承诺工作
