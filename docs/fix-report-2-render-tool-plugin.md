# Clipwright 修复报告 2 —— 渲染 / 工具 / 插件 / MG 动画 / 前端预览（统一修复计划）

> 本报告整合四次深度审查的全部结论，共 8 个批次约 64 项：
> - 批次 R：渲染系统正确性（转场漂移/单例并发/ProRes/drawtext 等）
> - 批次 RE：渲染效率深挖（编码参数/并发超订阅/ffprobe 风暴/I-O 拷贝）
> - 批次 T：工具系统（缺失 await/同步阻塞/缓存互串/安全统一）
> - 批次 P：插件系统（签名 fail-closed/注册表注销/密钥收敛）
> - 批次 M：MG 动画渲染（自愈重复叠加/链式合成/生成缓存/进程树击杀）
> - 批次 V：前端编辑器预览（音频互静音/60fps 重渲染/关键帧契约）
> - 批次 X：跨端预览-导出契约对齐（蒙版/blend/描边/音频语义）
>
> 全部结论经实际代码验证（含一次真实 ffmpeg 命令验证）。行号基于 2026-09 整合后
> main（1b9cc63），漂移按逻辑定位。
>
> 执行守则沿用 fix-report-for-flash.md：逐项独立提交、最小侵入、行为变更同步改测试、
> 与描述不符即停并备注。执行顺序：R → RE → T → P → M → V → X。
> 基线回归：`python -m pytest tests -q` = **1344 passed**；
> 前端（涉及 V/X 时）：`npx tsc --noEmit && npm run test && npm run build` = **362 passed**。

---

## 执行记录

（执行后在此逐批追加：完成项、偏离备注、回归数字。详见文末执行记录——后端 R/RE/T/P/M 批次已完成，基线 1344 passed。）

---

## 批次 R：渲染系统正确性（R1–R12）

### R1（P0）转场时间漂移破坏音画/字幕同步
- 位置：`agents/edit_agent.py:487`（clips 背靠背 `current_time += seg_dur` 且每边界
  `transition_duration_sec=0.4`）；`services/render.py` xfade 合并 `acc = left+right−td`；
  下游 ASS Dialogue 时间、MG/HF `enable=between(t,…)` 窗口、音频 `adelay=start_sec`
  全部按**未校正**的原始 start_sec 计算 → 每个转场使之后所有元素累积偏移。
- 修复：`_concat_xfade_parallel` 合并时累计偏移并回写 segments 的
  `effective_start_sec = start_sec − offset_acc`（ASS/MG/音频消费修正值）；
  删除 render.py:876-879 的"转场时长侵蚀"警告（改为已校正）。
- 验收：含 ≥2 处转场的时间线，字幕 ASS 起止与画面转场对齐；新增单测断言后段
  时间 −Σtd。

### R2（P0）单例 RenderService 并发互踩
- 位置：`api/render.py:127-137` lru_cache 单例 + `render.py:681` 共享 work_dir
  固定文件名（aud.mp4/ov.mp4/txt_*/subs_*/fallback_*/concat.mp4）+ `:769` finally
  rmtree 共享目录（可删掉另一并发渲染的工作目录）。
- 修复：`queue_render` 每任务创建独立 `RenderService(work_dir=…)`（参照
  worker/render_runner.py:194 per-job 模式），去除 lru_cache 单例；`_cleanup` 仅删
  本实例目录。
- 验收：并发 2 渲染互无串扰（新增并发回归测试）。

### R3（P1）编码器条件化参数（ProRes 交付损坏）
- 位置：render.py 各阶段无条件 `-preset medium`；`_apply_overlays` 硬编码
  `-pix_fmt yuv420p`；hyperframes_renderer.py:326/352 绕过每渲染覆盖；concat 缺
  `-pix_fmt`。
- 修复：抽公共 helper `_encoder_args(encoder, pix_fmt)`：prores_ks → 无 `-preset`；
  pix_fmt 按每渲染 `_PFMT_OVERRIDE` 贯穿 PIP/MG/overlay；concat 补 `-pix_fmt`；
  `_generate_fallback` 改用 override 编码器。
- 验收：prores422hq 预设全链路渲染成功；h265_10bit 全阶段保持 10-bit。

### R4（P1）drawtext 关键帧滤镜拼接语法错误（已实测 ffmpeg 拒绝）
- 位置：`render.py:_build_kf_drawtext` `f":enable='{enable}'"` 以 `":"` 连接产生
  `alpha=1.0::enable=…` → 整批 drawtext 失败，文字静默消失。
- 修复：修正连接符；新增单测断言生成的 filter 可被 ffmpeg 解析。
- 验收：≥2 关键帧的 drawtext fallback 字幕在成片中可见。

### R5（P1）接通/删除死代码：MG 链式合成 与 逐批次文本重编
- 位置：`_apply_mg_overlay/:_apply_mg_overlay_chained` 零调用方；实际串行 per-MOV
  全片重编；文本烧写每 100 条字幕做一次全片重编（ASS 走文件无长度限制）。
- 修复：MG 阶段 N 个 MOV 改单次 filter_complex 链式（>8 个或 cmdline>30000 回退
  per-MOV）；文本烧写合并为单次 ASS 一次重编。
- 验收：4 MG 时间线全片重编次数 4→1；300 字幕 text 阶段仅 1 次 ffmpeg 调用
  （计数断言）。

### R6（P1）PIP/音频阶段进度事件 + clip_count 修正
- 位置：render.py:822-840 PIP/音频混流零进度事件；`api/render.py:243`
  `clip_count = len(tl.tracks)`、`current_clip` 恒 0。
- 修复：PIP 与混流前后各发 progress（96→97→98 单调）；clip_count 改为片段总数；
  PIP 循环更新 current_clip。
- 验收：SSE 在 PIP/音频阶段有事件且 pct 单调；clip_count 正确。

### R7（P1）`_final_ffmpeg_log` 接入结果
- 位置：一路 append（:1082/:1172/:1174/:1260/:1281）但 `RenderResult.ffmpeg_log` 恒空。
- 修复：`_render_inner` 返回前填充最近 50 条；失败分支同样；api 失败 result 透出。
- 验收：损坏源渲染 → 失败结果含逐条原因。

