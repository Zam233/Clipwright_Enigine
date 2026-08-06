# Bug 审计与修复记录（Bugfix Audit）

> 计划：`.omo/plans/clipwright-bugfix-audit.md`（前端仓库工作目录）
> 范围：后端 18 项（B1-B18，含前端配合项）+ 前端 12 项（F1-F12），共 30 项
> 执行日期：2026-08-06 · 提交：双仓分别提交（见文末）

## 后端修复（J:\Clipwright）

### B1 — glitch 转场移除 xfade 映射

- **文件**：`clipwright/tool/effects.py`
- **修复**：`glitch` 从 xfade filter_map 移除，回退到 `crossfade`（fade）
- **测试**：`tests/clipwright/test_effects.py` — 断言 `transition='glitch'` 生成合法 filter（含 `transition=fade`），ffmpeg 实测 returncode 0
- **提交**：`7b46e8e`

### B2 — rect 转场改为 rectcrop

- **文件**：`clipwright/tool/effects.py`
- **修复**：`rect` → `rectcrop`（合法 xfade transition）
- **测试**：`tests/clipwright/test_effects.py` — 断言 filter 含 `transition=rectcrop`，ffmpeg 实测
- **提交**：`7b46e8e`

### B3 — clock 转场改为 circleopen

- **文件**：`clipwright/tool/effects.py`
- **修复**：`clock` → `circleopen`
- **测试**：`tests/clipwright/test_effects.py` — 断言 `transition=circleopen`，ffmpeg 实测
- **提交**：`7b46e8e`

### B5 — motion 运动模糊滤镜合法化

- **文件**：`clipwright/tool/effects.py`
- **修复**：`mblur=planes=15:mask={radius}` → 合法运动模糊 `gblur=sigma={radius}`（非法 blur_type 回退 gaussian）
- **测试**：`tests/clipwright/test_effects.py` — 断言 motion 生成 `gblur` filter 且 ffmpeg 实测 returncode 0
- **提交**：`7b46e8e`

### B6 — sepia 滤镜语法修复

- **文件**：`clipwright/tool/effects.py`
- **修复**：`colorchannelmixer=.393*.769*.189:...` → 合法命名参数语法 `colorchannelmixer=rr=0.393:rg=0.769:rb=0.189:gr=0.349:gg=0.686:gb=0.168:br=0.272:bg=0.534:bb=0.131`
- **测试**：`tests/clipwright/test_effects.py` — 断言 filter 含 `rr=` 命名参数，ffmpeg 实测 sepia/old_film 出片
- **提交**：`7b46e8e`

### B7 — drawtext 标题转义

- **文件**：`clipwright/tool/video.py`
- **修复**：`drawtext=text='{text[:50]}'` 增加转义（与 effects.py L245 一致：`'` → `'\''`、`:` → `\:` + 逗号转义）
- **测试**：`tests/clipwright/test_video_tool.py` — 断言含 `:`/`'` 的标题生成 filter 可被 ffmpeg 解析（"T3: 发布会" 出图）
- **提交**：`7b46e8e`

### B8 — whisper 模型缓存按 model_size 分键

- **文件**：`clipwright/services/stt.py`
- **修复**：`_transcribe_whisper` 模型缓存 `self._models` 改为 `dict[str, model]` 按 `model_size` 分键
- **测试**：`tests/clipwright/test_stt.py`（mock whisper）— 断言两次不同 model_size 调用加载不同模型
- **提交**：`7b46e8e`

### B9 — faster-whisper 模型缓存

- **文件**：`clipwright/services/stt.py`
- **修复**：`_transcribe_faster_whisper` 增加模型缓存（同 whisper 按 size 分键）
- **测试**：`tests/clipwright/test_stt.py` — 断言第二次调用复用缓存对象（mock 计数 == 1）
- **提交**：`7b46e8e`

### B10 — _ensure_audio 提取失败日志升级

- **文件**：`clipwright/services/stt.py`
- **修复**：`_ensure_audio` 提取失败 `logger.debug` → `logger.warning`
- **测试**：`tests/clipwright/test_stt.py` — mock ffmpeg 返回非 0，断言 warning 被记录
- **提交**：`7b46e8e`

### B11 — voice maybe_upload 路径校验

- **文件**：`clipwright/services/voice.py`
- **修复**：`maybe_upload` 增加校验：`local_path` 必须以媒体扩展名结尾（`.wav/.mp3/.m4a/.mp4`）且文件存在，否则抛清晰错误
- **测试**：`tests/clipwright/test_voice_upload_validation.py` — 断言 API URL 抛 FileNotFoundError 带说明
- **提交**：`7b46e8e`

### B12 — 渲染下载链路（后端 + 前端配合）

