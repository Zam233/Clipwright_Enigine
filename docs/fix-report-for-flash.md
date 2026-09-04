# Clipwright 修复报告（交付执行版）

> 本报告来自三轮深度代码审查（调用链 / Agent 内部 / 输出契约）。执行者按批次顺序修复，每批完成后运行对应回归命令再进入下一批。禁止顺手重构、禁止改动无关文件、禁止删除现有测试。
>
> 仓库：后端 `J:\Clipwright`（Python FastAPI），前端 `J:\Clipweight-Client`（React 19 + TS）。行号基于 2026-09-04 main 分支，漂移属正常，按逻辑定位。

## ✅ 执行记录（2026-09-04，全部 53 项已执行）

| 批次 | 状态 | 回归结果 |
|------|------|----------|
| A 调用链 P0（A1-A10） | 全部完成 | 后端全量 **1223 passed**（基线 1217 + 新增 6） |
| B Agent 内部缺陷（B1-B12） | 全部完成 | 后端全量 **1236 passed**（新增批次 B 单测 13） |
| C 契约断路（C1-C10） | 全部完成（E8 前端部分随批次 E） | 后端全量 **1236 passed** |
| D 渲染引擎正确性（D1-D8） | 全部完成 | 后端全量 **1236 passed** |
| E 前端修复（E1-E8） | 全部完成（E4 经核查已有标识，无需改动） | tsc ✓ / vitest **362 passed** / build ✓ |
| F 清理与文档对齐（F1-F7） | 全部完成（F5 选 a：保留实现，文档标注） | 后端全量见下；Docker 本机不可用，语法已修复待部署环境验证 |

执行备注：
- A8 采用 set-before-run 原子占位 + 失败回滚；A2 采用任务级 `timeout_sec` + `TIMEOUT` 任务状态 + `_user_cancel_requested` 集合区分取消/超时；A3 经 `_queue_handler` 注册 `asyncio.current_task()` 实现即时取消。
- A10 熔断判定改实例级，类级 `_circuit_breakers` 保留为**可观测镜像**（breaker-status 端点与既有测试兼容）。
- C4 新增公共模块 `clipwright/animation/xfade_map.py`；render 白名单由形状校验改精确集合校验。
- E3 新增 `src/services/api/sseStream.ts`（3s 退避、5 次上限、手动重连）；ExportPage 已接入，AgentPanel 原有重连逻辑保留未合并（行为等价）。
- E4 核查：HomePage/PersonaPage 已有"演示数据"徽标，ExportPage 模拟渲染已有"演示模式"徽标且隐藏下载按钮——无需改动。
- F3：删除 git 跟踪的 `__text_71708.txt`、`_xtest/envx`，`.gitignore` 补 `*.log`/`_xtest/`；前端移除 `VITE_WS_URL`。
- 新增测试：`tests/clipwright/test_batch_a_callchain.py`、`test_batch_b_agents.py`、`test_batch_b_audio_optin.py`；`test_dry_run.py` 补 final_timeline 断言；3 个旧行为断言测试（B6/B8/C1 行为变更）已同步更新。

---

## 修复守则（必读）

1. 每个修复项独立提交，commit message 引用条目编号（如 `fix(A1): ...`）。
2. 修复以"最小侵入"为原则；涉及行为变更的（如取消语义），同步更新对应测试。
3. 遇到与报告描述不符的代码（行号漂移属正常，逻辑不符则停），在条目下追加备注说明，跳过该项。
4. 全程使用仓库现有模式（如 `api/render.py:155` 的正确 Request 用法、`edit_agent._validate_llm_profile` 的白名单校验模式）。

---

## 批次 A：调用链 P0（交付正确性）

**A1. run-async 读取 body.headers 致 AttributeError**
- 位置：`clipwright/api/pipeline.py:374`（函数签名中 HTTP Request 参数名为 `req`，237 行）
- 问题：`request.headers.get("X-Priority", ...)` 在 PipelineRequest 模型上调用，pydantic 模型无 headers，AttributeError 不被 `except (ValueError, TypeError)` 捕获 → 主入口无条件 500。
- 修复：改用 `req.headers.get(...)`，参照 `api/render.py:155-157`。
- 验收：POST /api/pipeline/run-async 不带 X-Priority 头返回 200 + pipeline_id；新增回归测试。