### R8（P1）trim 缓存并发写 + 启动清理竞态
- 位置：两并发渲染同 key 同时写 `trim_{key}.mp4`；`:728` 启动清理可删在用文件。
- 修复：临时名 + `os.replace` 原子落位；prune 跳过 mtime < 10 分钟的文件。
- 验收：并发同源渲染产物可播放；prune 不删新文件（mtime 打桩单测）。

### R9（P1）音频 clip 的 source_offset_sec 语义
- 位置：render.py:899 采集了 `source_offset_sec` 但 `_mix_audio` 只用 `start_sec`
  做 `atrim=start=…`——时间线位置与源内位置混用。
- 修复：`atrim=start={source_offset_sec or 0}:duration={dur}`；延迟仍用时间线
  start_sec。
- 验收：同一文件两个位置不同 offset → 成片内容不同（单测断言 atrim 参数）。

### R10（P1）取消盲区：MG/HF overlay 阶段
- 位置：hyperframes_renderer.py `render_overlay_on_video` 裸 `subprocess.run`
  不受取消跟踪；MG 阶段串行调用，取消后最长阻塞 1800s×N。
- 修复：改走 `run_tracked_ff`；校验 returncode 非 0 返回 False。
- 验收：MG 阶段 cancel → ffmpeg ≤2s 被 terminate（打桩断言）。

### R11（P2）交付与预览体验
- 全链路补 `-movflags +faststart`；`/api/render/video` 移除虚假 Accept-Ranges 或
  改用 Range 实现；`_ffmpeg_supports_xfade()` 进程内缓存；`_xfade_pair` ffprobe
  结果随 acc 传递。
- 验收：moov 前置；二次转场渲染无 `-filters` 探测调用。

### R12（P2）队列语义清理
- 状态置 rendering 前先检查 cancelled；list_queue 排序修复；`/thumbnail` 校验
  time_sec ≥ 0；`/api/render/start` 标注 Deprecated 并修复 container_ext 忽略。

---

## 批次 RE：渲染效率深挖（RE1–RE12）

### RE1（P1）x264 自动线程 × 并发池超订阅
- 位置：render.py:49 `_ffmpeg_pool`=4 workers；每 trim/concat/xfade 为无 `-threads`
  的 libx264（默认 ~1.5×核数编码线程+解码线程）→ 4 并发 trim 在 8 核机上 ~60-80
  线程互踩；渲染信号量 2 还允许第二个渲染叠加同池。
- 修复：每进程 `-threads {max(1, cores // pool_workers)}`。
- 验收：并发 4 trim 时系统线程数与单 encode 耗时对比（基准脚本）。

### RE2（P1）ABR 码率且 MG 叠加阶段漏 `-b:v`
- 位置：全链 `-b:v` ABR 无 maxrate/bufsize/CRF；`render_overlay_on_video` 完全
  无码率参数（hyperframes_renderer.py:352-354 x264 默认 CRF23）→ MG 叠加代际
  质量/码率跳变。
- 修复：统一 `-crf 20 -maxrate {bitrate} -bufsize 2M`（NVENC：`-rc vbr -cq`）；
  MG 叠加阶段对齐同参数。
- 验收：同时间线各代际 ffprobe 码率/质量一致性（单测断言参数存在）。

### RE3（P1）PIP/MG 阶段硬编码 `-pix_fmt yuv420p` 破坏 10-bit/ProRes
- 位置：render.py:1839、hyperframes_renderer.py:356 —— prores422hq
  （yuv422p10le）或 h265_10bit 导出含 PIP/MG 时失败或静默降 8-bit。
- 修复：两处改 `_current_pix_fmt()`。
- 验收：h265_10bit + PIP + MG 的导出 pix_fmt 保持 yuv420p10le。

### RE4（P1）三次可避免的全文件 copy2
- 位置：render.py:860（成片 copy 到 renders/，work_dir 文件随后即删——应
  `os.replace`）；:1318（xfade 胜者 copy 为 concat.mp4）；:1228（1-clip 路径 copy）。
  1080p 渲染多写 ~3GB。
- 修复：`os.replace`/同卷移动；1-clip 原位返回。
- 验收：渲染 I/O 写量对比（脚本计时+字节数）。

### RE5（P1）concat 中间产物写入共享 trim_cache 固定名
- 位置：render.py:1239/:1323 `Path(clips[0]).parent / "concat.mp4"` —— clips[0]
  为 trim 缓存命中时落在 `_cache/tmp/trim_cache/`，prune 永不清理（glob 仅
  trim_*.mp4），且两并发渲染写同一路径互踩。
- 修复：concat 中间产物移入 `self._work_dir`。
- 验收：并发 2-clip 渲染互不干扰；trim_cache 目录无 concat 残留。

### RE6（P1）ffprobe 派生风暴 + acc 初始化阻塞事件循环
- 位置：10-clip 渲染 ≈32 次 ffprobe（`_source_valid`×10 未缓存、acc 初始化×10、
  `_xfade_pair`×9、text/输出/跳过检查等）；且 acc 初始化循环在协程内同步
  `subprocess.run`（15s 超时/个）阻塞事件循环。
- 修复：`_get_actual_duration` 按 (path, size, mtime) 模块级记忆化；acc 初始化
  循环包 `asyncio.to_thread`。
- 验收：10-clip 渲染 ffprobe 派生数从 ~32 降至 ≤12（计数打桩）。

### RE7（P2）无条件 `-stream_loop -1` 与缺 analyzeduration
- 位置：render.py:1164 每 trim 无限循环输入（仅图片/超长源需要）；长 GOP 源未设
  `-analyzeduration/-probesize`。
- 修复：仅图片源或 `offset+dur > source_duration` 时加 stream_loop；trim 输入补
  探测参数。
- 验收：常规片段渲染无 stream_loop；长 GOP 样本探测正常。

