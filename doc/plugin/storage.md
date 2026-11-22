# 存储与任务：workspace / artifact / materials / job

插件不要直接读写项目目录；通过宿主提供的受限接口拿数据、登记产物、跑子任务。权限由
`scope`（plugin_id / project_id / task_id）约束，跨项目访问会被拒绝。

## 1. workspace：插件自己的命名空间

```python
from clipwright.plugins.runtime.bridge import call_host

await call_host("workspace.write", {
    "path": "cache/index.json",
    "data": b'{"v":1}',
})
info = await call_host("workspace.read", {"path": "cache/index.json"})
```

| 方法 | 参数 | 说明 |
|---|---|---|
| `workspace.read` | `path` | 返回 `{path,size,sha256,data}` |
| `workspace.write` | `path,data,expected_revision?` | 原子写；revision 不匹配返回 `REVISION_CONFLICT` |
| `workspace.list` | `path?` | 列表，限 2000 项 |
| `workspace.stat` | `path` | 元数据 |
| `workspace.hash` | `path` | sha256 |
| `workspace.mkdir` | `path` | 建目录 |
| `workspace.delete` | `path` | 仅命名空间内 |
| `workspace.rename` | `src,dst` | 仅命名空间内 |

路径都相对于插件命名空间；`../` 等逃逸会得到 `PERMISSION_DENIED`。持久化数据放这里，
不要写插件自身目录（升级会覆盖）。

## 2. artifact：登记产物

```python
await call_host("artifact.register", {
    "path": "out/preview.mov",
    "role": "preview",
    "mime": "video/quicktime",
})
items = await call_host("artifact.list", {"scope": "own", "role": "preview"})
```

- 宿主负责 hash/size/mime 与保留策略；插件只提供路径与角色；
- `scope=own|task` 可用；`project` 未授权会被拒绝；
- 产物引用可交给主 Agent / 报告 / 渲染链消费；不要让用户拿裸绝对路径。

## 3. materials：项目素材（只读）

```python
rows = await call_host("materials.list", {"limit": 50})
data = await call_host("materials.read", {"ref": rows[0]["ref"], "max_bytes": 4 * 1024 * 1024})
```

- `materials.list` 返回 `{name,ref,mime,size,sha256}`；
- `materials.read` 返回 `data_b64`，默认上限 4 MiB；
- 素材目录是项目 `materials/`，插件只读；需要写就登记到插件 workspace 或 artifact。

## 4. job：跑外部命令

```python
job = await call_host("job.submit", {
    "runtime_id": "ffmpeg",
    "argv": ["-i", "in.mp4", "-t", "3", "out.mp4"],
    "outputs": ["out.mp4"],
    "timeout_ms": 120000,
    "idempotency_key": "trim-c1",
})
status = await call_host("job.status", {"job_id": job["job_id"]})
```

- `shell=False`，参数是列表；禁止拼字符串；
- 环境变量走白名单；runtime 需管理员登记；
- 状态：`queued/running/succeeded/failed/cancelled/timed_out/interrupted`；
- 可取消：`job.cancel`；任务取消时宿主会推 `job.cancelled` 事件；
- 输出文件通过 `outputs` 声明，宿主负责收集/校验。

## 5. timeline：只读摘要

```python
summary = await call_host("timeline.summary", {})
# {duration_sec,width,height,fps,track_count,clip_count}
```

需要完整时间线时让 engine 组装结构化请求（见 [子进程插件](subprocess.md) 的
`media_overlay.v2`），不要自己去读项目文件。

## 6. 能力调用

```python
result = await call_host("capability.call", {"name": "media_probe", "input": {"path": "in.mp4"}})
```

默认放行 `media_probe` / `frame_check` / `align`；其他能力要管理员在
`runtime_config.capabilities` 里登记。能力缺失是一等状态：返回错误/空结果，
不要伪造成功。