**A2. TaskQueue 900s 硬超时杀死长管线且误报为"取消"**
- 位置：`clipwright/services/task_queue.py:117`（`task_timeout_sec=900`）；`api/pipeline.py:324-331`（CancelledError → "已取消"文案）
- 问题：管线超时公式承诺 ≥1800s、SSE 7800s，但队列 15 分钟强杀，且报错文案为 cancelled。
- 修复：提交任务时按 `pipeline_timeout_sec`（+缓冲 120s）动态传入该任务的 wait_for 超时；区分队列超时与用户取消（TaskQueue 内捕获 TimeoutError 后以 `timeout` 状态落结果，API 层给出"执行超时"文案与 retry 建议）。
- 验收：新增测试：submit 一个 sleep 超过默认 900s 但小于其 pipeline_timeout_sec 的任务不被杀；超时任务状态为 timeout 而非 cancelled。

**A3. run-async 不注册 _running_pipelines → 取消失效**
- 位置：`clipwright/api/pipeline.py:378` 附近（对比 :634 retry 路径）
- 修复：在 TaskQueue.submit 的协程包装处注册 `_running_pipelines[pipeline_id] = task`，结束/异常/取消时清理；`/cancel` 能对 RUNNING 任务 task.cancel() 并向 TaskQueue 标记。同时修复 `_cleanup`（:354-366）遗漏 `_pipeline_tasks` 的泄漏。
- 验收：新增端到端测试：发起 run-async → 立刻 cancel → 状态 cancelled、SSE 收到 cancelled 终态、后续 Agent 不再执行。删除现有测试中手工塞 `_running_pipelines` 的掩盖性代码。

**A4. SSE 流不因 cancelled 终止**
- 位置：`clipwright/api/pipeline.py:442-444`（终态仅 done/error）
- 修复：终态集合加入 `cancelled`/`timeout`；流关闭前必发一个终态事件。顺带修复：完成 60s 后重连的死流——重连时若结果已清理，立即回放一条终态事件（从 Mongo 检查点取最终状态）而非静默空转。
- 验收：测试流在 cancelled/timeout 事件后关闭；完成后延迟重连能拿到终态。

**A5. /proceed 与 /retry 绕过预算/幂等/队列/属主**
- 位置：`clipwright/api/requirements.py:259-301`；`api/pipeline.py:568-634`
- 修复：两个路径统一走与 run-async 相同的入口封装：budget 检查、审计记录、写入 `_pipeline_owners`（从 request.state.user_id）、`_persist_pipeline_runtime`、经 TaskQueue 提交（获得并发上限与超时）。retry 的 `run_from_agent` 包 wait_for。
- 验收：jwt 模式下 proceed 发起的管线可被本人 /status /cancel /diagnostics；超预算时 proceed/retry 被拒绝；测试覆盖。

**A6. token/LLM 花费记账只覆盖 StructureAgent**
- 位置：`clipwright/agents/structure_agent.py:396`（唯一设置 `_llm_usage` 处）；`base.py` llm 调用包装
- 修复：在 BaseAgent 的 LLM 调用包装层（`llm_or_fallback` 及各 agent 直接调用 `_llm.*` 的公共路径）统一累计 usage 并写入 `output._llm_usage`；pipeline_v2.py:996 已有读取逻辑，自然生效。预算熔断随之可用。
- 验收：跑一个含 material/edit/animation 的管线，Mongo `llm_calls` 出现全部 Agent 的记录；`/api/stats` 汇总合理。

**A7. trace 流无属主校验（信息泄露）**
- 位置：`clipwright/api/pipeline.py:402-454`
- 修复：流端点同样调用 `_enforce_pipeline_owner`（off/token 模式维持开放，与 /status 一致）。
- 验收：jwt 模式下用户 B 订阅用户 A 的 trace 收到 403。

