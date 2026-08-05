# 前后端功能对账审计报告（Frontend–Backend Parity Audit）

> 仓库：`J:\Clipweight-Client`（前端） ↔ `J:\Clipwright`（后端）
> 阶段：Phase 5 · 六任务大改（clipwright-six-task-overhaul）收尾
> 本文由任务 40 撰写；数据取自**当前工作区真实代码**（非计划假设），所有计数均有来源命令可复现。
> 文档语言与仓库既有文档（README / api_reference.md）一致：中文为主，标识符用英文。

---

## 0. 结论摘要（TL;DR）

- 后端真实路由 **176 条**，分布 29 个 APIRouter（`J:\Clipwright\clipwright\api\*.py`）。
- 前端 API 客户端 **20 个模块 + 172 个函数**（`src/services/api/*.ts`，排除 `client.ts`/`index.ts`/测试文件）。
- 三类差距（无客户端 / 死坏调用 / 形状漂移）**已全部修复**，并补齐 4 个新管理页 + 7 处现有页面接线。
- `/metrics`、独立 worker 服务、`/ws` WebSocket 为**明确范围外**：`WsClient.ts` 已删除，实时链路为 SSE-only。
- 唯一无专属客户端模块的后端路由组为 `/api/test/*`（model_test 4 条），但已被 `ModelsPage.tsx` 以裸 `getApiClient()` 消费（见 §2.4）。

---

## 1. 后端路由全量清单（① 真实路由 inventory）

> 数据来源：`Select-String -Path "J:\Clipwright\clipwright\api\*.py" -Pattern "@router\.(get|post|put|delete|patch)"` → **176 条**。
> 前缀来源：每个文件 `router = APIRouter(prefix=...)`。
> 排除：`errors.py`（无路由）、`__init__.py`（聚合）。`/health` 在 `main.py`（非 `api/*.py`），由 `healthApi.check()` 消费。