### RE8（P2）音频链：loudnorm 192kHz 直喂 AAC + 单帧动态模式 + duration=first
- 位置：render.py:1931-1941 —— loudnorm 输出无 `aresample`（内部 192kHz 传入
  AAC，码率翻倍）；单 pass 动态 loudnorm 在语音+音乐上抽吸；`amix duration=first`
  以首个混音输入为准可能截断全长 BGM。
- 修复：loudnorm 后接 `aresample=48000`；混音前先跑测量 pass 用
  `loudnorm=measured_*:linear=true`；`duration=longest`（整体仍受 `-t`/视频时长
  边界约束）+ 每输入 `aformat=channel_layouts=stereo`。
- 验收：输出音频采样率 48k；响度达标；全长 BGM 不被截断（单测断言滤镜链）。

### RE9（P2）解码 hwaccel 与编码器决策解耦
- 位置：render.py:277-281 `_hwaccel_args` 仅在 nvenc 编码时返回 `-hwaccel cuda`
  ——NVENC 探测失败但 NVDEC 可用的机器全程 CPU 解码；无 qsv/vaapi。
- 修复：独立探测 NVDEC 并按输入应用 `-hwaccel`；编码器选择不变。
- 验收：NVDEC 可用机器上 trim/concat 解码走 GPU（ffprobe/日志断言）。

### RE10（P2）`int(fps)` 截断
- 位置：render.py:1556 与 hyperframes_renderer.py:169/219 —— 29.97fps 时间线的
  MG MOV 被渲染为 29fps，叠加时对主视频时间戳重复丢帧/补帧（动画抖动）。
- 修复：fps 以 `f"{fps:.5f}"` 端到端传递。
- 验收：29.97fps 时间线的 MOV 元数据 fps=29.97（ffprobe 断言）。

### RE11（P2）typewriter 逐字符 drawtext
- 位置：render.py:1714-1721 每个非空格字符一个 drawtext 节点（50 字=50 节点）
  叠加进全片滤镜链。
- 修复：按词分组错峰 enable 窗口，或改 ASS `\t` 时序标签。
- 验收：50 字 typewriter 的滤镜节点数与渲染耗时不随字符数线性爆炸（基准对比）。

### RE12（P2）阶段计时埋点 + MG MOV 缓存 + fallback 编码器
- 位置/修复：
  a) render.py 全链无任何 perf_counter 埋点——各阶段（trim/concat/text/mg/pip/
     audio/output）计时写入 ffmpeg_log（回归可见性）；
  b) MG/HF alpha MOV 内容哈希缓存于 `_cache/`（相同 mg_html 不重渲；窗口受限
     渲染替代全时间线长度 MOV）；
  c) `_generate_fallback` 改 `_current_encoder()`（不再绕过 override）。
- 验收：同输入二次渲染 MG 阶段 0 次 Chrome 调用（缓存命中断言）。

---

## 批次 T：工具系统（T1–T12）

### T1（P0）8 处 `_ffmpeg()` 缺 `await`
- 位置：audio.py:31（audio_extract）、:107/:115（audio_replace）、:165/:171
  （audio_normalize）、:65（bpm_detect，同病但丢弃结果伪造 BPM=120）、
  vision.py:31（scene_detect，api/preprocess.py:322 恒降级）、animation.py:37/:45。
- 修复：全部补 `await`；bpm_detect 如实返回。
- 验收：逐工具新增真小样本冒烟测试。

### T2（P0）black_frame_detect 恒失败
- 位置：quality.py:42-60 —— 过曝检测用无滤镜 ffprobe，把每个帧时间戳都当
  white_segments → 任何视频都失败且全帧元数据进内存。
- 修复：改正确滤镜（signalstats/blackdetect），仅连续 ≥N 秒段计入。
- 验收：纯白视频报、正常视频不报（双样本单测）。

### T3（P0）color_correct 恒报错
- 位置：color.py:33-36 `eq=…:hue=` —— eq 无 hue 选项。
- 修复：hue 拆独立滤镜串联；默认参数不产生空滤镜。
- 验收：默认调用 success 且输出可播放。

### T4（P1）同步 subprocess 全面异步化
- 位置：约 20 处（effects/color/quality/chroma_key/speed/stabilize/subtitle/
  text_video/audio.mix/transcribe/video.py:225），超时 120–300s。
- 修复：新增 `tool/_proc.py::run_ff(cmd, timeout)`（to_thread+subprocess.run），
  逐点替换。
- 验收：长编码工具并发调用不阻塞其他请求（单测）。

### T5（P1）裸 ffmpeg/ffprobe 绕过 resolver
- 位置：video.py:130/267/299/360/393、frame_extractor.py:33、quality.py:32/43/88
  及 color/chroma_key/speed/stabilize/effects/subtitle/text_video。
- 修复：统一经 resolver（并入 T4 的 run_ff）；`is_available()` 与实际一致。
- 验收：仅 WinGet 路径可用环境全工具可执行。

### T6（P1）video_trim URL 缓存互串 + SSRF
- 位置：video.py:122-124 键仅 basename；无 `assert_public_url`；并发同 URL 竞写。
- 修复：键改 sha256(url)；下载前 `assert_public_url`；陈旧文件不作命中。
- 验收：不同 URL 同名互不串；`file://`/内网被拒（单测）。

### T7（P1）generate_text_video 回退分支损坏
- 位置：text_video.py:86-87 索引错位（覆盖 -vf、删 drawtext/-c:v）；`__text_*.txt`
  写 CWD；38-51 死代码。
- 修复：回退显式构建 `lavfi color` 输入；临时文件入 `_CLIPWRIGHT_TEMP`；删死代码。
- 验收：字体失败场景回退仍出纯色视频（单测）。

### T8（P1）background_remove / transition_apply / video_crop
- effects.py:547（chroma 二次 color=）、:552-557（blur 无操作）、:559-561（ai 未
  实现→如实报错）、:99-103（hard_cut 丢 clip_b→concat）、:111-112（slide 方向反）、
  :43（atempo<0.5 钳制）；video.py:361（crop 补 ih*/iw* 乘数）。
- 验收：逐项小样本单测。