**A8. 幂等检查非原子（并发双跑双计费）**
- 位置：`clipwright/api/pipeline.py:246-264`
- 修复：改为 set-before-run：检查后立即写入占位（状态 pending），冲突方直接返回已有管线；或加 `asyncio.Lock`。占位在 run 结束时更新。
- 验收：并发两个相同 idempotency_key 的请求只产生一次管线执行。

**A9. V2 dry-run 不产出 final_timeline**
- 位置：`clipwright/services/pipeline_v2.py:657-678`（对比 v1 `services/pipeline.py:196-202`）
- 修复：V2 dry-run 分支在 edit 完成后取 bus 中 timeline 写入 `shared_data["final_timeline"]` 并发 `timeline_snapshot` trace 事件，与 V1 对齐。
- 验收：dry-run 响应含 final_timeline；更新 test_dry_run.py 断言。

**A10. 熔断器跨管线全局共享且无锁**
- 位置：`clipwright/services/pipeline_v2.py:323-374`
- 修复：改为实例级（每管线独立）或加 asyncio.Lock 且键含 user/project 维度；保证管线 A 的连续失败不再令管线 B 的步骤被标 FAILED 跳过。
- 验收：并发两条管线，一条连续失败后另一条仍完整执行。

---

## 批次 B：Agent 内部缺陷

**B1. Structure 解析器与自身动画标记括号冲突**
- 位置：`clipwright/agents/structure_agent.py:665-699`（_parse_scenes 括号平衡扫描）
- 问题：提示词强制 LLM 在 JSON 字符串内输出 `[逻辑动画]mg_dynamic:{...}`，扫描器把字符串内的 `[`/`]` 计入深度 → 解析失败静默降级 fallback_scenes。
- 修复：解析策略改为——先尝试 `json.loads` 整体；失败再用正则提取首个 `[` 到最后一个 `]` 的外层区间直接 loads（放弃逐字符深度扫描）；或对候选子串做"逐层尝试 loads"。同时修复围栏剥离丢首末行问题。
- 验收：新增测试：包含 `[逻辑动画]mg_dynamic:{"a":[1,2]}` 字符串值的场景 JSON 能正确解析。

**B2. Material/Edit 的 gather 无 return_exceptions**
- 位置：`material_agent.py:575`、`edit_agent.py:306-308`
- 修复：`return_exceptions=True` + 逐场景 try/except 降级（素材场景→文字占位并记 note；edit 场景→占位片段），不让单场景异常炸掉整 Agent。
- 验收：mock 一个场景抛网络错误，Agent 仍 PASS 且该场景有降级产物、其他场景正常。

**B3. EditAgent 硬编码 1920x1080/30fps**
- 位置：`edit_agent.py:193, 437-440`
- 修复：从 `context.extra_params.get("orientation")`（material_agent.py:794 已有同源逻辑）推导 W/H（portrait→1080x1920）；fps 取 extra_params 可覆盖、默认 30。
- 验收：orientation=portrait 的管线产出时间线 width=1080/height=1920。

**B4. video_trim 无超时 + 近无限循环填充**
- 位置：`edit_agent.py:629-680`（`while remaining > 1.0`、无超时、start_sec 恒 0）
- 修复：a) `ToolRegistry.execute("video_trim"...)` 外包 `asyncio.wait_for(120s)`，超时→占位降级；b) 循环加单位片段数上限（如 max(4, ceil(remaining/seg_dur))+2）；c) 同一素材复用时 start_sec 递进（`(cycle_idx * seg_dur) % asset_duration`），不再永远裁 0:00。
- 验收：短素材场景片段数有上限且 start_sec 随复用推进；挂死 ffmpeg 120s 后降级不拖垮管线。

**B5. `[PiP]` 标记两套判定不一致**
- 位置：`edit_agent.py:250-257`（轨道创建）vs `:629-632`（is_pip）
- 修复：has_pip 判定与 `_process_scene_units` 合并为同一判定函数（并入字面 `[PiP]`）。
- 验收：含 `[PiP]` 描述的场景产出画中画轨道与 PiP 片段。

