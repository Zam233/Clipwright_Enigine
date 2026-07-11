# 帧艺 ClipWright 内容视频编排引擎 — API 参考

## 基础信息

- **Base URL**: `http://localhost:8000`
- **格式**: 全部请求/响应均为 JSON
- **交互式文档**: `http://localhost:8000/docs`

---

## 健康检查

### `GET /health`

返回服务状态。

```json
{"status": "ok", "service": "clipwright-engine"}
```

---

## Pipeline

### `POST /api/pipeline/run`

全流程执行：Structure → Material → Edit → Animation → Audio → Quality。

**请求**：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `persona_id` | string | 是 | Persona ID（对应 `personas/{id}/` 目录） |
| `category_plugin_id` | string | 是 | 视频类型插件 ID |
| `topic` | string | 是 | 选题/话题 |
| `extra_params` | object | 否 | 额外参数 |
| `dry_run` | bool | 否 | 仅生成预览，不渲染 |

**响应**：`PipelineState` 对象，包含 `steps`（每个 Agent 的执行状态）和 `shared_data.final_timeline`。

**示例**：

```bash
curl -X POST http://localhost:8000/api/pipeline/run \
  -H "Content-Type: application/json" \
  -d '{
    "persona_id": "zam_knowledge_critical",
    "category_plugin_id": "knowledge_longform",
    "topic": "年轻人的盲盒消费"
  }'
```

### `POST /api/pipeline/step/{agent_name}`

单 Agent 执行。

---

## Persona

### `GET /api/persona/list`

列出所有可用 Persona。返回 `string[]`。

### `GET /api/persona/{persona_id}`

获取指定 Persona 的完整配置（含四层）。

### `POST /api/persona/create`

创建新 Persona。

### `PUT /api/persona/{persona_id}`

更新 Persona 配置。

---

## PersonaForge（智能构建）

### `POST /api/persona/forge/from-prompt`

自然语言描述 → Persona。

**请求**：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `description` | string | 是 | 风格描述文本 |
| `persona_id` | string | 是 | 生成目标的 Persona ID |
| `persona_name` | string | 否 | Persona 显示名称 |

**示例**：

```bash
curl -X POST http://localhost:8000/api/persona/forge/from-prompt \
  -H "Content-Type: application/json" \
  -d '{
    "description": "冷峻风格的科技评论，画面黑白为主，文字用打字机效果，节奏偏快",
    "persona_id": "cold_tech_review_v1"
  }'
```

### `POST /api/persona/forge/from-script`

脚本/口播文本分析 → Persona（语言层精确）。

**请求**：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `script` | string | 是 | 脚本或字幕文本 |
| `persona_id` | string | 是 | 生成目标的 Persona ID |
| `persona_name` | string | 否 | Persona 显示名称 |
| `script_format` | string | 否 | `txt` / `srt` / `md` |

### `POST /api/persona/forge/refine`

迭代优化。

**请求**：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `persona_id` | string | 是 | 已有 Persona ID |
| `feedback` | string | 是 | 自然语言反馈（如"节奏太慢了"） |

### `POST /api/persona/forge/dialogue/generate-questions`

对话引导：生成下一步问题。

**请求**：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `persona_id` | string | 是 | 目标 Persona ID |
| `existing_answers` | object | 否 | 已收集的问答对 |

### `POST /api/persona/forge/dialogue/build`

对话引导：将问答编译为 Persona。

**请求**：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `persona_id` | string | 是 | 目标 Persona ID |
| `persona_name` | string | 否 | Persona 显示名称 |
| `answers` | object | 是 | 问答记录 |

---

## Render

### `POST /api/render/start`

提交渲染任务。

### `GET /api/render/status/{render_id}`

查询渲染进度。

---

## Tool（原子能力层）

### `GET /api/tool/list`

列出所有已注册的原子能力工具及其可用状态。

**响应**：`ToolInfo[]`，每个工具包含 `name`、`description`、`parameters`、`available`。

### `POST /api/tool/execute`

按名称执行工具。

**请求参数**（query）：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 是 | 工具名称 |

**请求体**（JSON）：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `params` | object | 否 | 工具参数（键值对） |

**响应**：`ToolExecResult`，包含 `status`（success/error/not_found/dependency_missing）、`output`、`error`。

### `POST /api/tool/batch`

批量执行多个工具调用（顺序执行，互不影响）。

**请求体**（JSON）：

```json
[
  {"tool": "scene_detect", "input_path": "/path/to/video.mp4", "threshold": 0.3},
  {"tool": "audio_extract", "input_path": "/path/to/video.mp4", "format": "wav"}
]
```

**响应**：`ToolExecResult[]`

---

## Skill（技能）

### `GET /api/skill/list`

列出所有已注册的技能及其可用状态。

**响应**：`SkillInfo[]`，每个技能包含 `name`、`description`、`parameters`、`required_tools`、`available`。

### `POST /api/skill/execute`

按名称执行技能。

**请求参数**（query）：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 是 | 技能名称 |

**请求体**（JSON）：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `params` | object | 否 | 技能参数 |

**响应**：`SkillExecResult`，包含 `status`、`output`、`tool_calls`（技能内部调用的工具记录）。

---

## Plugin（第三方插件管理）

### `GET /api/plugin/list`

列出所有已加载的第三方插件。

**响应**：`PluginMetadata[]`，每个包含 `manifest`、`enabled`、`config`。

### `GET /api/plugin/discover`

发现插件目录中的所有可用插件（不加载）。

**响应**：`string[]`（插件 ID 列表）

### `POST /api/plugin/load/{plugin_id}`

加载并初始化指定插件。

**响应**：`PluginMetadata`（加载后的插件元信息）

### `POST /api/plugin/unload/{plugin_id}`

卸载指定插件。

**响应**：`{"status": "ok", "plugin_id": "..."}`

### `POST /api/plugin/load-all`

发现并加载所有可用插件。

**响应**：`string[]`（成功加载的插件 ID 列表）

### `GET /api/plugin/capabilities`

获取系统全部能力概览（插件 + 工具 + 技能）。

**响应**：

```json
{
  "tools": [{"name": "...", "available": true, ...}],
  "skills": [{"name": "...", "available": true, ...}],
  "plugins": [{"manifest": {...}, "enabled": true, ...}]
}
```