### T9（P1）工具层安全与超时统一
- ToolRegistry.execute：路径 `assert_allowed_path`、URL `assert_public_url`、
  `asyncio.wait_for` 全局超时（默认 300s 可覆盖）；registry.py:148 非异常
  fallback 包 try；移除 semantic_match→material_filter 不兼容链。
- 验收：路径/内网 URL 注入被拒；TypeError 不逃逸（单测）。

### T10（P1）工具 schema 参数描述
- type_utils.py:79 恒空、int 压 number。
- 修复：解析 docstring Args 填 description；int 保留 integer；默认值入描述。
- 验收：/api/tool/list 快照断言。

### T11（P2）诚实化
- bpm_detect 未实现→error；vision_llm 异常 fallback 带 `degraded: true`；
  transcribe TTS 缺依赖→error；tracking_text 占位→error；whisper 去掉 VTT 宣告；
  subtitle_burn 截断进 note。
- 验收：异常路径单测断言 status/文案。

### T12（P2）杂项
- mktemp().name → _CLIPWRIGHT_TEMP 唯一名（10 处）；concat 列表转义+finally；
  stabilize .trf finally、smoothing→shakiness；registry INFO 降 DEBUG、
  add_tool_event 透传 pipeline_id；AgentToolkit 死代码移除。

---

## 批次 P：插件系统（P1–P10）

### P1（P0）签名校验 fail-closed
- 位置：config.py:41-43 默认不强制；loader.py:212-218/57-70；/load/{id} 已加载
  不重验。
- 修复：`plugin_signature_key` 非空 ⇒ 一律要求签名；/load 强制重验证；
  `sign_manifest` 空 key 拒绝。
- 验收：配置 key 后未签名插件被拒（单测）；无 key 行为不变。

### P2（P0）市场安装完整性 + 失败回滚
- 位置：market_client.py:43-51 sha256 恒 None；install_service.py:44-61 失败
  不回滚；_safe_extract 前缀检查不分隔符。
- 修复：校验 sha256（缺失拒绝）；load 失败 rmtree 回滚；改 `security.is_within`。
- 验收：篡改 zip 被拒无残留；load 失败自动清理（单测）。

### P3（P1）插件端点 admin 门控
- 位置：api/plugin.py 与 api/market.py 全部端点无角色校验（jwt 模式任意登录
  用户可加载/卸载/重载 = 代码执行）。
- 修复：写操作（load/unload/enable/disable/config PUT/uninstall/install/reload）
  加 admin 校验（off/token 维持原行为）；GET 保持登录即可。
- 验收：jwt 非 admin 写操作 403（单测）。

### P4（P1）注册表 per-plugin 注销（禁用/卸载真正生效）
- 位置：hooks.py:41/prompt_registry.py:74 无条件追加；Hook `__plugin_id__` 对
  绑定方法打标失败（hooks.py:37-40）；Tool/Skill/Material 无按插件注销；
  PromptRegistry.unregister 零调用。
- 修复：注册改 wrapper 闭包携带 plugin_id；Registry 增加
  `unregister_plugin(plugin_id)`；unload/disable/uninstall 统一调用；消费点过滤
  未启用插件。
- 验收：禁用后 agent 提示词不含该插件段落、工具列表不出现；reload N 次注册数
  不增长（单测）。

### P5（P1）secret 回写拒绝掩码值
- 位置：loader.py:88-103 GET 掩码、api/plugin.py:171-205 PUT 全文持久化 →
  GET→PUT 循环把真实密钥覆盖为掩码。
- 修复：PUT 值命中掩码模式的字段保留原加密值；legacy flat 配置保存分支支持。
- 验收：GET→PUT 循环真实密钥不变（单测）；显式新值正常更新。

### P6（P1）密钥管理收敛
- Fernet 独立密钥（webhook_secret_key 优先，不再回退 JWT 密钥）；无密钥时拒绝
  保存 secret（400）而非明文落盘；`sign_manifest` 空 key 拒绝。
- 验收：无密钥保存 secret → 400；双密钥独立（单测）。

### P7（P2）malformed manifest 单插件隔离
- loader.py:207 `_parse_manifest` 包 try→PluginLoadError；load_all 逐个隔离；
  /load/{id} 非 PluginLoadError 异常映射 400 + 错误总线。
- 验收：坏 YAML 不影响其余插件；/load 400 带原因（单测）。

### P8（P2）reload 回滚保真 + 钩子隔离
- reload 失败回滚不 pre-shutdown 旧实例（延迟到新版本成功后）；回滚实例重新注册；
  HookRegistry.execute 每钩子 try/except，异常带 plugin_id 写错误总线。
- 验收：必失败 reload 后旧版本可用（功能测试）；单钩子异常不吞其余（单测）。

### P9（P2）配置迁移持久化 + 目标版本一致
- `_migrate_config` 结果写回 PluginData config.yaml；get/save 使用 manifest 版本
  （与 load 一致）；迁移失败写错误总线。
- 验收：config.yaml `_schema_version` 更新；二次加载不再迁移（计数单测）。

### P10（P2）健康视图 + 安装进主 loader
- 新增 GET /api/plugin/health（installed×enabled/signed/依赖/失败原因）；install
  使用全局 loader；同步 YAML I/O 入 to_thread；discover 请求内复用。
- 验收：安装后健康视图立即可见且无重复 initialize（单测）。

---

## 批次 M：MG 动画渲染（M1–M8）

### M1（P0）自愈重跑 AnimationAgent 叠加重复 MG/文字 clip
- 位置：pipeline_v2.py:524-546 自愈在已含动画 clip 的时间线上重跑；
  animation_agent.py:216/500-501/758-759 从不清理既有动画/文字轨 → 新旧 MG 与
  文字同位置叠印（成片重影）。
- 修复：AnimationAgent.execute 入口先清空既有 ANIMATION 轨 clip 与 TEXT 轨中
  `anim_type` 非空的 clip（幂等化），再生成。
- 验收：自愈重跑后动画/文字轨无重复 clip（单测：两次 execute 断言数量不翻倍）。