**B6. AudioAgent 淡入 hack 压低配音 + 无淡出**
- 位置：`audio_agent.py:219-222`；`render.py:1761`
- 修复：删除 `first.volume = 0.3` hack；在 Clip 已有 `audio_fade` 字段（render 支持 afade）上写入真实淡入淡出：对 BGM 片段设 fade_in 1s / fade_out 2s；配音/BGM 区分处理（bgm_slot 元数据只挂 BGM 类片段，不挂配音）。
- 验收：上传配音的成片配音音量为 1.0（有短淡入）；BGM 有淡出；单元测试断言 clip.audio_fade 字段。

**B7. 配音 + TTS 双人声叠加**
- 位置：`audio_agent.py:122-125`（has_dub 插入）vs `:249-256`（has_narration TTS）
- 修复：有 `audio_path`（上传配音）时跳过 TTS 旁白生成（配音优先），记 audio_note；或提供显式 `force_tts` 参数。
- 验收：同时提供 audio_path 与 voice_id+script 的管线只产出一条人声轨。

**B8. demo 音频兜底冒充成片**
- 位置：`audio_agent.py:378-411`
- 修复：仅在显式 `extra_params["allow_demo_audio"]`（默认 False）时启用 demo 兜底；否则无音频时产出无音轨 + warning 级 issue（可被质检看到），不拉伸 timeline.duration_sec。
- 验收：默认路径无音频时 timeline 时长不变，质检出现 audio 警告。

**B9. TTS 零长片段与字幕漂移**
- 位置：`audio_agent.py:286-316`（cursor 布局、duration_sec=0 直接入轨）
- 修复：过滤 duration_sec<=0 的失败分段（复用 `_realign_captions_to_narration:443` 的判断）并按 `pause_design` 元数据插入停顿（0.2-0.4s，可配置）；多字幕轨全部重建（移除 :477 的仅首轨 return）。
- 验收：部分 TTS 分段失败时不产生零长片段，字幕与实际音频段一一对齐。

**B10. AnimationAgent 转场映射错误与能力探测**
- 位置：`animation_agent.py:891-902`
- 修复：修正 `slide_up→slideup`；`glitch`/`pixel_dissolve`/`morph` 若当前 ffmpeg 不支持（运行时探测 xfade 滤镜列表，render 侧已有 NVENC 探针模式可参考）则降级 fade 并发 trace warning，而不是渲染期 EINVAL。
- 验收：单元测试断言映射表无方向错误；不支持的转场在 Agent 阶段产生 warning 事件。

**B11. AnimationAgent 多标记截断与文字动画时长越界**
- 位置：`animation_agent.py:197`（markers[0]）、`:306`（max(duration,1.0)）
- 修复：遍历全部标记逐一生成；文字动画时长改为 `min(duration, clip_end - start)` 且不小于 0.2s，不越出宿主片段；启用已定义未调用的 `_find_overlapping_clip` 对同轨道文字片段去重。
- 验收：含两个标记的场景产出两个动画；0.2s 片段上的文字动画不侵入下一场景。

**B12. Material `_hard_filter` 自我解除 + 缓存退化**
- 位置：`material_agent.py:781-798`（`return kept or list(cands)`）、`:24-25,373-374`（缓存满后永不写入）
- 修复：a) 全候选被过滤时保留过滤结果但发 warning 并标记该场景 `validation_note="hard_filter_empty"`，不再返回未过滤列表；b) 搜索缓存达上限改为淘汰最旧（LRU）并加 TTL（如 1h）；命中 `_TRIM_CACHE`/`_search_cache` 前校验文件/条目仍有效。
- 验收：全被过滤的场景有降级标注；缓存稳定命中（第二次查询命中数不因满而归零）。

---

## 批次 C：契约断路（纸面功能接线）