- **文件**：`clipwright/api/render.py` + `clipwright/security.py`（后端）；`src/pages/ExportPage.tsx` + `src/services/api/render.ts`（前端）
- **修复**：`queue_render` 使用请求 `output_path`（仅取 basename 拼到 `renders/`）；SSE completed 事件增加 `output_path` 字段；新增 `is_safe_download_name`（允许 CJK/unicode，仅拒 `/ \ : * ? " < > |` 与 `..`）；前端 `RenderProgress` 加 `output_path?`，下载链接优先用 SSE `output_path` basename
- **测试**：`tests/clipwright/test_render_download.py`（12 用例）— queue 输出与 download 解析路径一致、路径穿越被拒、CJK 文件名下载 200；前端 `src/services/api/render.test.ts` + `client.test.ts` 覆盖 URL 构建与 401 事件
- **提交**：后端 `78e683c`；前端 `cf28109`

### B13 — diagram_svg dataclass 字段过滤

- **文件**：`clipwright/animation/diagram_svg.py`
- **修复**：`cls(**{**cls.__dict__, ...})` → 只取 dataclass 字段（`{f: getattr(cls, f) for f in cls.__dataclass_fields__}`）
- **测试**：`tests/clipwright/test_diagram_svg_preset.py` — mock 插件注册 style preset + persona 传 style_preset → 不抛 TypeError 且返回 DiagramStyle
- **提交**：`7b46e8e`

### B14 — 相对路径锚定（paths.anchor）

- **文件**：`clipwright/paths.py`（新建）+ `clipwright/api/render.py`、`api/learning.py`、`api/type_maker.py`、`api/template.py`、`api/video_editor.py`、`api/webhook.py`
- **修复**：新增 `anchor(path: str) -> Path` 锚定包父目录；替换全部 `Path("相对路径")`（含 webhook `_WEBHOOKS_FILE`）
- **测试**：`tests/clipwright/test_paths_anchor.py` — monkeypatch cwd 断言各 API 仍指向锚定目录
- **提交**：`7b46e8e`

### B15 — security 媒体根目录锚定

- **文件**：`clipwright/security.py`
- **修复**：`allowed_media_roots` 中 persona_dir/tts_output_dir 用 `anchor()` 而非 `resolve()`
- **测试**：`tests/clipwright/test_render_download.py`（is_safe_download_name 用例）+ 白名单校验任意 CWD 一致
- **提交**：`78e683c`

### B16 — 删除空壳 preprocess worker

- **文件**：`clipwright/services/material_preprocessor.py` + `clipwright/main.py`
- **修复**：删除空壳 `preprocess_worker`（模块仅剩 docstring）；main.py 移除 import 与 spawn_background 调用（真 worker 在 `api/preprocess.py` `_ensure_worker` 惰性启动，日志 "预处理 worker 已启动"）
- **测试**：`tests/clipwright/test_preprocess_cleanup.py`（8 用例）— 断言 main lifespan 不再创建 preprocess 任务（mock spawn_background）、启动日志不再出现 "素材预处理 worker 已启动"、真 worker 拾取任务至终态
- **提交**：`a5f7cf8`

### B17 — preprocess 移除 transcribe 操作

- **文件**：`clipwright/api/preprocess.py`
- **修复**：`SUPPORTED_OPERATIONS` 移除 `"transcribe"`；`_execute_preprocess` 删除 transcribe 分支；`/operations` 描述与文档同步删除
- **测试**：`tests/clipwright/test_preprocess_cleanup.py` — 断言 submit 传 transcribe 返回 400、operations 列表不含 transcribe
- **提交**：`a5f7cf8`

### B18 — stream_chat known limitation 注释

- **文件**：`clipwright/services/requirements_service.py`
- **修复**：`stream_chat` 补注释标记 known limitation（**不改造，文档性改动**）
- **测试**：无
- **提交**：`a5f7cf8`

## 前端修复（J:\Clipweight-Client）

> 全部 12 项于单次提交 `cf28109`

### F1 — undo/redo 深克隆守卫

- **文件**：`src/stores/historyStore.ts`
- **修复**：`undo`/`redo` 的 `structuredClone(current)` 包 try/catch（失败时跳过 push 快照，仅弹栈）
- **测试**：`src/stores/historyStore.test.ts` — 注入不可克隆对象断言 undo 不抛且状态一致

### F2 — 历史合并按 clip 作用域

- **文件**：`src/features/properties/PropertiesPanel.tsx` + `src/features/properties/historyCoalesce.ts`（新建）
- **修复**：`pushHistoryCoalesced` 从模块级全局改为按 clip 作用域（key = clipId）
- **测试**：`src/features/properties/historyCoalesce.test.ts` — 断言连续编辑两个不同 clip 产生两个撤销点

### F3 — pagehide flush 负载大小守卫

- **文件**：`src/pages/EditorPage.tsx` + `src/pages/flushPayload.ts`（新建）
- **修复**：新增 `decideFlushPayload`（阈值 48KB）：>48KB 时退化为保存紧凑元数据 + toast "项目较大，正在保存元数据，请稍候关闭"
- **测试**：`src/pages/EditorPage.test.tsx` — 断言超大 timeline 走降级路径
- **已知历史缺口（未修复）**：`doSave` 随项目保存 `agent_state`，而 `pagehide` 冲刷路径省略 `agent_state` —— 卸载临界期避免序列化过大的 Agent 状态。本次仅保留代码注释，未做改动。

### F4 — 分隔条 pointer 事件兜底