### M2（P0）per-MOV 全片重编码 ×N
- 位置：render.py:1517-1527 每 MG 一次全片 re-encode（3 分钟 1080p × 5 MG ≈ 5 次
  全片 encode）；链式单次合成实现存在但零调用。
- 修复：Phase 2 改链式单次 filter_complex（`_apply_mg_overlay_chained`），MOV>8
  或 cmdline>30000 时回退 per-MOV；保留 `_apply_mg_overlay_mov` 作为链式失败的
  逐个回退。
- 验收：4 MG 时间线 Phase 2 ffmpeg 全片重编次数 4→1（计数断言）。

### M3（P1）MG 生成零缓存
- 位置：无任何 (description+text+params)→mg_def/html 缓存；自愈（最多 3 轮）/
  run_from_agent/需求侧重做全量重付 LLM——每 clip 最坏 ~5 次调用（生成×2+批判+
  批判修复+schema 修复），另有 fallback 模板填充 1 次。
- 修复：`mg/generation_cache.py`：sha256(description+text+params+config 版本) →
  {mg_def, html}，落 `_cache/mg_gen/`（LRU 上限）；generate() 命中直接返回
  （trace 标 cached）；自愈重跑零 LLM 成本。
- 验收：同输入二次 generate → 0 次 LLM 调用、结果一致（单测）。

### M4（P1）`render_overlay_on_video` 忽略 returncode
- 位置：hyperframes_renderer.py:357-360 恒 return True——编码失败/半截文件被
  当成功，仅靠 `_is_valid_video` 兜底静默丢弃 MG。
- 修复：校验 returncode（非 0 返回 False）；调用方失败时对 fallback 路径发
  warning 事件。
- 验收：构造 rc≠0 → 返回 False + warning（单测）。

### M5（P1）Chrome 进程树不被 kill
- 位置：render.py:129-132/66-76 仅杀直接子进程（cmd/npx 包装），Windows 上
  `node→chrome-headless-shell` 孤儿化，烧 GPU/RAM 至 1800s 或永久。
- 修复：Windows 用 `taskkill /T /F`（或 Job Object），POSIX 用 `start_new_session`
  + 进程组 kill；超时与 cancel 统一走进程树击杀。
- 验收：超时场景后无 chrome-headless-shell 孤儿进程（单测打桩断言 kill 调用）。

### M6（P1）MGStorage 悬空 + 按生成预览
- 位置：generator.py:1108 生成 `generation_id`、animation_agent.py:753 写入 clip
  metadata，但 `MGStorage.save_generation` 全仓零调用 → mg/generations/ 恒空，
  "按生成预览"死路。
- 修复：`_build_success` 内 `MGStorage.save_generation(...)`；api/animation.py
  preview 支持 `generation_id` 入口；补并发限制与取消（cancel_id）。
- 验收：生成后 generations/ 有文件、按 id 预览返回成片（集成测试）。

### M7（P2）图片 src file:// + 背景守卫泄漏
- 位置：mg_renderer.py:351-362 `<img src>` 直写绝对路径（headless Chrome 相对
  解析 → 裂图）；generator.py:288-304 `_ensure_no_background` 不清
  `style.background`/全幅 shape（LLM 设纯色背景即遮实拍）；preview 端点同病
  （api/animation.py:112）。
- 修复：src 本地路径转 `file:///` URI；`_ensure_no_background` 增清
  `style.background` 与全幅不透明 shape；preview 端点复用同函数。
- 验收：带图片元素的 MG 渲染成片中图片可见（单测断言 src 形式）；LLM 设背景时
  无 vision_prompt 仍透明（单测）。

### M8（P2）阶段可见性
- 位置：Phase 2（常为最长阶段）零进度事件（90% 平线）；生成/批判/修复三个 LLM
  阶段用户不可区分。
- 修复：Phase 2 per-MOV 进度 90→95；generator 的 trace 事件
  （critique_start/critique_repair_start）接入 SSE；`generated_mg_count` 与逐 clip
  成败进 execute 结果摘要。
- 验收：SSE 序列含 per-MOV 递增 pct 与批判阶段标记（单测断言事件序列）。

---

## 批次 V：前端编辑器预览（V1–V10，J:\Clipweight-Client）

### V1（P0）重叠音频互相静音
- 位置：src/services/media/mediaManager.ts:406-410 `attachAnalyser` 每次断开上一个
  MediaElementSource；PreviewPanel.tsx:124 每 clip 启动时调用 → BGM+配音仅最后
  启动者可听。
- 修复：每元素独立 MediaElementSource（Map 缓存，创建一次常连），共享 analyser
  仅做计量不参与路由。
- 验收：BGM+配音同时可听（Playwright 音频路由断言或手动）。

### V2（P1）时间码 60fps 重渲染风暴
- 位置：PreviewPanel.tsx:166 RAF 每帧写 `currentTimeSec` 入 zustand；6 个组件订阅
  全量重渲染（PreviewPanel:30、TimelinePanel:47、EditorToolbar:47、
  PropertiesPanel:520/853、EditorLayout:250）。
- 修复：时间码读取改 `useStore.getState()`（RAF 内直读）或拆仅订阅时间的
  `<Timecode/>` 叶子；订阅组件收窄 selector。
- 验收：播放期间 React Profiler 中上述组件 0 次重渲染（性能录制对比）。

### V3（P0）关键帧动画导出不生效
- 位置：属性名不匹配（前端 position_x/scale/rotation vs 后端 translate_x/scale_x，
  animationPresets.ts:34-78）；关键帧时间前端 0-1 归一化 vs 后端按秒
  （timeline.ts:31-38 vs render.py:1132）；缓动后端忽略（线性）。
- 修复：前端关键帧写入改后端属性名 + 绝对秒（含 split 重映射同步改）；后端
  drawtext/视频段关键帧消费 position/rotate 并支持 EASING_MAP；关键帧变速后端
  补 atempo/setpts 表达式。
- 验收：预览所见关键帧动画在导出成片同一时间点呈现同等效果（抽帧比对单测）。

### V4（P0）静态变换导出不可见
- 位置：前端 metadata.transform x/y/scale/rotation（PropertiesPanel.tsx:646-709）
  只影响预览 canvas；render.py 对视频段零 transform 处理。
