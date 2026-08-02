# 帧艺 ClipWright — 远程渲染 Worker 文档

远程渲染把 ffmpeg 渲染任务从主应用（`localhost:8000`）卸载到独立的 Worker 进程（默认 `localhost:8100`）。Worker 复用主应用 `RenderService` 的全部渲染逻辑（零复制），通过 `asset://` 引用共享素材，用 REST + 轮询完成「上传素材 → 提交任务 → 下载产物」的远程闭环。

代码位置：`clipwright/worker/api.py`、`clipwright/worker/render_runner.py`、`clipwright/worker/__main__.py`、`clipwright/worker/store.py`。

---

## 协议

所有端点挂在 `/api/worker` 前缀下，请求/响应均为 JSON（下载除外）。

### 鉴权

| 项 | 说明 |
|----|------|
| 方式 | `Authorization: Bearer <token>`（所有 `/api/worker/*` 请求） |
| 令牌来源 | 优先 `CLIPWRIGHT_WORKER_TOKEN`，未设置则回退 `CLIPWRIGHT_API_TOKEN` |
| 令牌为空 | **开放开发模式**：所有请求直接放行，启动时打印安全警告 |
| 校验 | `hmac.compare_digest` 恒定时间比较；缺失/错误返回 `401` |
| 401 响应 | `{"detail": "未授权：缺少或错误的 Worker 令牌"}` |

> Worker 令牌就是主应用自己的令牌（或独立的 `CLIPWRIGHT_WORKER_TOKEN`）。本地侧 `CLIPWRIGHT_REMOTE_RENDER_TOKEN` 应填同一值。