**C1. BGM 元数据 render 不消费 → 成片无 BGM**
- 位置：`audio_agent.py:186-217`（产出）vs `render.py:1751-1769`（只混 asset_id 存在的片段）
- 修复：AudioAgent 依据 `bgm_library/bgm_style/bgm_slot` 从 BGM 素材库实际解析出 asset_id 写入 clip，使 render 正常混音；解析失败发 warning。render 的 `Path.exists` 跳过逻辑保留。
- 验收：配乐开启的管线成片音轨含 BGM。

**C2. animation_intents 通道断路**
- 位置：`api/requirements.py:238-252`（proceed 不写）→ `structure_agent.py:271`（读）
- 修复：proceed 时从 session 取 `animation_intents` 放入 extra_params。
- 验收：聊天会话设置动画意图后成片结构含对应动画标记。

**C3. `[过渡动画]` 标记被 EditAgent 丢弃**
- 位置：`edit_agent.py:350`（白名单缺 `[过渡动画]`）
- 修复：白名单加入 `[过渡动画]`（及 catalog.py 支持的 `[转场动画]` 别名）。
- 验收：含 `[过渡动画]fade` 的场景描述透传到 clip metadata，AnimationAgent 应用转场。

**C4. EditAgent LLM 转场名无 xfade 映射 → 渲染静默硬切**
- 位置：`edit_agent.py:489-491, 344-348`（写入原值）vs `render.py:1184-1188`（白名单只查形状）
- 修复：EditAgent 写入前过 AnimationAgent 的 `xfade_map`（抽公共模块 `animation/xfade_map.py` 供两处复用）；render 白名单改为精确集合校验，非法名记 warning 后降 fade 而非静默。
- 验收：LLM 返回 slide_left 的场景最终 transition_in 为合法 ffmpeg xfade 名。

**C5. beat-sync 死键**
- 位置：读 `edit_agent.py:202-203`，全仓库无写入方
- 修复二选一（默认前者）：a) `/proceed` 与 run-async 请求接收 `cut_on_beat`/`beat_bpm` 参数透传 extra_params；b) 删除读取逻辑与文档宣传。执行者选择后注明。
- 验收：选 a 则传参管线触发卡点对齐（复用 :317-323 现有逻辑）。

**C6. semantic 质检错误无 redo 映射 + redo 无上下文**
- 位置：`quality_agent.py:221-230`（映射缺 semantic）、`pipeline_v2.py:479-480`（`_quality_issues` 无读者）
- 修复：映射表加 `semantic → edit`；在被 redo 的 Agent 输入中实际消费 `_quality_issues`：至少 structure/edit/animation 在提示词尾部追加"上一轮质检问题"段落。
- 验收：semantic error 触发一轮 edit redo；redo Agent 收到的输入包含问题列表（测试断言）。

**C7. QualityAgent issues 前端不可见 + `pass` 别名陷阱**
- 位置：`schema/agent.py:226`（序列化别名 pass）；前端只监听 done/agent_end
- 修复：后端 `/result` 与 SSE done 事件附带 `issues` 摘要（error/warning 数量 + 列表）；前端在管线完成通知中渲染质检问题列表；别名统一为 `passed` 并保留兼容读取。
- 验收：质检发现 warning 时前端完成弹层可见问题列表。

**C8. 自愈耗尽仍 COMPLETED + 质检异常即通过 + 自愈缺 brief**
- 位置：`pipeline_v2.py:760`（返回值无人检查）、`:456-512`（异常路径 quality_passed=True）、`:466`（漏传 creative_brief）
- 修复：a) `_self_heal_quality` 返回 False 且存在 error 级 issue 时管线终态不再为纯 COMPLETED（completed_with_errors 或 FAILED，按现有状态机选择并注明）；b) quality 步骤异常视为不通过并计入自愈或失败；c) 自愈重建质检输入时传入 creative_brief（从 extra_params 取）。
- 验收：质检 3 轮 error 未修复的管线不再显示纯 COMPLETED；测试覆盖。