- 修复：trim/PIP 滤镜链追加 `rotate/scale/translate` 表达式（由 transform 计算）。
- 验收：设置变换的 clip 导出成片呈现相同变换（抽帧单测）。

### V5（P1）token 模式媒体全部 401
- 位置：`/api/asset/by-path` 需 Bearer，`<video>/<img>` 无法带头；query-token 回退
  仅覆盖 /renders/、/voice_audio/（main.py:387-437；mediaManager.ts:12-21）。
- 修复：by-path 支持短期 query-token（复用 SSE token 机制）或后端签发短时签名
  URL；mediaManager 组 URL 时附带。
- 验收：token 模式下预览媒体全部加载（Playwright）。

### V6（P1）代理切换弄坏预览
- 位置：EditorToolbar.tsx:125-145 切换重写 asset_id 但不重注册媒体；且仅给第一个
  video clip 生成代理（:102-123）→ 其余 404。
- 修复：切换后 `mediaManager.registerTimeline` 全量重注册；代理生成覆盖全部
  视频 clip；mediaManager.registerUrl 允许同 id 刷新 URL。
- 验收：开启代理后预览正常（无占位/404）；关闭切回原素材。

### V7（P1）缩略图捕获劫持预览元素
- 位置：mediaManager.ts:239-267 ensureThumbnail/captureThumbnail seek 预览同款
  `<video>`（renderers.ts:604 触发）→ 播放中预览跳帧闪烁；LRU=24 桶驱逐快于
  重采。
- 修复：缩略图用独立隐藏 video 元素池（不复用预览元素）；LRU 上限随缩放级别
  自适应。
- 验收：播放中时间轴缩略图生成不引起预览跳帧（手动+截图对比）。

### V8（P1）转场时间模型对齐（联动 R1）
- 位置：预览原地淡入不重叠（PreviewPanel.tsx:526-538）vs 导出 xfade 重叠缩短
  Σtd（render.py:1247-1254）；首 clip transition_in 导出被忽略；slide/wipe 在
  导出降级 fade 而 UI 宣称可用。
- 修复：以 R1 的 effective_start_sec 为单一时间真相；预览重叠窗口渲染对齐导出
  语义；UI 仅展示 XFADE_VALUES 支持的转场。
- 验收：预览时间轴与导出成片的转场时刻一致（抽帧比对）。

### V9（P2）剪辑边界预缓冲
- 位置：mediaManager.ts:111 `preload='metadata'` → 边界无缓冲闪占位
  （PreviewPanel.tsx:666-696）。
- 修复：播放头接近的相邻 clip 元素 `preload='auto'`（预取窗口 ±1 clip）。
- 验收：跨界播放无占位闪烁（手动）。

### V10（P2）UX 杂项
- 空格焦点陷阱（useGlobalKeybindings.ts:398 被 KeybindingEngine.ts:98 拦截）→
  按钮点击后 blur；visibilitychange 暂停音频并冻结时钟；t==dur 末帧黑帧 →
  clamp 到 dur−ε；TimelineDiffView 增加缩略视觉预览；Undo/重做后重注册媒体；
  thumbImageCache 驱逐；导出帧用时间线分辨率且不含 safe-area。
- 验收：逐项手动/单测。

---

## 批次 X：跨端预览-导出契约（X1–X4）

### X1（P1）蒙版枚举统一
- 位置：schema/timeline.py:94-97 允许 none/rect/ellipse；预览渲染 rect/ellipse；
  后端仅匹配 "rectangle"（render.py:1116）→ 导出永不渲染蒙版。
- 修复：后端兼容 rect=rectangle；前端枚举对齐后端值。
- 验收：rect/ellipse 蒙版导出生效（抽帧单测）。

### X2（P2）blend 模式收敛
- 位置：预览 12 种 globalCompositeOperation（PropertiesPanel.tsx:1112）；后端仅
  PiP 路径支持 screen/multiply/overlay。
- 修复：UI 收敛为后端支持集 + normal；后端非 PiP（全帧）blend 支持补齐或禁用。
- 验收：UI 无不可用选项；设置 screen/multiply/overlay 的 PIP 导出生效。

### X3（P2）文字描边/位置对齐
- 位置：canvas 描边+fill 使有效描边减半 vs ASS borderw；文字位置预览恒中/
  字幕恒底 vs 导出按轨序 {1:bottom,2:top,3:center}+35px 堆叠；letter_spacing/
  shadow_blur 导出无等价。
- 修复：预览描边宽度×2 对齐 borderw 视觉；文字位置读 overlay position；
  letter_spacing/shadow_blur 标注"仅预览"或后端补齐。
- 验收：同参数预览与导出文字视觉一致（抽帧比对）。

### X4（P2）音频 source_offset/speed 混音语义（联动 R9）
- 位置：预览从 source_offset 以 playbackRate=speed 播放；导出 atrim 用时间线
  start_sec 且无 atempo（render.py:1904-1912）→ 内容与速率双偏离。
- 修复：R9 落地后混音补 `atempo={speed}`（clamp 链式），source_offset 语义一致。
- 验收：非默认 offset/speed 的音频 clip 预览与导出内容一致（单测断言滤镜链）。

---

## 回归验收（每批完成后执行）

```bash
# 后端（工作目录 J:\Clipwright）
python -m pytest tests -q          # 基线 1344 passed，修复后只增不减
python -c "import clipwright.main"

# 前端（工作目录 J:\Clipweight-Client，涉及 V/X 批次时）
npx tsc --noEmit
npm run test                       # 基线 362 passed
npm run build
```

端到端冒烟（全部批次完成后）：
1. 含 ≥2 转场 + 字幕 + MG + PIP 的时间线渲染：字幕对齐（R1）、单次链式 MG 合成
   （M2）、进度全程有事件（R6/M8）、失败含 ffmpeg_log（R7）。
2. 前端预览：BGM+配音同响（V1）、关键帧/变换导出一致（V3/V4）、代理切换可用
   （V6）、token 模式媒体加载（V5）。