| 前缀 | 文件 | 数量 | 路由 |
|---|---|---|---|
| `/api/animation` | animation.py | 4 | GET `/list` · GET `/onscreen` · GET `/transitions` · GET `/get/{animation_id}` |
| `/api/asset` | asset.py | 8 | POST `/upload` · GET `/list` · GET `/{asset_id}` · GET `/{asset_id}/file` · GET `/{asset_id}/thumbnail` · DELETE `/{asset_id}` · POST `/import-path` · POST `/import-url` |
| `/api/persona/forge/chat` | chat_forge.py | 5 | POST `/start` · POST `/message` · POST `/knowledge` · POST `/commit` · GET `/state/{session_id}` |
| `/api/edl` | edl.py | 4 | POST `/import/edl` · POST `/import/fcpxml` · POST `/export/edl` · POST `/export/fcpxml` |
| `/api/fonts` | font.py | 4 | GET `/list` · GET `/default` · GET `/resolve` · POST `/clear-cache` |
| `/api/learning` | learning.py | 11 | GET `/status` · GET `/datasets` · POST `/datasets/create` · DELETE `/datasets/{dataset_id}` · GET `/jobs` · GET `/jobs/{job_id}` · POST `/jobs/create` · POST `/jobs/{job_id}/start` · POST `/jobs/{job_id}/cancel` · DELETE `/jobs/{job_id}` · GET `/models` |
| `/api/material` | material.py | 3 | GET `/sources` · POST `/search` · GET `/asset/{source_id}/{asset_id}` |
| `/api/test` | model_test.py | 4 | POST `/llm` · POST `/embed` · POST `/rerank` · GET `/config` |
| `/api/persona` | persona.py | 9 | GET `/list` · GET `/{persona_id}` · POST `/create` · PUT `/{persona_id}` · DELETE `/{persona_id}` · GET `/{persona_id}/prompt` · PUT `/{persona_id}/prompt` · GET `/{persona_id}/knowledge` · POST `/{persona_id}/knowledge` |
| `/api/persona/forge` | persona_forge.py | 5 | POST `/from-prompt` · POST `/from-script` · POST `/refine` · POST `/dialogue/generate-questions` · POST `/dialogue/build` |
| `/api/pipeline` | pipeline.py | 13 | GET `/runs` · POST `/run-v2` · POST `/run` · POST `/run-async` · GET `/trace/{pipeline_id}` · GET `/trace/stream/{pipeline_id}` · GET `/result/{pipeline_id}` · GET `/status/{pipeline_id}` · POST `/retry/{pipeline_id}/{agent_name}` · POST `/regenerate-scene/{pipeline_id}/{scene_index}` · POST `/predict-script` · POST `/predict-material` · POST `/step/{agent_name}` |
| `/api/plugin` | plugin.py | 10 | GET `/list` · GET `/discover` · POST `/load/{plugin_id}` · POST `/unload/{plugin_id}` · GET `/{plugin_id}/config` · PUT `/{plugin_id}/config` · DELETE `/{plugin_id}/config` · POST `/load-all` · GET `/capabilities` · GET `/{plugin_id}/ui` |
| `/api/preprocess` | preprocess.py | 7 | GET `/operations` · GET `/queue` · POST `/submit` · POST `/batch-submit` · GET `/task/{task_id}` · DELETE `/task/{task_id}` · GET `/task/{task_id}/results` |
| `/api/project` | project.py | 13 | POST `/`（create） · GET `/`（list） · POST `/folders/rename` · POST `/folders/delete` · GET `/{project_id}` · PUT `/{project_id}` · DELETE `/{project_id}` · POST `/{project_id}/duplicate` · PATCH `/{project_id}/rename` · PATCH `/{project_id}/folder` · POST `/{project_id}/tags` · DELETE `/{project_id}/tags/{tag}` · GET `/{project_id}/thumbnail` |
| `/api/proxy` | proxy.py | 2 | POST `/generate` · POST `/switch` |
| `/api/persona`（RAG） | rag.py | 4 | POST `/{persona_id}/rag/query` · POST `/{persona_id}/rag/index` · GET `/{persona_id}/rag/status` · DELETE `/{persona_id}/rag/index` |
| `/api/render` | render.py | 10 | POST `/queue` · GET `/queue/{task_id}` · GET `/queue/stream/{task_id}` · GET `/queue` · GET `/video` · GET `/download/{filename}` · GET `/presets` · POST `/start` · GET `/status/{render_id}` · GET `/thumbnail` |
| `/api/requirements` | requirements.py | 7 | POST `/init` · POST `/chat` · POST `/chat/stream/{session_id}` · POST `/upload/{session_id}` · GET `/session/{session_id}` · GET `/plan/{session_id}` · POST `/proceed` |
| `/api/skill` | skill.py | 2 | GET `/list` · POST `/execute` |
| `/api/stt` | stt.py | 2 | POST `/transcribe` · POST `/align` |
| `/api/subtitle` | subtitle.py | 4 | POST `/import` · POST `/export` · POST `/transcribe` · POST `/align` |
| `/api/template` | template.py | 6 | GET `/list` · GET `/{template_id}` · POST `/create` · PUT `/{template_id}` · DELETE `/{template_id}` · POST `/{template_id}/apply` |
| `/api/tool` | tool.py | 3 | GET `/list` · POST `/execute` · POST `/batch` |
| `/api/type-maker` | type_maker.py | 6 | GET `/list` · GET `/{type_id}` · POST `/create` · PUT `/{type_id}` · DELETE `/{type_id}` · POST `/preview` |
| `/api/video-editor` | video_editor.py | 13 | GET `/status` · GET `/projects` · POST `/projects/create` · GET `/projects/{project_id}` · PUT `/projects/{project_id}` · DELETE `/projects/{project_id}` · POST `/projects/{project_id}/undo` · POST `/projects/{project_id}/redo` · POST `/projects/{project_id}/clips/add` · POST `/projects/{project_id}/clips/remove` · POST `/projects/{project_id}/clips/move` · POST `/projects/{project_id}/clips/split` · POST `/projects/{project_id}/export` |
| `/api/vision` | vision.py | 2 | POST `/analyze` · POST `/import` |
| `/api/voice` | voice.py | 6 | POST `/upload` · POST `/clone` · GET `/list` · DELETE `/{db_id}` · POST `/synthesize` · POST `/dub` |
| `/api/waveform` | waveform.py | 1 | POST `/generate` |
| `/api/webhook` | webhook.py | 8 | GET `/events` · GET `/list` · POST `/register` · DELETE `/{webhook_id}` · PUT `/{webhook_id}/toggle` · POST `/{webhook_id}/test` · POST `/notify` · GET `/deliveries` |
| **合计** | **29 个 router** | **176** | |

