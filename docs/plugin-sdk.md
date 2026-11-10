# 插件 SDK（subprocess 插件可用面）

> 对应实现：`plugins/runtime/`（host/worker/bridge/protocol/permissions）+ `services/plugin_{rpc,workspace,materials,jobs,context}.py`
> 安全基线（M1）：worker 只拿白名单环境变量；读仅限插件目录、Python 运行环境与显式授权目录。

## 1. 声明（plugin.yaml）

```yaml
id: my_plugin
runtime: subprocess
permissions: []            # network / http / fs_write（写仅限 PluginData）
dependencies: [other_plugin]   # M5：允许调用哪些插件
runtime_config:
  call_timeout: 60
  exposes:                 # M5：允许别人调用本插件的哪些能力
    - "tool:my_tool"
    - "skill:my_skill"
  capabilities:            # M7：额外授权的引擎能力（默认只有 align/frame_check/media_probe）
    - "llm"
  extra_read_roots:        # M1：额外的只读目录（可选，谨慎使用）
    - "D:/models"
contributions:
  - { kind: tool, id: my_tool }
```

## 2. 宿主 RPC（`bridge.call_host`）

| 方法 | 参数 | 返回 | 说明 |
|---|---|---|---|
| `workspace.read` | `path` | `{path,size,sha256,data}` | 插件命名空间内相对路径 |
| `workspace.write` | `path,data,expected_revision?` | `{path,size,sha256,revision}` | 原子写；revision 冲突返回 `REVISION_CONFLICT` |
| `workspace.list` | `path?` | `[{name,path,type,size,mtime}]` | 限 2000 项 |
| `workspace.stat/hash/mkdir/delete/rename` | `path` / `src,dst` | 通用元数据 | 删除/重命名仅限命名空间内 |
| `materials.list` | `limit?` | `[{name,ref,mime,size,sha256}]` | M3：项目 `materials/` 只读清单 |
| `materials.read` | `ref,max_bytes?` | `{name,mime,size,sha256,data_b64}` | M3：默认限 4MB |
| `artifact.register` | `path,role?,mime?,job_id?,retention?` | `{artifact_id,...}` | 宿主计算 hash/size/mime |
| `artifact.list` | `scope=own\|task,status?,role?` | `[...]` | M3：`project` 未授权拒绝 |
| `artifact.get` | `artifact_id` | `{...}` | 仅本插件 |
| `job.submit` | `runtime_id,argv,cwd?,outputs?,timeout_ms?,idempotency_key?` | `{job_id,...}` | `shell=False`、env 白名单 |
| `job.status/list/cancel` | `job_id` / `status?` | job 记录 | 状态：queued/running/succeeded/failed/cancelled/timed_out/interrupted |
| `plugin.call` | `plugin_id,kind(tool\|skill),id,input?,chain?` | `{plugin_id,kind,id,result}` | M5：需 `dependencies` + 对方 `exposes` |
| `timeline.summary` | — | `{duration_sec,width,height,fps,track_count,clip_count}` | M6：任务级只读摘要 |
| `capability.call` | `name,input?` | 能力结果 | M7：默认 `align/frame_check/media_probe`；其他需 `capabilities` |

## 3. 事件（M4）

```python
from clipwright.plugins.runtime.bridge import on_event

@on_event
def _handle(name, payload):
    if name == "job.cancelled":
        ...  # 清理临时状态
```

事件：`job.cancelled`、`job.timed_out`、`task.cancelled`。单向、无 id、不需要响应。

## 4. 渲染请求（`media_overlay.v2`）

clip_kind 的 `render_contract: media_overlay.v2` 时，请求包含：

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

上限：cues ≤200、neighbors ≤8、materials ≤32、总 JSON ≤256KB。v1 请求只含旧字段。

## 5. 错误码

`BAD_REQUEST`、`PERMISSION_DENIED`、`NOT_FOUND`、`REVISION_CONFLICT`、`ALREADY_EXISTS`、`UNKNOWN_METHOD`、`UNKNOWN_PLUGIN`、`UNAVAILABLE`、`UNSUPPORTED_RUNTIME`、`OVERLOADED`、`DEPTH_EXCEEDED`、`TIMEOUT`、`WORKER_DEAD`、`CANCELLED`。

## 6. 安全模型（合作型插件）

- **env**：worker 只继承白名单（PATH/系统目录/编码/临时目录），拿不到 `CLIPWRIGHT_*` 与模型密钥。
- **读**：限插件目录、Python 运行环境、`extra_read_roots`；`materials` 走独立只读接口。
- **写**：worker 仅能写 PluginData；业务文件走 `workspace.write`（scope 内）。
- **子进程**：插件不能自行 `subprocess`；渲染/编译走 `job.submit` + 已批准 runtime。
- **网络**：未声明 `network/http` 时审计钩子拒绝。
- **非目标**：OS 级恶意隔离尚未实现；威胁模型是“合作型插件 + 最小权限”。

## 7. 版本与兼容

- 协议 v1 仅 host→worker；v2 增加 worker→host 请求与 event 帧，宿主同时兼容。
- 请求契约 v1/v2 由 `clip_kind.render_contract` 选择，旧插件零改动。
- M1 读根限制是**破坏性安全修复**：依赖“读任意文件”的老插件需声明 `runtime_config.extra_read_roots`。