- **文件**：`src/layouts/EditorLayout.tsx`
- **修复**：`mousemove`/`mouseup` 增加 `pointermove`/`pointerup` 兜底（document 级监听 + activeDragRef 防重入，未重构为纯 pointer 方案）
- **测试**：`e2e/panel-divider.spec.ts`（Playwright，hermetic route mock）— dispatch `pointerdown/pointermove/pointerup` 于 `.panel-divider` → 断言 panelWidths 变化 + 纯 mouse 回归

### F5 — 媒体加载失败反馈

- **文件**：`src/services/media/mediaManager.ts`
- **修复**：registerUrl/registerFile 为 video/audio 元素加 `error` 监听 → notify + 标记 entry.error；预览渲染处显示占位
- **测试**：`src/services/media/mediaManager.test.ts` — 模拟元素 error 事件断言 entry 标记

### F6 — 播放速度循环修复

- **文件**：`src/features/preview/PreviewPanel.tsx`
- **修复**：`speeds.indexOf(playbackSpeed)` 不在列时从 0.5 开始
- **测试**：`src/features/preview/PreviewPanel.speed.test.ts` — 断言 playbackSpeed=1.25 时点击 → 0.5

### F7 — 删除失败保留项目

- **文件**：`src/pages/ProjectsPage.tsx`
- **修复**：`handleDelete` catch 分支：删除失败时保留项目 + toast 错误提示（不移除列表项）
- **测试**：`src/pages/ProjectsPage.test.tsx` — 断言失败后项目仍在

### F8 — 连接配置持久化

- **文件**：`src/stores/settingsStore.ts`
- **修复**：`apiBaseUrl`/`wsUrl`/`authToken` 持久化到 localStorage（load 时读取、set 时写入，键 `clipwright.connectionPrefs`，类型校验 + 损坏回退）
- **测试**：`src/stores/settingsStore.test.ts`（5 用例）— 断言 set 后 reload 读取一致、token 置 null、无数据/损坏/非法类型回退

### F9 — Agent 时间线轨道迁移

- **文件**：`src/features/agent/timelineDiff.ts`
- **修复**：`mergeTimeline` modified clip 若 `track_id` 变化则移入新轨道（target track 不存在时创建）
- **测试**：`src/features/agent/timelineDiff.test.ts` — 增加轨道迁移用例

### F10 — 401 全局事件

- **文件**：`src/services/api/client.ts`
- **修复**：401 处理 `console.warn` + `window.dispatchEvent(new CustomEvent('cw:unauthorized'))` 供页面显示登录提示（不实现登录页）
- **测试**：`src/services/api/client.test.ts` — 断言 401 响应触发事件

### F11 — API 地址变更重探测

- **文件**：`src/pages/HomePage.tsx` + `src/pages/useBackendHealth.ts`
- **修复**：订阅 settingsStore 的 `apiBaseUrl` → 变化时重新 `healthApi.check()` 刷新 backend 状态（配合 SettingsPage blur 时 `resetApiClient()`）
- **测试**：`src/pages/useBackendHealth.test.tsx` — 断言改地址后 backend 状态更新（含防无限循环用例）

### F12 — 快捷键 typing-target 守卫

- **文件**：`src/features/keyboard/KeybindingEngine.ts`
- **修复**：`isTypingTarget` 增加 `BUTTON`/`A`（`[contenteditable]` 已由 `isContentEditable` 覆盖）；单键快捷键在 button/a 聚焦时不触发，modifier 组合保留
- **测试**：`src/features/keyboard/KeybindingEngine.test.ts` — 断言 button 聚焦按 `s` 不触发、按 `ctrl+s` 仍触发

## 提交清单

| 仓库 | 提交 | 说明 |
|------|------|------|
| J:\Clipwright | `78e683c` | fix(render): B12 render download chain + B15 anchor media roots |
| J:\Clipwright | `7b46e8e` | fix(tools): B1-6 ffmpeg filter/transition + B7 drawtext escape + B8-10 stt caches + B11 voice validation + B13 diagram preset + B14 paths anchor |
| J:\Clipwright | `a5f7cf8` | chore: B16 remove preprocess stub + B17 drop transcribe op + B18 known-limitation comment + ignore `_local_backup_*/` |
| J:\Clipweight-Client | `cf28109` | fix(editor): F1-F12（见上） |

## 验证基线

- 后端 `python -m pytest tests/ -q`：**759 passed, 0 failed**（提交后复跑）
- 后端 ruff：全库错误 **906（HEAD）→ 899（工作树）**，无新增错误
- 前端 `npx tsc --noEmit`：**0 errors**；`npm run test`：**161 passed / 25 files**；`npm run build`：成功；`npm run lint`：0 errors
- ffmpeg 滤镜实测（B1-6）：`tests/clipwright/test_video_tool.py` + `test_effects.py` 真实调用 ffmpeg 8.1.2，全部通过（非 skip）
- `_local_backup_20260803/` 工作区文件原样保留，仅从 git 索引移除（`git rm -r --cached`）+ `.gitignore` 忽略 `_local_backup_*/`