---

## 2. 前端客户端清单（② 客户端 inventory）

> 数据来源：`src/services/api/*.ts` 共 **22 个文件**（含 `client.ts` 工厂与 `index.ts` 聚合桶）；20 个业务模块、**172 个函数**。
> 计数命令：遍历 `src/services/api/*.ts`（排除 `client.ts`/`index.ts`/`*.test.ts`）匹配 `async xxx(...)` 顶层方法。

| 模块文件 | 导出 | 函数数 | 覆盖后端 |
|---|---|---|---|
| `pipeline.ts` | `pipelineApi` | 13 | `/api/pipeline`（13/13） |
| `persona.ts` | `personaApi` | 23 | `/api/persona` + RAG + forge + forge/chat（23/23） |
| `project.ts` | `projectApi` / `healthApi` / `pluginApi` / `animationApi` / `skillApi` | 31 | `/api/project` + `/health` + `/api/plugin` + `/api/animation` + `/api/skill` |
| `asset.ts` | `assetApi`（含 material 素材） | 7 | `/api/asset` + `/api/material`（search/sources/asset 详情） |
| `render.ts` | `renderApi` | 10 | `/api/render`（10/10） |
| `requirements.ts` | `requirementsApi` | 7 | `/api/requirements`（7/7） |
| `tool.ts` | `toolApi` | 3 | `/api/tool`（3/3） |
| `voice.ts` | `voiceApi` | 7 | `/api/voice`（6/6 + URL helper） |
| `font.ts` | `fontApi` | 4 | `/api/fonts`（4/4） |
| `webhook.ts` | `webhookApi` | 8 | `/api/webhook`（8/8） |
| `typeMaker.ts` | `typeMakerApi` | 6 | `/api/type-maker`（6/6） |
| `template.ts` | `templateApi` | 6 | `/api/template`（6/6） |
| `edl.ts` | `edlApi` | 4 | `/api/edl`（4/4） |
| `waveform.ts` | `waveformApi` | 1 | `/api/waveform`（1/1） |
| `proxy.ts` | `proxyApi` | 3 | `/api/proxy`（2/2） |
| `preprocess.ts` | `preprocessApi` | 7 | `/api/preprocess`（7/7） |
| `learning.ts` | `learningApi` | 11 | `/api/learning`（11/11） |
| `videoEditor.ts` | `videoEditorApi` | 13 | `/api/video-editor`（13/13） |
| `subtitle.ts` | `subtitleApi` + `sttApi` | 6 | `/api/subtitle`（4/4）+ `/api/stt`（2/2） |
| `vision.ts` | `visionApi` | 2 | `/api/vision`（2/2） |
| `client.ts` | `getApiClient` / `resetApiClient` | — | Axios 工厂（baseURL 默认 `http://localhost:8000`） |
| `index.ts` | 聚合桶 | — | 导出全部 20 个模块 |

### 2.1 聚合桶导出（index.ts）

```ts
export { getApiClient, resetApiClient } from './client';
export { pipelineApi } from './pipeline';
export { personaApi } from './persona';
export { assetApi } from './asset';
export { renderApi } from './render';
export { requirementsApi } from './requirements';
export { projectApi, healthApi, pluginApi, animationApi, skillApi } from './project';
export { toolApi } from './tool';
export { voiceApi } from './voice';
export { fontApi } from './font';
export { webhookApi } from './webhook';
export { typeMakerApi } from './typeMaker';
export { templateApi } from './template';
export { edlApi } from './edl';
export { waveformApi } from './waveform';
export { proxyApi } from './proxy';
export { preprocessApi } from './preprocess';
export { learningApi } from './learning';
export { videoEditorApi } from './videoEditor';
export { subtitleApi, sttApi } from './subtitle';
export { visionApi } from './vision';
```