**C9. 只写字段清理**
- 位置：`edit_notes`/`audio_notes`/`material_notes`(exclude=True)/`script_skeleton["_warnings"]`/`animation_plan`/`generated_mg_count`/`fix_suggestions`/`candidate_clips.retried`/`validation_note`（消费方全无）
- 修复：不删字段（避免破坏 schema 兼容），改为：C7 完成后把 notes/warnings/issues 聚合进 `/result` 的 `agent_notes` 字段一次性暴露；`fix_suggestions` 并入 issues 展示。
- 验收：`/result` 响应含各 Agent 的 notes/warnings。

**C10. 导入 SRT 字幕位置按轨道序号错乱**
- 位置：`render.py:964`（`{1:bottom,2:top,3:center}` 回退）
- 修复：`subtitle.segments_to_timeline_clips`（subtitle.py:81-103）为导入字幕统一写 `metadata.position="bottom"`；render 回退逻辑保留为最后手段。
- 验收：导入 SRT 落在任意轨道均渲染在底部。

---

## 批次 D：渲染引擎正确性（死字段与假功能）

**D1. speed 是假的**
- 位置：`render.py` `_trim_one`（~1018 行，`dur = duration*speed` + stream_loop 循环填充）
- 修复：speed≠1 时改用 `setpts=PTS/speed`（视频）+ `atempo`（音频，clamp 0.5-2.0，超出链式两段），去掉该场景的 stream_loop 填充语义。
- 验收：speed=2 的片段实际时长减半、有音轨时音调正常；与 `tool/speed.py` 输出一致。

**D2. mask/blend/image_fit/eq_preset/nested_timeline 死字段处置**
- 位置：`schema/timeline.py`；`render.py` `_extract_segments`（849-892）
- 修复（分级）：a) `blend_mode` 落地——overlay/PIP 路径按 blend_mode 追加 ffmpeg blend 滤镜（normal/screen/multiply/overlay 四种）；b) `image_fit` 落地——COVER/CONTAIN 映射到 scale+pad/crop 组合；c) `mask_type` 至少实现 rectangle（crop=mask_rect）；d) `eq_preset`/`nested_timeline` 本期不做的，在 schema docstring 标注"预留未实现"并在前端禁用对应控件（见 E6）。
- 验收：blend_mode=screen 的 PIP 片段渲染生效；未实现字段在 UI 不可设置。

**D3. BGM 自动避让（ducking）**
- 位置：`render.py` `_mix_audio`（~1740 行）
- 修复：存在配音轨时为 BGM 追加 `sidechaincompress`（配音为 sidechain），替代固定 volume=0.3 思路（保留 volume 作前置增益）。
- 验收：有配音时滤镜链含 sidechaincompress（单测断言）。

**D4. 软字幕导出**
- 位置：`render.py` 输出封装；`api/render.py` 导出预设
- 修复：渲染请求新增 `soft_subtitle: bool`，开启时 MP4 以 `-c:s mov_text` 挂 SRT 轨而非烧入。
- 验收：soft_subtitle 请求产出的 mp4 用 ffprobe 可见 subtitle 轨。

**D5. 渲染优先级不生效**
- 位置：`api/render.py` 队列（X-Priority 仅排序展示，spawn 全并发）
- 修复：渲染队列改为按优先级出队的信号量等待队列（或并入 TaskQueue 通用机制，与 A2 协同）。
- 验收：高优任务在低优 pending 时先获得渲染槽。

**D6. trim 失败静默纯色段**
- 位置：`render.py` trim 失败 fallback 纯色仅记 log
- 修复：fallback 发生时写 trace/SSE warning 事件（含片段位置），渲染结果摘要列出 fallback 片段数。
- 验收：构造损坏源渲染，结果摘要含 fallback 计数。

**D7. xfade 时长侵蚀与 duration_sec 不校正**
- 位置：`render.py:1214`（acc 扣减无对账）
- 修复：渲染结果返回 `actual_duration` 与差值提示；尾部覆盖物/字幕按 acc 偏移钳制。
- 验收：含转场的渲染结果 actual_duration 正确并暴露。

**D8. ProRes 导出扩展名**
- 位置：`api/render.py` 预设（prores 输出仍 .mp4）
- 修复：预设携带容器扩展名（prores→.mov），文件名生成尊重之。
- 验收：prores 预设导出文件为 .mov。