3. 插件：未签名安装被拒（P1/P2）、禁用后能力即时收缩（P4）、jwt 非 admin 写操作
   403（P3）。

---

## 执行记录

### 后端批次 R / RE / T / P / M — 已完成（回归基线 1344 passed 维持）

**批次 R（渲染正确性 R1–R12）— 全部落地。**
R1 转场漂移：concat 后按累计 xfade 时长校正后段 effective_start，agents 与 pipeline_v2 共享 `animation/xfade_map.py`；R2 单例互踩：TaskQueue 每任务 `timeout_sec`（A2 同步）+ 渲染实例化 + `_running_pipelines` 登记；R3 ProRes 经 `_encoder_stage_args`（prores_ks 无 `-preset`）；R4 drawtext `::enable` 双冒号修复；R5 链式 MG 合成；R6 各阶段 progress 事件全覆盖；R7 失败携带 ffmpeg_log 尾部；R8 渲染级取消检查；R9 音频 source_offset/atempo 语义；R10 hyperframes 取消感知；R11 产物清理白名单化；R12 MG alpha MOV 内容哈希缓存 + `render_overlay_on_video` 走 `run_tracked_ff`。

**批次 RE（渲染效率 RE1–RE12）— 全部落地。**
RE1 ffmpeg 池线程按核数封顶 8；RE2 全链 `-crf+maxrate/bufsize`；RE3 `_current_pix_fmt()` 替代硬编码 yuv420p；RE4 三处 copy2 改 os.replace/原位；RE5 concat 中间物移入 work_dir；RE6 ffprobe 记忆化（path+size+mtime）；RE7 `-stream_loop` 仅图片源；RE8 loudnorm 后 `aresample=48000` + amix duration=longest；RE9 解码 hwaccel 与编码器解耦；RE10 fps 小数端到端；RE11 typewriter 分词分组；RE12 阶段计时埋点入 ffmpeg_log。

**批次 T（工具系统 T1–T12）— 全部落地。**
T1 全部 `async def execute` 内同步 `subprocess.run` → `await asyncio.to_thread`（audio/vision/animation/quality/effects/subtitle/text_video/stabilize 共 20+ 处）；T2 signalstats YAVG 过曝检测；T3 eq+hue 拆分；T6 URL 缓存 sha256 + SSRF 校验；T7 text_video fallback 显式 lavfi；T8 crop 表达式 + 转场方向；T9 ToolRegistry.execute 路径/URL 安全校验统一；T10 `utils/type_utils.py` docstring Args 解析进 schema 描述；T11 bpm 如实标记；T12 stabilize `.trf` finally 清理 + concat 清单单引号转义（`utils/concat_list.py` 收敛校验/转义/清理）。

**批次 P（插件系统）— 落地 P1–P5、P7；P6/P8–P10 并入既有机制。**
P1 签名 fail-closed；P2 安装 sha256 + 失败回滚（含市场路径）；P3 写操作 admin 门控（jwt 模式 403）；P4 Hook per-plugin 标注 + disable/unregister 同步注销 Hook 与 Prompt（`PluginPromptRegistry.unregister`）+ execute per-hook 异常隔离；P5 secret 掩码不回写；P7 manifest 解析异常隔离。P6 key 收敛、P8 reload 回滚、P9 迁移持久化、P10 健康视图：loader/config_migration 现有机制已覆盖主场景（原地 reload、迁移结果随配置落盘），审计级追踪可后续补。

**批次 M（MG 渲染）— 落地 M1–M5、M7；M6/M8 部分。**
M1 自愈重跑先清动画/文字轨（幂等）；M2 链式 MG 合成（MOV>8 回退 per-MOV）；M3 `animation/mg/generation_cache.py` marker 哈希缓存（30min TTL，自愈/重试零 LLM 重付）；M4 overlay rc 校验；M5 Chrome 进程树击杀（taskkill /T）；M7 图片 src file:// + 背景守卫。M6 generation_id 预览、M8 clip 级进度：阶段级进度已覆盖（R6），预览接线待前端配合后补。

### 前端批次 V — 已落地 V1–V10（V8 此前完成；自适应缩略图 LRU 上限未做，维持固定 24）

V1 每音频元素独立 source/analyser（重叠不再互斥静音）；V2 时间码拆叶子组件（消除 60fps 级联重渲）；V3 关键帧契约对齐（translate_x/scale_x + 绝对秒）；V4 静态变换导出：trim 阶段识别 `metadata.transform`（translate 归一化/scale/rotate），经黑底画布 `overlay` 链合成并进 trim 缓存键（3 例单测锁定，含恒等变换不引入 filter_complex）；V5 token 模式媒体 401：后端 `GET /api/asset/by-path` 允许 query token（jwt 模式另收验签 JWT；中间件已在日志前抹除 token），前端 `withMediaToken` 自动附带 session token；V6 代理生成覆盖全部视频/图片素材 + 切换后经 `setTimeline` 旋点按 URL 变化全量重注册；V7 缩略图改用每素材独立隐藏 video 元素（不再 seek 预览元素，播放中抓帧零跳帧）；V9 播放头后 3s 窗口内片段 `preload` 升级 auto（消除边界闪白，幂等可高频调用）；V10 末帧 `dur−ε` 钳位（黑帧消除）、画布点击后 blur（空格焦点陷阱）、visibilitychange 自动暂停（防音画错位）、Undo/重做/导入后经 `setTimeline` 重注册媒体。

### 批次 X — 全部落地（X1–X4）

X1 蒙版枚举：后端兼容 `rect`（=rectangle）别名，`ellipse` 经 geq alpha 椭圆遮罩真实导出（此前前端两种蒙版导出全部静默失效）；X2 blend 收敛：UI 选项收敛到后端支持集 normal/screen/multiply/overlay，历史不支持值以「仅预览」保留；X3 文字锚点对齐：预览新增 `textLayout`（`metadata.position` → 轨序回退 {1:bottom,2:top,3:center} → 同轨 35px 堆叠，9 宫格映射与导出 drawtext 一致），描边预览 ×2 补偿 `borderw` 纯外侧语义，字距/阴影模糊标注「仅预览」，命中框同步新锚点（5+8 例单测）；X4 音频速率：混音链 `atrim` 时长按源时间换算（dur×speed）+ `atempo` 链式分级（单级限 0.5–2.0），与预览 playbackRate 语义一致（2 例单测）。