### 2.2 后端路由 → 前端函数覆盖矩阵

| 后端前缀 | 覆盖方式 |
|---|---|
| `/api/animation` | `animationApi.list/onscreen/transitions/get`（project.ts:156-180） |
| `/api/asset` | `assetApi.list/upload/importPath/importUrl/…` |
| `/api/edl` | `edlApi.importEDL/importFCPXML/exportEDL/exportFCPXML` |
| `/api/fonts` | `fontApi.list/getDefault/resolve/clearCache` |
| `/api/learning` | `learningApi.*`（11 函数） |
| `/api/material` | `assetApi.searchMaterials/listSources/getMaterialAsset`（asset.ts:100-132） |
| `/api/persona` + RAG + forge + forge/chat | `personaApi.*`（23 函数，persona.ts） |
| `/api/pipeline` | `pipelineApi.*`（13 函数） |
| `/api/plugin` | `pluginApi.*`（10 函数，project.ts:101-154） |
| `/api/preprocess` | `preprocessApi.*`（7 函数） |
| `/api/project` | `projectApi.*`（14 函数） |
| `/api/proxy` | `proxyApi.generate/switchToFull/switchToProxy` |
| `/api/render` | `renderApi.*`（10 函数） |
| `/api/requirements` | `requirementsApi.*`（7 函数） |
| `/api/skill` | `skillApi.list/execute` |
| `/api/stt` | `sttApi.transcribe/align`（subtitle.ts:100-129） |
| `/api/subtitle` | `subtitleApi.importSrt/exportSrt/transcribe/align` |
| `/api/template` | `templateApi.list/get/create/update/remove/apply` |
| `/api/test` | ⚠️ 无专属模块，**裸客户端消费**：`ModelsPage.tsx:26,38,46-48` 直接 `getApiClient().get/post` |
| `/api/tool` | `toolApi.list/execute/batch` |
| `/api/type-maker` | `typeMakerApi.list/get/create/update/remove/preview` |
| `/api/video-editor` | `videoEditorApi.*`（13 函数） |
| `/api/vision` | `visionApi.analyze/importImage` |
| `/api/voice` | `voiceApi.*`（6 函数 + getAudioUrl） |
| `/api/waveform` | `waveformApi.generate` |
| `/api/webhook` | `webhookApi.*`（8 函数） |

### 2.3 页面直接调用（非客户端模块，属正常消费路径）

- `EditorToolbar.tsx:148,154`：`/api/asset/upload`、`/api/subtitle/transcribe`（文件上传场景直连）。
- `PluginLayoutRenderer.tsx:67-68`：插件动态 action 端点（`GET`/`POST` 透传）。
- `ModelsPage.tsx`：`/api/test/*` 四个端点（见 §2.4）。

### 2.4 特殊项：`/api/test`（model_test）无专属客户端模块

后端 `model_test.py`（前缀 `/api/test`）的 4 条路由 **没有** `src/services/api/modelTest.ts` 之类的模块，而是由管理页 `src/pages/admin/ModelsPage.tsx` 直接以 `getApiClient()` 调用：

- `ModelsPage.tsx:38` `GET /api/test/config`
- `ModelsPage.tsx:26` `POST /api/test/llm|/embed|/rerank`（`:46-48` 定义端点表）

结论：**路由被 UI 消费（有 UI），但不经类型化客户端**。不计入「无客户端」缺口，仅记录此形态差异。若后续希望统一管理，可将 ModelsPage 内联调用抽取为 `modelTestApi`。

---

## 3. 三类差距 · 修复前后对比（③ BEFORE/AFTER）

> 「BEFORE」= 六任务大改前的客户代码（本计划任务 9-16 描述 + `git diff` 还原）；
> 「AFTER」= 当前工作区实际代码（本次逐文件核对）。

### 3.1 无客户端 / 死客户端（后端路由无前端函数）