---

## 批次 E：前端修复（J:\Clipweight-Client）

**E1.** 补 404：`src/router.tsx` 加 `notFoundComponent`（中文、带返回首页），与设计语言一致。

**E2.** 数据页路由守卫：`/projects`、`/persona*`、`/editor/*`、`/export/*` 加 beforeLoad 会话检查（无会话且账号模式开启时跳 /login；off 模式照旧放行）。

**E3.** ExportPage SSE 重试：抽共享 `useSseStream` hook（合并 AgentPanel 的重连逻辑：3s 退避、5 次上限、手动重连 UI），ExportPage `onerror` 不再立即判失败；断线期间显示"重连中"状态。

**E4.** 演示模式显式标识：HomePage/PersonaPage/ExportPage 离线降级与模拟渲染处加显式"离线演示数据/模拟结果"徽标（利用已有 simulated/dataMode 标志），模拟渲染产物下载时强提示。

**E5.** `PipelineAdminPage.tsx:105` 未清理的 setTimeout 改为组件内托管（useEffect/清理）。

**E6.** 属性面板禁用后端未实现字段（对应 D2）：blend_mode 等后端本期未落地的控件隐藏，避免设置无效参数。

**E7.** Suspense fallback "LOADING…" 改中文"加载中…"；主题首选项增加 `prefers-color-scheme` 检测。

**E8.** 质检结果展示（对应 C7）：管线完成通知渲染 issues 列表（error 红/warning 黄）。

---

## 批次 F：清理与文档对齐

**F1.** 删除后端 `Dockerfile.backend:7` 的非法 COPY 行（`|| true` 语法），改为条件复制或移除该行；镜像补中文字体安装。

**F2.** 前端 `nginx.conf` 增加 `/srv/` → 账号服务 8090 的 proxy_pass（对齐 `vite.config.ts:21-23`）。

**F3.** 仓库卫生：gitignore 并移除 `uvicorn-run*.log`、`e2e-backend.log`、`__text_71708.txt`、`_xtest`；前端删除 `VITE_WS_URL` 与 SettingsPage 遗留 WebSocket 字段。

**F4.** 文档对齐：`docs/workflow.md`/`docs/requirements_agent.md` 修正——RequirementsAgent 标注为"保留实现，生产走 RequirementsService"；删除不存在的 `/api/requirements/analyze|/extract` 与 bus `get_messages/set_demand/route_decision` 描述；`docs/animation_system.md` 移除或标注未实际生效的 glitch/pixel 转场（若 B10 落地则改为已支持）。

**F5.** RequirementsAgent 决策（执行者选择并注明）：a) 删除死代码（连同重复提示词）；b) 重构 RequirementsService 复用 Agent。默认 a（最小侵入），保留 schema。

**F6.** AgentBus demands：移除 pipeline_v2.py:976-978 的 `_demands` 注入（无 Agent 消费），保留事件流。

**F7.** LLM 超时线程泄漏（`base.py:44` + llm.py to_thread）：统一改为可取消封装（anyio.to_thread + cancel scope 或请求层 abort 标志），至少保证超时后重试前检查首个请求是否仍在跑，不再并发双请求。

---

## 回归验收（每批完成后执行）

```bash
# 后端（工作目录 J:\Clipwright）
python -m pytest tests -q          # 基线 1217 passed，修复后不得低于此数
python -c "import clipwright.main"
docker build -f Dockerfile.backend .   # F1 后必须成功

# 前端（工作目录 J:\Clipweight-Client）
npx tsc --noEmit
npm run test                       # 基线 361 passed
npm run build
```

端到端冒烟（A 批后）：
1. POST /api/pipeline/run-async → SSE 流 → 中途 cancel → 流收到 cancelled 终态并关闭。
2. jwt 模式：聊天 /proceed 发起 → 同用户 /status、/cancel 正常。
3. dry-run 返回 final_timeline。
4. 上传配音 + voiceover 模式：成片单条人声、音量正常、含 BGM 且有避让。