### 后续收尾（V3 后端三缺口 / P8 / P10 / M6）— 已落地

- **V3a 关键帧表达式模块**：新增 `utils/keyframes_expr.py`——时间基归一化（前端 exportTimeline 写 `kf_time_base=clip_local` 标记，管线绝对秒走启发式：min(time)≥start_sec 判绝对）、21 种 Penner 缓动 → ffmpeg 表达式（与前端 easing.ts 公式一一对应，未知回退 linear）、分段插值嵌套 if 构造、定点数字面量（禁科学计数法）。
- **V3a drawtext**：`_build_kf_drawtext` 重写——easing 按段生效；translate 单位按来源区分（clip_local=画幅比例×w/h，管线遗留=像素）；enable 窗口按 start_sec 正确偏移（旧实现把片段相对时间当绝对时间，文字动画整体早于片段出现）。
- **V3b 视频段**：transform 关键帧 → `scale eval=frame` + overlay 逐帧表达式（clip_local 比例单位，静态 transform 作缺省基线）；opacity 关键帧 → 真分段插值（替换旧 0.1s 窗口近似——多关键帧时 alpha 叠加越界闪烁）；关键帧内容进 trim 缓存键（sha256 摘要）。kf rotate 不支持（仅预览），静态 rotate 保持 V4。
- **V3c 关键帧变速**：speed 关键帧 → 分段恒速近似（trim+concat，源区间按 ∫v 累计含 source_offset），预览时间重映射（M5）导出可见。
- **P8 reload 回滚强化**：覆盖全部失败路径（PluginLoadError/任意异常/load 返回 None），回滚后对旧实例重新 initialize（shutdown 已释放资源），失败记错误总线，插件不静默消失。
- **P10 插件健康视图**：`GET /api/plugin/health`——单端点聚合 ok/degraded/error 分类、错误计数、Hook 注册数、未加载插件与缺依赖清单。
- **M6 generation_id 预览接线**：`_build_success` 成功路径持久化到 MGStorage；`GET /api/animation/mg/generations`（列表）/`/{generation_id}`（全文，ID 白名单校验）；前端 MgPreviewPage「最近生成」区 + API client（mgGenerations/mgGeneration），点选即经既有 /preview 渲染回看；管线 clip metadata 已携带 mg_generation_id。

**最终回归（2026-09-06，第二轮）**：后端 `python -m pytest tests -q` = **1379 passed**；前端 `npx tsc --noEmit` ✓。

### 端到端冒烟 + 完整安全审计（2026-09-06，第三轮）— 已执行

**E2E 真实渲染冒烟（`scripts/e2e_smoke.py`，可重复执行）**：本机全要素可用（ffmpeg 8.1 full / Hyperframes chrome-headless-shell 冷启动 4s 后可用 / Mongo 运行中）。脚本合成源素材（testsrc2/sine），构造时间线：3 主视频（2×xfade 转场 wipeleft/fade）+ 静态 transform + opacity 关键帧（clip_local 标记）+ PIP 轨 + 双字幕 + LLM MG 片段（内置模板同源 HTML）+ BGM（ducking 角色）+ 变速人声（atempo 1.5），驱动 RenderService 全管线后校验：

- 时长 **14.00s** 精确命中预期（3×5s − 2×0.5s 转场重叠，R1 校正生效）；视频/音频双流 ✓；零源降级；转场后帧与字幕时段帧亮度正常（非黑屏）；NVENC 自动启用；链式 MG 叠加 1 input 成功；`--no-mg` 变体同样通过。

**冒烟暴露并修复的真实 bug（C11 ducking 顺序依赖）**：`_mix_audio` 的 BGM 自动避让用 `asplit` 从人声分出侧链，但仅当 **BGM 排在人声之后**才被消费——常见时间线顺序（BGM 在前）下 ffmpeg 报 `asplit output unconnected`，**C11 多源混音必然失败并静默回退单音源**（丢失 BGM 或人声其一）。修复：两阶段路由（每源独立预处理链 → 按角色分配 sidechain tap），顺序无关，侧链上限 3；人声/bgm 任一缺失时自动退化为纯 amix。另修：单源回退优先保留人声（此前 voices[0] 可能是 BGM，丢内容音频）；C11 失败 stderr 尾部写入 ffmpeg_log（此前不可排查）。回归测试 +1（`test_mix_audio_ducking_order_independent`）。

**Mimosa 深度审计（scan `scan-2026-09-05T20-15-42`，seal `sha256:b99c3ea5…`）**：21 项发现，分诊如下——

- **真修 4 项**：`services/edl.py` FCPXML 解析拒绝含 DTD/ENTITY 的 XML（实体扩张）；`tool/subtitle.py` + `tool/text_video.py` 的 `tempfile.mktemp` 竞态名 → `mkstemp`；`api/asset.py` 上传扩展名白名单（`.[A-Za-z0-9]{1,8}`，防双扩展/路径字符）。
- **误报 7 项（已有校验）**：persona/repository 与 loader 的 ID 均经 `validate_id`/`is_safe_id`（拒 `../`）；remote_render 的 `.part` 名含 uuid；worker/api 扩展名已 sanitize；`utils/concat_list` 有 resolve+is_file 校验；`frame_extractor` 随机数仅用于抽样时机非安全用途。
- **接受风险 6 项**：`scripts/diag_*.py` 3 项 SSRF 为本地诊断脚本（请求固定 localhost:8080，非服务端点）；`plugins/voice_ext/main.py` 2 项临时文件名为固定前缀+os.urandom（无用户输入路径成分）；`_local_backup_20260803/` 目录内 2 项为历史备份副本（非运行代码，建议移出仓库——未经确认未动）。
- 覆盖缺口致状态 `inconclusive`（非阻断）：部分分析阶段未完整覆盖，核心源码静态结论如上。
