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
| 3.6 | 小项：get_step/edit logger.exception/失败不推进度 + scene_time 死变量清理（PiP 同源提示延后） | schema/pipeline.py:73 / edit_agent.py:553 等 |✅ |

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
| 6.4 | EDL/FCPXML 互操作修复（轮64完成：编号连续/reel清洗/转场码映射/XML转义/file URI/ntsc修正/in-out点/字幕轨导出/导入保留位置/DOCTYPE守卫收窄） | services/edl.py |✅ |
| 6.5 | 归档 zip：重名消歧/重映射表/成片打包 + POST /import-archive 导入还原（轮64完成；流式打包延后） | api/project.py:242-286 |✅ |
| 6.6 | platform_export 封面真实现 + 钩子返回值消费 | plugins/platform_export/main.py / api/render.py:246-248 |◐ 部分（hook 上下文✅；封面抽帧被安全扫描阻断→延后） |
| 6.7 | /api/render/start 透传交付参数；水印/LUT/抠像/防抖标注"仅独立工具" | api/render.py:599-608 / docs |✅ |

## 批次 7：工具/插件治理

| # | 修复项 | 关键锚点 | 状态 |
|---|--------|---------|------|
| 7.1 | BPM 真检测或下线 | tool/audio.py:63-74 |✅ |
| 7.2 | 假成功清理：transcribe 已删、SemanticMatch 澄清、TrackingText 改 DEPENDENCY_MISSING（VisionLLM 回退带 fallback 标记保留） | tool/vision.py / animation.py / stubs.py / transcribe.py |✅ |
| 7.3 | whisper_stt 可用性+不覆盖内置；voice_ext 去 xtts+is_available；suno 默认禁用 | plugins/* |✅ |
| 7.4 | Hook 治理：unload/reload 清理 + logic_animations/diagram_style 补 plugin_id（text_animations 注册归属延后） | loader.py/hooks.py |✅ |
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

## 轮 64：遗留待办收尾（2026-09-08）

| # | 修复项 | 状态 |
|---|--------|------|
| 6.4 | EDL/FCPXML 全量修复（事件编号连续/reel 清洗/FROM CLIP NAME 可读/转场码映射/XML 转义/file URI 规范/ntsc 仅 29.97/in-out 点/字幕轨导出/导入保留时间位置/DOCTYPE 守卫收窄为仅拒 ENTITY） | ✅ |
| 6.5 | 归档 zip 重构（媒体重名消歧/archive_media_map 重映射表/时间线 asset_id 归档化/成片打包）+ POST /api/project/import-archive 导入还原端点（zip slip 防护/500MB 上限/媒体解包重映射） | ✅ |
| 批8a | DELETE /api/render/artifacts 过期产物清理端点（jwt 管理员管控） | ✅ |
| 7.2 | TrackingText 假 SUCCESS → DEPENDENCY_MISSING | ✅ |
| 7.4b | logic_animations/diagram_style Hook 注册补 plugin_id | ✅ |
| 3.6 | scene_time 死变量清理 + semantic QA 简报回退链（D3） | ✅ |

回归：后端 1465 passed / 0 失败（含归档往返新增 3 项）。

## 轮 65：persona 字段接线 + 计划修订改写场景 + 展示补全（2026-09-08）

| # | 修复项 | 状态 |
|---|--------|------|
| 8c | persona base_shot_duration_ms：四个 category 密度档位接线（显式值优先） | ✅ |
| 8d | persona min_duration_sec：质检最短时长门接入（旧硬编码 10s） | ✅ |
| 8e | persona target_loudness_lufs：audio_agent 写入 timeline.metadata → 渲染 loudnorm I 值（新增 Timeline.metadata 字段） | ✅ |
| 8f | 计划修订改写 raw_scenes：反馈先经 LLM 有界改写场景本体（失败回退仅重译），规划书与管线消费改写后场景 | ✅ |
| 8g | 前端 agent_notes 建议展示（结构警告/素材/剪辑/音频备注 → 建议列表） | ✅ |
| 3.6 | PiP 与主画面同源时备注提示；scene_time 死变量清理 | ✅ |
| 评估 | SSE 真流式：isobase 上游无 token 级流式回调，需上游支持后实施（已在 llm.py 注记） | ⏸ |

回归：后端 1465 passed / 0 失败；前端 tsc 0 错误 + vitest 379/379。

## 轮 66：前端交互 P0 + 管线降级路径修复（2026-09-08）

| # | 修复项 | 状态 |
|---|--------|------|
| B-P0 | 中文输入法 IME isComposing 守卫（聊天/选题输入，组词回车不再误发送） | ✅ |
| B-P0 | SSE 重连回放去重（单调 seq 游标，重连不再重复日志/建议/MG 计数） | ✅ |
| B-P1 | 建议生命周期（终态清空 + 同文案去重 + 上限 8 条；轮63 的 agent_notes 展示已落地） | ✅ |
| E-P0 | 幽灵历史：pointerdown 不推送，pointerup 有实际变更才推送（move/trim/gain 三处）；preSnapshot/label 入 DragState | ✅ |
| E-P0 | 多选拖拽互斥：每片落下前重读最新状态 + 排除全部同批片段 | ✅ |
| E-P1 | rolling 同步 remap source_offset；trim-end 不越过同轨下一片段；粘贴/克隆深拷贝（剥 group_id/深拷贝 nested_timeline）；加轨推历史；拆分先校验后推历史；gain 推历史 | ✅ |
| 后端-D1 | trim 失败时同步丢弃对应 segment（配对不再错位） | ✅ |
| 后端-D2 | owner 校验 Mongo 回退（60s 清理后 jwt 用户不再 403） | ✅ |
| 后端-D3 | 时长对账只统计主视频轨（排除画中画叠加层） | ✅ |
| 后端-D4/D6 | 持久化线程池模块级单例；MG 缓存键修复（self._ck 从未赋值） | ✅ |

回归：后端 1465 passed / 0 失败；前端 tsc 0 错误 + vitest 379/379。

## 轮 67：AgentPanel 体验打磨 + 工具诚实状态（2026-09-08）

| # | 修复项 | 状态 |
|---|--------|------|
| 日志 | 每条时间戳显示；悬停 title 看完整摘要；自动滚动以最后一条 id 为依赖（500 条上限后不再失效）+ 贴底检测（上翻阅读不拉底）；导出按钮（.log 下载） | ✅ |
| 聊天 | 单行 input → textarea（自动增高上限 ~5 行 / Shift+Enter 换行 / 发送后高度复位）；错误消息红色样式区分（会话创建/发送/编辑/初始化/启动失败前缀命中即红框） | ✅ |
| 工具 | SemanticMatchTool 假 SUCCESS → DEPENDENCY_MISSING（CLIP 未接入如实反映；输出结构保留供调用方容错） | ✅ |

回归：后端 1465 passed / 0 失败；前端 tsc 0 错误 + vitest 379/379。

## 轮 68：SSE 真流式 + 归档加固 + 响应式存活线（2026-09-08）

| # | 修复项 | 状态 |
|---|--------|------|
| 批1 | SSE 真流式：LLMService.stream_generate() async 生成器（isobase generate_stream 经线程→Queue 桥接为 AsyncIterator；修正"上游无流式"过时注释）；CREATIVE_BRIEF_SYSTEM 追加 reply 字段保序指令；stream_chat gathering 态走 _stream_gathering_llm 流式路径（增量提取 reply 字段文本 → delta 块 SSE 推送，失败回退缓冲路径）；chat() 加 on_delta 参数穿透 | ✅ |
| 批2 | 归档/导入加固：媒体成员 ZIP_STORED（MP4/JPG 压缩无收益纯烧 CPU）；导入 file.size 预检 413 先于读取（旧实现先全量读再检查 → 2GB 上传先吃满内存）；SpooledTemporaryFile 直接传 ZipFile 不再 BytesIO 二次拷贝；流式解包（1MB 块 copyfileobj）；累计解压上限 2GB（解压炸弹防护） | ✅ |
| 批3a | EditorToolbar 根加 overflow-x-auto（≤1280px 不再裁切）；去重分隔线；Properties 面板 hidden xl:block 安全底线 | ✅ |

回归：后端 1465 passed / 0 失败；前端 tsc 0 错误 + vitest 379/379。

## 轮 69：真流式落地 + 响应式抽屉 + 后端加固 D5/D9/D11/D12（2026-09-08）

| # | 修复项 | 状态 |
|---|--------|------|
| 批1-后端 | stream_chat 真流式落地：旧实现把 on_delta 收集进列表、chat() 返回后一次性 yield（客户端仍是"最后一起出现"）→ 改 asyncio.Queue 桥接（chat 后台任务 + 队列逐块 yield + 断线 cancel）；_stream_gathering_llm 增量提取重写（旧 reply_buf 只在首块赋值 → 仅首块内容被推送；新实现维护值起点 + 已发送字符数，按安全前缀解码，切分点跨 `\n`/`\"`/`\uXXXX` 均不产生错字）；合法 JSON 缺 reply 键时用已提取文本兜底 | ✅ |
| 批1-前端 | AgentPanel delta 消费：首个 delta 创建打字气泡（RequirementMessage.streaming + 光标）、后续 appendRequirementsDelta 增量追加、result 到达用权威回复收尾替换、失败转错误文案；自动滚动依赖末条内容长度（流式时消息数不变）；busy 指示器在流式气泡存在时隐藏 | ✅ |
| 批3b | 响应式折叠抽屉：useMediaQuery hook（jsdom 无 matchMedia 时按宽屏）；workspaceStore.mobilePanel；EditorLayout <lg 隐藏 docked 面板、同组件渲染右侧滑出抽屉（遮罩点击/Esc/关闭按钮，回桌面宽度自动关闭）；Toolbar 三开关 <lg 路由为抽屉开合；Properties ≥xl 才停靠（1024px 不再三栏挤压预览）；<768 时间线加只读提示覆盖层 | ✅ |
| D5 | TaskQueue 加固：max_pending 背压（QueueFullError → /run-async 429，旧实现无限堆积）；等信号量期间取消二次检查（旧实现 cancel 返回 True 但任务照跑）；优先级 aging（每等 60s +1，上限 +2，防低优先级饥饿）；pending_count 计入等信号量任务；cancel 同步清理 Mongo 记录 | ✅ |
| D9 | trace 索引泄漏：_cleanup_stale 只清 _traces/_trace_times → 补清 _seq_counters/_seq_index；_expire_old_events 重建 seq 索引（旧实现索引与事件长度失配退化线性扫描）；_trim_events 去掉无效 `del` 死代码 | ✅ |
| D11 | 删除 `POST /api/pipeline/step/{agent}`：零调用、无 owner/budget/queue 治理（可被用于绕过队列并发上限），文档同步移除 | ✅ |
| D12 | MG 渲染 SSRF 防护：_safe_image_src 白名单（data:image 允许；http(s)/协议相对拒绝；file:// 与绝对路径须落在媒体白名单内且存在；相对路径禁 `..` 穿越）；_sanitize_css_value 中和所有含 `url(` 的 CSS 值（body/元素背景/关键帧/静态透传四路） | ✅ |

回归：后端 1487 passed / 0 失败（+22：流式 8 + trace 2 + 队列 4 + MG 8）；前端 tsc 0 错误 + vitest 386/386（+7：delta 消费 2 + 抽屉 5）。

## 轮 70：审计遗留 D7/D8/D10/D13/D15 + 前端 P2（2026-09-08）

| # | 修复项 | 状态 |
|---|--------|------|
| D8 | Webhook 加固：投递重试（仅网络错误/5xx，3 次指数退避，4xx 立即返回）；`/{id}/test` 走与真实投递同一签名/重试路径（旧实现 test 无签名，配了 secret 的消费者一律拒收）；list/delete/toggle/test 接入 owner 隔离（`authz.filter_by_owner`/`enforce_owner`）；新增 `dispatch_event_bg` 非阻塞分发并替换 4 个管线/渲染终态调用点（旧实现串行 await，慢 webhook 最长 45s 拖住终态写入与 SSE done）；删除零引用的死遗留 `services/webhook.py`（无签名/明文 JSON） | ✅ |
| D10 | proceed 幂等：`Idempotency-Key` 或 `session_id` 命中在跑/排队的管线时返回同一 pipeline_id（旧实现每次调用新建 pipeline_id → 双击起两条管线）；幂等表上限 500 条 | ✅ |
| D13 | CancelledError 传播：`_run_background`/requirements `_queue_handler` 处理完终态后 `raise`（实证：吞掉取消时 TaskQueue 把任务标为 COMPLETED 而非 CANCELLED） | ✅ |
| D15 | 后台任务卫生：`spawn_background` done-callback 记录未观察异常（旧实现静默丢弃）；新增 `cancel_all_background` 并在 lifespan 关闭时调用 | ✅ |
| D7 | 远程渲染健壮性：轮询连续瞬态网络错误容忍 3 次（`REMOTE_RENDER_POLL_MAX_FAILURES`，旧实现首次异常即放弃远程）；产物下载上限 4096MB（`REMOTE_RENDER_MAX_DOWNLOAD_MB`，Content-Length 预检 + 流式累计双重校验，超限删 `.part-*`）；取消传播限制（Worker 无取消端点 → 远程 job 继续跑）写入文档 | ✅ |
| 前端 P2 | 消息时间戳（非法/缺失时间戳不渲染）；错误横幅在成功终态与新一轮启动时自动清除（旧实现上一轮红色错误长期残留） | ✅ |

回归：后端 1509 passed / 0 失败（+22：webhook 10 + D10/D13/D15 6 + 远程 6）；前端 tsc 0 错误 + vitest 387/387（+1 错误横幅清除）。

## 轮 71：ReviewPanel 路径统一 + persona identity 接线（2026-09-08）

| # | 修复项 | 状态 |
|---|--------|------|
| 前端 | ReviewPanel 规划确认统一走 `requirementsApi.proceed`（旧实现直连 `pipelineApi.runAsync`）：会话状态不再停在 plan_ready；`animation_intents`/`material_source_ids`/`subtitle_enabled` 等 user_inputs 不再丢失；owner/预算/超时公式/任务队列与 AgentPanel 路径完全一致；无会话时如实提示而非静默启动 | ✅ |
| 后端 | persona `identity.positioning` / `identity.class_perspective` 接线：结构提示词新增「账号定位/阶层视角」行（Persona 治理面板可编辑但此前结构 Agent 永远读不到）；提取 `StructureAgent._build_system_prompt` 便于单测；`tone` 为 None 时回退 neutral | ✅ |

回归：后端 1513 passed / 0 失败（+4 persona identity）；前端 tsc 0 错误 + vitest 389/389（+2 ReviewPanel 路径）。

## 轮 72：AgentPanel P2 收尾 + 内存上限（2026-09-08）

| # | 修复项 | 状态 |
|---|--------|------|
| 建议 | 逐条关闭（X）+ 复制（剪贴板 + toast）；旧实现只读文本、无法忽略 | ✅ |
| 计时 | 运行中显示本轮耗时 mm:ss（`pipelineStartedAt` 在 setPipelineId 记录，终态清除） | ✅ |
| 日志 | `LogLine` 加 `memo`（展开单条时其余 499 条不再重渲染，store 保留未动条目引用）+ 分组结果 `useMemo` | ✅ |
| 多标签页 | 管线 id 从仅 sessionStorage 改为 sessionStorage + localStorage 镜像（12h TTL）：新标签页也能追踪运行中管线；终态两处同清（`services/storage/pipelineSession.ts`） | ✅ |
| 死状态 | 删除零消费者的 `chatMessages`/`isStreaming`/`addChatMessage`/`setStreaming` | ✅ |
| 内存 | 前端 `requirementsMessages` 内存上限 200 条（草稿仍 50 条）；后端 `_session_owners` 上限 500（会话有 TTL，防长进程增长） | ✅ |

回归：后端 1513 passed / 0 失败；前端 tsc 0 错误 + vitest 398/398（+9：建议/计时 3 + pipelineSession 6）。

## 轮 73：编辑器正确性（快捷键穿透 / 数值钳制 / 修剪重叠）（2026-09-08）

| # | 修复项 | 状态 |
|---|--------|------|
| 快捷键 | 输入框内 Alt 组合一律不触发编辑器快捷键：旧实现只拦「无修饰键」组合，在聊天输入框按 Alt+S 会切换吸附并 `preventDefault`（Alt+←/→ slip、Ctrl+Alt+←/→ slide 同理） | ✅ |
| 关键帧 | 关键帧属性按属性域钳制（opacity 0–1、speed 0.25–4、scale 0.01–10、rotation ±3600、position ±10000、fx 亮度/对比度）并给 input 加 min/max——旧实现不透明度可填 42 | ✅ |
| 修剪 | `trimClipStart` 不越过同轨前一片段结尾（与 `trimClipEnd` 对称）——旧实现向左拖可拉出单轨重叠 | ✅ |

回归：前端 tsc 0 错误 + vitest 403/403（+5：快捷键 3 + 修剪 2）；后端无改动（1513 passed）。

## 批次 8（已全部落地，2026-09-08 收口）

| # | 项 | 落地轮次 | 状态 |
|---|----|---------|------|
| 8a | 前端 proceed 携带 project_id（成品时间线自动回存） | 轮68 前 | ✅ |
| 8b | agent_notes UI 展示（结构警告/素材/剪辑/音频备注 → 建议） | 轮65 | ✅ |
| 8c | ReviewPanel 与 AgentPanel 路径统一（统一 /proceed） | 轮71 | ✅ |
| 8d | SSE 真流式（后端队列桥接 + 前端 delta 打字气泡） | 轮68/69 | ✅ |
| 8e | 计划修改意见改写 raw_scenes（_revise_raw_scenes 有界改写） | 轮65 | ✅ |
| 8f | persona 剩余字段接线（rhythm/min_duration/loudness/identity.positioning/class_perspective） | 轮65/71 | ✅ |
| 8g | 渲染产物 TTL 清理（DELETE /api/render/artifacts） | 批8a 前 | ✅ |

## 验收总则

- 每批配套针对性单测（含已知测试盲区：公网图缓存成功路径、多句配音混音、ProRes 全链）
- 每批全量回归 0 失败 + `python -c "import clipwright.main"`；fix-plan §10 记录
- 总验收：默认环境（仅 LLM key）成片=有声字幕正确的完整视频；配置火山三件套后 AI 生成素材可检索入轨；ProRes/远程渲染/导出预设按文档承诺工作