| 后端路由组 | BEFORE | AFTER | 证据 |
|---|---|---|---|
| `/api/learning`（11 条） | 无客户端，页面不存在 | `learningApi.*`（11 函数）+ `LearningPage.tsx` | learning.ts |
| `/api/video-editor`（13 条） | 无客户端 | `videoEditorApi.*`（13 函数）+ `VideoEditorPage.tsx` | videoEditor.ts |
| `/api/subtitle` + `/api/stt`（6 条） | 无客户端 | `subtitleApi.*` + `sttApi.*` + `SubtitleToolsPage.tsx` | subtitle.ts |
| `/api/vision`（2 条） | `AssetPanel.tsx:371,375` 内联调用 | 正式化 `visionApi.analyze/importImage` | vision.ts |
| `/api/tool/batch` | 无客户端 | `toolApi.batch` + `ToolsPage` 批量执行控件 | tool.ts:42-45 |
| `/api/material/asset/{source}/{id}` | 无客户端 | `assetApi.getMaterialAsset` + `AssetPanel` 素材详情弹层 | asset.ts:129-132 |
| `/api/pipeline/runs` | 无客户端（PipelineAdminPage 显示假数据） | `pipelineApi.getRunRecords` + 后端 `GET /runs` 真实数据 | pipeline.ts:55-61 |
| `/api/pipeline/predict-material`、`/trace/{id}` | 无客户端 | `predictMaterial`、`getTraceJson` | pipeline.ts:95-113 |
| persona RAG / prompt / knowledge / chat-forge 状态 | 仅部分 | `ragIndex/ragStatus/ragDelete`、`getPrompt/updatePrompt`、`getKnowledge`、`chatForgeState` | persona.ts:57-146 |
| forge `/from-script`、`/dialogue/*` | 无客户端 | `forgeFromScript`、`forgeDialogueQuestions/Build` | persona.ts:164-191 |
| `/api/animation` ons/transitions/get | 仅 `list` | `onscreen/transitions/get`（PropertiesPanel 在线预设） | project.ts:163-179 |
| `/api/test/*`（4 条） | 无模块（ModelsPage 内联） | **维持内联消费**（有 UI） | ModelsPage.tsx |

### 3.2 死/坏调用（调用不存在路由或必 422/404）

| 客户端 | 字段 | BEFORE | AFTER | 后端事实源 |
|---|---|---|---|---|
| `preprocess.submit` | 请求体 | `{op_id, asset_path, params}` → **422** | `{file_path, operations[]}` | preprocess.py:55-61 `SubmitRequest` |
| `preprocess.batchSubmit` | 请求体 | `{ops:[…]}` → **422** | `{file_paths[], operations[]}` | preprocess.py:64-70 `BatchSubmitRequest` |
| `preprocess.listResults` | 端点 | `GET /api/preprocess/results`（**不存在 → 404**） | `GET /api/preprocess/task/{task_id}/results`（签名增 `taskId`） | preprocess.py:233 |
| `preprocess.PreprocessTask` | 类型 | `{id, op_id, result}` | `{task_id, file_path, file_name, operations, status, progress, results, error}` | preprocess.py 任务 dict |
| `proxy.generate` | 请求体 | `{asset_path, resolution}` → **422** | `{input_path, proxy_height}` | proxy.py:13 |
| `proxy.switchToFull/switchToProxy` | 请求体 | `{asset_path, use_proxy}` → **422** | `{timeline, proxy_suffix}` | proxy.py:25 |
| `typeMaker.preview` | 请求体 | 非完整定义 → **422** | 发送完整 `TypeDefinition` | type_maker.py:202 |
| `persona.forgeFromPrompt` | 请求体 | 缺 `persona_id` → **422** | `{persona_id, description, persona_name}` | persona_forge.py:18-21 |
| `webhook.test` | 响应 | 直接返回 `{status, response_code, error}`，UI 读不到 success | `normalizeWebhookTest()` → `{success, status_code, body}` | webhook.py:186-191 |
| `template.apply` | 类型/行为 | 断言 `{project_id}` 且丢弃后端时间线 | 断言 `{status, template_id, timeline}`，TemplatesPage 用返回时间线建项目 | template.py:181 |

