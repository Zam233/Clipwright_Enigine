# 子进程插件（`runtime: subprocess`）

进程内插件适合受信任代码；需要隔离时用子进程：宿主**不 import** 插件代码，只通过 NDJSON
线协议通信。实现在 `plugins/runtime/` 与 `services/plugin_{rpc,workspace,materials,jobs}.py`。

## 1. 声明

```yaml
id: my_worker
name: 子进程示例
version: 0.1.0
kind: capability
api_version: "2"
runtime: subprocess
permissions: [network]
runtime_config:
  call_timeout: 60
  exposes: ["tool:my_tool"]
  capabilities: ["llm"]
  extra_read_roots: ["D:/models"]
contributions:
  - { kind: tool, id: my_tool }
```

`permissions` 只能是 `network` / `http` / `fs_write`，且必须落在
`settings.plugin_allowed_permissions` 内。`fs_write` 只能写插件自己的 `PluginData` 命名空间。

## 2. 协议

- 换行分隔 JSON（NDJSON），一行一条消息；v2 当前版本，兼容 v1；
- 请求/响应带自增 `id`，`direction` 区分 host/worker，两个方向独立编号；
- 上限：单帧 **8 MiB**、单方向在途 **32**、嵌套深度 **8**；超限直接拒绝；
- worker 启动后向宿主 `describe` 自己的贡献声明，宿主据此走门面注册。

worker 侧调用宿主：

```python
from clipwright.plugins.runtime.bridge import call_host, on_event

rows = await call_host("workspace.stat", {"path": "motion/project.json"})

@on_event
def _on_event(name, payload):
    if name == "job.cancelled":
        ...  # 清理
```

## 3. 宿主方法（`services/plugin_rpc.py`）

| 方法 | 说明 |
|---|---|
| `workspace.read/write/list/stat/hash/mkdir/delete/rename` | 插件命名空间内文件；写是原子写，冲突 `REVISION_CONFLICT`，越界 `PERMISSION_DENIED` |
| `artifact.register/list/get` | 注册产物（宿主算 hash/size/mime）；`own` / `task` 范围 |
| `materials.list/read` | 项目 `materials/` 只读清单/读取（默认 4 MiB） |
| `job.submit/status/list/cancel` | `shell=False` 子任务；状态 queued/running/succeeded/failed/cancelled/timed_out/interrupted |
| `plugin.call` | 调其他插件 tool/skill；需要 `dependencies` + 对方 `exposes` |
| `timeline.summary` | 任务级只读时间线摘要 |
| `capability.call` | 引擎能力；默认 `media_probe/frame_check/align`，其他需 `runtime_config.capabilities` |

每个请求都带 `scope`（plugin_id/project_id/task_id）；宿主校验后构造受限视图，插件不能跨项目读写。

## 4. 事件

`@on_event` 订阅单向事件：`job.cancelled`、`job.timed_out`、`task.cancelled`。事件没有 `id`，
不需要响应；收到后自行清理临时状态。

## 5. 渲染请求契约（`media_overlay.v2`）

clip kind 声明 `render_contract: media_overlay.v2` 时，请求包含：

```json
{
  "contract": "media_overlay.v2",
  "clip": {"id","kind","start_sec","duration_sec"},
  "context": {"topic","category","stage","duration_sec","width","height","fps","persona_id?","brief_summary?"},
  "cues": [{"start_sec","duration_sec","text","source":"caption|nel","type?","bpm?"}],
  "audio": {"clip_id","duration_sec","role":"dub|narration","artifact_id"} | null,
  "materials": [{"name","ref","mime","size","sha256"}],
  "neighbors": [{"clip_id","kind","start_sec","duration_sec","text"}],
  "render_settings": {"width","height","fps"},
  "output": {"container":"mov","alpha":true},
  "truncated": true?
}
```

上限：cues ≤200、neighbors ≤8、materials ≤32、总 JSON ≤256KB。只消费结构化字段；
**禁止**执行插件返回的 HTML/JS，外链资源按安全评审拦截。

## 6. 错误码

`BAD_REQUEST`、`UNKNOWN_METHOD`、`PERMISSION_DENIED`、`PLUGIN_ERROR`、`TIMEOUT`、
`WORKER_DEAD`、`BOOT_FAILED`、`UNSUPPORTED_KIND`、`OVERLOADED`、`DEPTH_EXCEEDED`、
`CANCELLED`、`SCOPE_DENIED`、`UNSUPPORTED_METHOD`。

宿主按码决策：可重试的（TIMEOUT/OVERLOADED）退避重试；WORKER_DEAD/BOOT_FAILED 禁用该插件
并写错误通道；SCOPE_DENIED 不重试。

## 7. 运维

- runtime 登记：`GET/POST /api/plugin/runtimes`；
- 能力授权：`GET /api/plugin/{id}/capabilities`、`POST/DELETE .../{name}`；
- 排错：`GET /api/plugin/errors`，日志前缀 `clipwright.plugin.<id>`；
- 环境变量白名单：worker 只拿白名单 env；读路径限插件目录、Python 环境、显式授权目录。