### 端点一览

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/worker/health` | Worker 存活检查 |
| `POST` | `/api/worker/assets` | 上传渲染素材（multipart，流式写盘 + 去重） |
| `HEAD` | `/api/worker/assets/{hash}` | 素材存在性探测（去重前探） |
| `GET` | `/api/worker/assets/{hash}` | 下载素材（对称/调试用） |
| `POST` | `/api/worker/jobs` | 提交远程渲染任务（立即返回 job_id，202） |
| `GET` | `/api/worker/jobs/{job_id}` | 查询任务状态 / 进度 |
| `GET` | `/api/worker/jobs/{job_id}/download` | 下载已完成的渲染产物 MP4 |

### `GET /api/worker/health`

| 状态 | 响应 |
|------|------|
| `200` | `{"status": "ok"}` |

### `POST /api/worker/assets`

请求体为 `multipart/form-data`：

| 字段 | 类型 | 必填 | 说明 |
|------|------|:----:|------|
| `file` | file | ✓ | 素材文件本体 |
| `hash` | string | | 客户端预计算的 sha1 hex；与服务器计算值不一致返回 409 |

服务器增量计算 sha1，按 `<work_dir>/assets/<sha1[:16]><ext>` 落盘（扩展名取自客户端文件名并净化到 `[A-Za-z0-9.]`）。

| 状态 | 响应 / 说明 |
|------|------|
| `200` | `{"hash": "<完整 sha1 hex>", "stored": true}`（首次写入） |
| `200` | `{"hash": "<完整 sha1 hex>", "stored": false}`（已存在同名素材，去重不重复写盘） |
| `400` | `{"detail": "No file provided"}`（无文件名） |
| `409` | `{"detail": "hash mismatch"}`（表单 `hash` 与服务端计算值不一致） |
| `413` | `{"detail": "文件超过大小上限"}`（超过 `CLIPWRIGHT_WORKER_MAX_ASSET_MB` 上限，默认 2048 MB） |

### `HEAD /api/worker/assets/{hash}`

| 状态 | 响应 / 说明 |
|------|------|
| `200` | 素材存在（按 `sha1[:16]` 前缀匹配） |
| `404` | `{"detail": "Asset not found"}` |

### `GET /api/worker/assets/{hash}`

| 状态 | 响应 / 说明 |
|------|------|
| `200` | 素材文件（`FileResponse`，直接下载） |
| `404` | `{"detail": "Asset not found"}` |

### `POST /api/worker/jobs`

请求体（JSON）：

```json
{
  "timeline": { "tracks": [...] },
  "params": { "width": 1920, "height": 1080, "fps": 30, "bitrate": "5M", "audio_bitrate": "192k" },
  "asset_refs": { "<asset_id>": "asset://<sha1>" }
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|:----:|------|
| `timeline` | object | ✓ | 必须含 `tracks` 列表；`asset_id` 为原始素材 id（经 `asset_refs` 重映射为 `asset://<sha1>`） |
| `params` | object | | 透传给 `RenderService.render`：`width`/`height`/`fps`/`bitrate`/`audio_bitrate`/`audio_file_path`/`bgm_file_path`/`enable_progress`；缺省宽高帧率回退到 timeline |
| `asset_refs` | object | | 原始素材 id → `asset://<sha1>` 映射；值必须是 `asset://` 开头字符串 |

| 状态 | 响应 / 说明 |
|------|------|
| `202` | `{"job_id": "job_<12 位 hex>"}`，后台异步执行 |
| `400` | `{"detail": "请求体必须是 JSON 对象"}` |
| `400` | `{"detail": "timeline 必须是包含 tracks 列表的对象"}` |
| `400` | `{"detail": "params 必须是对象"}` |
| `400` | `{"detail": "asset_refs 必须是对象"}` |
| `400` | `{"detail": "asset_refs[...] 必须是 asset:// 开头的字符串"}` |

任务状态机：`queued` → `rendering` → `completed | failed`。任何失败（含素材缺失 / timeline 畸形 / `RenderResult.success=False`）都会先写 `status="failed"` + `error`，再兜底保证不会留下僵死任务。

### `GET /api/worker/jobs/{job_id}`

| 状态 | 响应 / 说明 |
|------|------|
| `200` | `{"job_id": "...", "status": "queued|rendering|completed|failed", "progress": 0-100, "phase": "prepare|trim|concat|...|done", "detail": "...", "error": "...", "output_path": "..."}` |
| `404` | `{"detail": "Job {job_id} not found"}` |

`progress` 渲染中封顶 99，完成时写入 100。`output_path` 形如 `<work_dir>/jobs/<job_id>.mp4`。

> **注意**：job 状态保存在 Worker **进程内存**中（`JobStore`，闲置 60 秒 TTL 惰性清理，进程重启即丢失）。渲染中轮询会持续刷新存活时间；完成后请及时下载产物，避免被清理。

### `GET /api/worker/jobs/{job_id}/download`

| 状态 | 响应 / 说明 |
|------|------|
| `200` | MP4 文件（`Content-Type: video/mp4`，`filename=<job_id>.mp4`） |
| `409` | `{"detail": "job not completed"}`（任务尚未完成，轮询后再试） |
| `404` | `{"detail": "Job {job_id} not found"}`（任务不存在） |
| `404` | `{"detail": "output file not found"}`（已完成但产物文件缺失） |

---

## 远程环境要求

Worker 复用主应用 `RenderService`，因此渲染能力与本地完全一致：

| 项 | 要求 |
|----|------|
| ffmpeg / ffprobe | **PATH 中可用**（`RenderService` 直接以字面量 `ffmpeg`/`ffprobe` 调 `subprocess`；`ffmpeg_available()` 探测 `ffmpeg -version`，缺失时渲染直接失败） |
| 编码器 | 默认 `libx264`（软编），preset `medium`；与主应用共用 `clipwright.config.settings`，可用 `CLIPWRIGHT_RENDER_ENCODER` / `CLIPWRIGHT_RENDER_PRESET` 覆盖 |
| 滤镜/解码 | `scale` `pad` `setsar` `format` `colorchannelmixer` `eq` `hue` `gblur` `concat` `overlay` `loudnorm` `amix` `volume`，`drawtext`（需 `fontfile`）；`xfade` 可选（不支持时自动回退普通 `concat`） |
| 音频编码 | `aac` |
| 字体 | 见下方 `_fonts/msyh.ttc` 说明 |

### `_fonts/msyh.ttc` 自动复制

`RenderService._resolve_system_font()`（`services/render.py`）在 **首次需要 drawtext 时**自动处理：

- **Windows**：按优先级探测 `C:\Windows\Fonts\msyh.ttc`、`msyhbd.ttc`、`simhei.ttf`、`simsun.ttc`、`arial.ttf`，命中后复制到 **当前工作目录** `<CWD>/_fonts/`（不存在则创建，已存在则复用），并返回相对路径 `_fonts/<name>`（无盘符冒号，规避该 ffmpeg 构建的过滤器解析器不识别 `\:` 转义的问题）。**Worker 进程必须从一个可写目录启动**，否则字体复制失败会逐项回退，最终 drawtext 无 fontfile 可用。
- **Unix**：直接返回系统字体绝对路径（`Noto CJK` / `DejaVu` / `PingFang` 等），无需复制。

### 可选：Hyperframes（MG 动画）

MG / 图解动画依赖 `HyperframesRenderer`（`npx hyperframes`）。Worker 通过 `_hyperframes_available()` 探测，**不可用时自动降级**：跳过 Hyperframes 合成，动画片段回退为普通 `drawtext` 渲染，任务不会失败。可选安装，非必选。

### 磁盘空间

素材、中间文件与产物分布在三个位置：

| 位置 | 用途 | 说明 |
|------|------|------|
| `<work_dir>/assets/` | 上传素材 | 单文件默认上限 2048 MB（`CLIPWRIGHT_WORKER_MAX_ASSET_MB`） |
| `<CWD>/_cache/tmp/`（含 `trim_cache`） | 渲染中间文件 | `RenderService` 裁剪缓存与临时片段 |
| `<work_dir>/jobs/` | 渲染产物 | `<job_id>.mp4` |

建议预留 **素材总量 2~3 倍** 的空闲空间（素材副本 + 中间片段 + 最终产物并存）。产物下载后 Worker 不会自动清理，长期运行需人工清理。

### 无 GPU / OS 假设

- 默认纯软件编码（libx264），无需 GPU / CUDA。
- 不依赖特定 OS：字体复制路径与系统字体路径在 Windows / Unix 下均被 `_resolve_system_font()` 处理。

---

## 部署

Worker 是**同一仓库的独立进程**，与主应用共用 `pyproject.toml`，依赖已就绪。

```bash
# 安装依赖（与主应用相同）
pip install -e ".[dev]"

# 启动 Worker（默认 0.0.0.0:8100）
python -m clipwright.worker

# 自定义端口：--port 优先于环境变量
python -m clipwright.worker --port 8200
```

启动入口 `clipwright/worker/__main__.py`：

| 配置 | 说明 |
|------|------|
| `--host` | 监听地址，默认 `0.0.0.0` |
| `--port` | 监听端口，默认 `8100`；`--port` 优先，其次 `CLIPWRIGHT_WORKER_PORT` |
| `CLIPWRIGHT_WORKER_TOKEN` | 鉴权令牌；未设置回退 `CLIPWRIGHT_API_TOKEN`；均为空则开放开发模式（启动打印警告） |
| `CLIPWRIGHT_WORKER_WORK_DIR` | 工作目录（`assets/` + `jobs/`），默认 `<仓库根>/_cache/worker`，按需自动创建 |
| `CLIPWRIGHT_WORKER_MAX_ASSET_MB` | 单文件上传上限（MB），默认 `2048`（2GB），非法值回退默认 |

示例（Windows PowerShell）：

```powershell
$env:CLIPWRIGHT_WORKER_TOKEN = "你的令牌"
$env:CLIPWRIGHT_WORKER_WORK_DIR = "D:\render-worker"
python -m clipwright.worker
```

> Worker 从可写目录启动（字体复制到 `./_fonts/` 需要写当前目录）。验证：`curl http://localhost:8100/api/worker/health` 应返回 `{"status": "ok"}`。

---

## 本地侧配置

在**主应用**的 `.env`（`clipwright/.env`）中配置以下 5 个变量（`config.py` 的 `Settings` 字段，均为 `CLIPWRIGHT_` 前缀）：

```bash
# ── 远程渲染 ──
# 远程渲染服务地址（留空表示仅本地渲染）
CLIPWRIGHT_REMOTE_RENDER_URL=http://localhost:8100
# 远程渲染服务鉴权令牌（与 Worker 的 CLIPWRIGHT_WORKER_TOKEN / CLIPWRIGHT_API_TOKEN 一致）
CLIPWRIGHT_REMOTE_RENDER_TOKEN=
# 远程渲染不可用时是否回退本地渲染
CLIPWRIGHT_REMOTE_RENDER_FALLBACK=true
# 远程渲染轮询间隔（秒）
CLIPWRIGHT_REMOTE_RENDER_POLL_INTERVAL=1.5
# 远程渲染超时（秒）
CLIPWRIGHT_REMOTE_RENDER_TIMEOUT=1800
```

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `CLIPWRIGHT_REMOTE_RENDER_URL` | `""` | 远程 Worker 地址；**留空 = 仅本地渲染**（与引入远程前行为完全一致） |
| `CLIPWRIGHT_REMOTE_RENDER_TOKEN` | `""` | 请求 Worker 时携带的 `Authorization: Bearer <token>`；Worker 为开放模式时可不填 |
| `CLIPWRIGHT_REMOTE_RENDER_FALLBACK` | `true` | 远程不可用（网络/超时/任务失败）时回退本地渲染 |
| `CLIPWRIGHT_REMOTE_RENDER_POLL_INTERVAL` | `1.5` | 轮询 `GET /api/worker/jobs/{id}` 的间隔（秒） |
| `CLIPWRIGHT_REMOTE_RENDER_TIMEOUT` | `1800` | 远程渲染整体超时（秒），超时按失败处理 |

**目标接线**（按上述 settings 字段设计；本地侧集成服务 `clipwright/services/remote_render.py` 尚待落地，todo 5/6）：当 `remote_render_url` 非空时，渲染请求改走远程路径 —— 上传 timeline 引用的素材到 Worker（`POST /api/worker/assets`，返回 hash 后以 `asset://<sha1>` 引用）→ `POST /api/worker/jobs` 提交任务 → 每 `poll_interval` 秒轮询状态 → 完成后 `GET /api/worker/jobs/{id}/download` 取回产物；`remote_render_url` 为空时不经远程，直接本地渲染。

---

## 失败回退

远程路径任一环节失败时，按 `CLIPWRIGHT_REMOTE_RENDER_FALLBACK` 决定行为：

| 失败场景 | `fallback=true`（默认） | `fallback=false` |
|----------|------------------------|------------------|
| 网络错误 / 连接被拒（Worker 未启动） | 回退本地渲染 | 渲染报错 |
| `401`（令牌缺失/错误） | 回退本地渲染 | 渲染报错 |
| 上传素材失败 / 超时 | 回退本地渲染 | 渲染报错 |
| job `status=failed`（含素材缺失、timeline 畸形、ffmpeg 失败） | 回退本地渲染 | 渲染报错 |
| 超过 `remote_render_timeout` | 回退本地渲染 | 渲染报错 |

回退语义：

- **同一 timeline**：本地侧用与远程请求完全相同的 timeline 数据走本地 `RenderService`，输出与「远程引入前」完全一致。
- **不留半成品**：本地只保留最终 MP4；远程路径失败时不会在本地残留部分产物 / 中间文件（远程的中间产物留在 Worker 侧 `work_dir`，不影响本地）。
- **`remote_render_url` 未设置**：不做任何远程尝试，行为与引入远程功能前逐字节一致 —— 回退开关无副作用，可随时安全地把 `remote_render_url` 置空回到纯本地模式。