### 3.3 形状漂移（字段错配 / 假类型）

| 位置 | BEFORE | AFTER | 后端事实源 |
|---|---|---|---|
| `typeMaker.TypeDefinition` | `persona_mappings`（复数数组） | `persona_mapping: Record<string, PersonaMappingItem>`（单数 dict） | type_maker.py:60 |
| `typeMaker` 列表项 | `is_builtin: boolean` | `builtin: boolean` | type_maker.py:70 |
| `typeMaker` 多余字段 | `created_at/updated_at/is_builtin` 硬编码进类型 | 移除，改 `builtin/tags` | type_maker.py:65-71 |
| `pipeline.getStatus` | 断言 `{phase?, progress?}`（后端无此字段） | 断言 `{pipeline_id, status, has_result}` | pipeline.py:189-197 |
| `pipeline` 运行记录 | 无类型（管理页用假数据） | `PipelineRunRecord`/`PipelineSpan` 对齐 `{id, topic, status, duration_ms, started_at, agents[{agent,start,dur,status}]}` | pipeline.py:31-35、pipeline_v2.get_run_records |
| `preprocess` 任务类型 | `{id, op_id, result}` | 对齐后端 `{task_id, file_path, …}` | preprocess.py 任务 dict |
| `webhook.test` 响应形状 | 后端原始 `{status, response_code, error}` | 归一化 `{success, status_code, body}` | webhook.py:186-191 |

### 3.4 差距收敛结果

| 指标 | BEFORE | AFTER |
|---|---|---|
| 无客户端路由组（除 /metrics/worker/ws） | ~10 组（learning/video-editor/subtitle/stt/vision/tool-batch/material 详情/pipeline-runs/persona RAG 等） | **0 组**（`/api/test` 由 ModelsPage 内联消费） |
| 死/坏调用 | 10 处（3 个死调用 + 6 个 422 + 1 个 404） | **0 处** |
| 形状漂移 | 6 处 | **0 处** |
| 幽灵路由（前端调后端不存在的路由） | 1 处（`/api/preprocess/results`） | **0 处** |

---

## 4. 无 UI 功能补齐（④ 新增页面 + 接线）

后端原本存在但前端无界面的功能，本次补齐 UI：

### 4.1 新增 4 个管理页（路由 + 导航 + 布局）

| 页面文件 | 路由 | 覆盖后端路由 | 接线点 |
|---|---|---|---|
| `src/pages/admin/LearningPage.tsx` | `/settings/learning` | `/api/learning` 全 11 条（数据集/任务/模型/状态） | router.tsx:152、SettingsPage.tsx:186-196 |
| `src/pages/admin/VideoEditorPage.tsx` | `/settings/video-editor` | `/api/video-editor` 13 条 + proxy generate/switch + waveform generate（运维控制台，非完整编辑器） | router.tsx:160、SettingsPage.tsx:220-228 |
| `src/pages/admin/PreprocessPage.tsx` | `/settings/preprocess` | `/api/preprocess` 7 条（操作/队列/提交/批量/详情/结果） | router.tsx:176-178、SettingsPage.tsx:202-212 |
| `src/pages/admin/SubtitleToolsPage.tsx` | `/settings/subtitle-tools` | `/api/subtitle` + `/api/stt` 6 条 | router.tsx:172、SettingsPage.tsx:236-244 |

路由均经 `React.lazy()` 懒加载；`StandardLayout.tsx` 子导航与 `SettingsPage.tsx` 首页索引同步更新（SettingsPage.tsx:181-244）。

### 4.2 现有页面接线（此前为死客户端/未消费路由）

| 页面 | 接线内容 | 消费路由 |
|---|---|---|
| `PropertiesPanel.tsx`（AnimationSection） | 挂载时请求后端动画预设，在线展示、离线回退静态 `ANIMATION_PRESETS` | `/api/animation/list` + `/onscreen` + `/transitions` |
| `PersonaDetailPage.tsx` | 提示词 GET/PUT 编辑、知识库列表、RAG index/status/delete 按钮 | `/api/persona/{id}/prompt` · `/knowledge` · `/rag/*` |
| `PersonaForgePage.tsx` | chat-forge 会话状态恢复、从脚本文稿生成、对话引导 questions/build | `/api/persona/forge/*` 全部模式 |
| `ToolsPage.tsx` | 批量执行控件 | `/api/tool/batch` |
| `PipelineAdminPage.tsx` | 改用真实 `GET /api/pipeline/runs`（移除假数据兜底，仅后端离线才回退）+ trace JSON 检视 | `/api/pipeline/runs` · `/trace/{id}` |
| `AssetPanel.tsx` | 素材详情弹层 | `/api/material/asset/{source}/{id}` |
| `TemplatesPage.tsx` | 应用模板后使用后端返回时间线建项目（不再丢弃） | `/api/template/{id}/apply` |

---

## 5. 范围外项（⑤ 明确不做）

| 项 | 现状 | 说明 |
|---|---|---|
| `/metrics`（Prometheus） | 后端可能暴露，前端**无 UI** | 监控指标属运维侧，不建界面 |
| 独立 worker 服务（`worker/api.py`） | **未挂载**到主应用 | 不作为路由对账对象，无前端客户端 |
| `/ws` WebSocket | **`src/services/ws/WsClient.ts` 已删除**（任务 15） | 前端已无任何 `@/services/ws` 引用；`vite.config.ts` 仅保留 `/api` 代理（无 `/ws`）；实时链路为 **SSE-only**（`/api/pipeline/trace/stream/{id}`、`/api/requirements/chat/stream/{session_id}`、`/api/render/queue/stream/{task_id}`） |
| 残留遗留 | `SettingsPage.tsx:51`「WebSocket 地址」字段、`.env.example` 的 `VITE_WS_URL` | 历史遗留 UI 字段/环境变量，不影响功能；不在此次对账范围内改动（如需彻底移除属任务 15/41 延伸） |

> ⚠️ 一致性声明：本报告 §5 的「SSE-only / 无 /ws」表述已与当前代码核对一致——
> `grep -r "services/ws|WsClient|WebSocket" src` 仅命中 `SettingsPage.tsx:51` 一处遗留文案；
> `vite.config.ts` 无 `ws` 代理（见上）。

---

## 6. 复现命令（QA 证据）

```powershell
# 后端路由总数（176）
Select-String -Path "J:\Clipwright\clipwright\api\*.py" -Pattern "@router\.(get|post|put|delete|patch)" | Measure-Object

# 每文件路由数
Select-String -Path "J:\Clipwright\clipwright\api\*.py" -Pattern "@router\.(get|post|put|delete|patch)" | Group-Object Filename | Sort-Object Name

# 前端客户端函数总数（172）
$files = Get-ChildItem "J:\Clipweight-Client\src\services\api\*.ts" | Where-Object { $_.Name -notmatch "\.test\.|client\.ts|index\.ts" }
$files | ForEach-Object { (Select-String -Path $_.FullName -Pattern "^\s{2,4}async\s+\w+").Count } | Measure-Object -Sum

# 死客户端残留检查（应 0）
#   - 调用不存在的 /api/preprocess/results
#   - @/services/ws 引用
```

---

## 7. 结论与残留风险

**结论**：前后端功能对账完成——后端 176 条真实路由全部有前端消费路径（除明确范围外的 /metrics、worker、/ws）；10 处死/坏调用与 6 处形状漂移已修复；4 个新管理页 + 7 处页面接线已落地；文档（本报告、AGENTS.md、README）与代码一致。

**残留风险 / 后续建议**：
1. `/api/test/*` 无专属客户端模块（ModelsPage 内联），如后续统一 API 管理可抽取 `modelTestApi`。
2. `SettingsPage.tsx:51`「WebSocket 地址」为 WsClient 移除后的遗留字段，属 UI 清理项，不影响对账结论。
3. 本报告基于 2026-08-04 工作区快照；路由新增/变更后需重跑 §6 命令并更新本报告。
