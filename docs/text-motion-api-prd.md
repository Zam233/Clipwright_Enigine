# 文字 MotionSpec API PRD

> 状态：实施稿  
> 范围：`/api/text-animation` 的 validate-spec / compile / author / commit 契约  
> 关联：`docs/text-motion-llm-spec-prd.md`、`docs/text-motion-frontend-prd.md`

## 1. 背景

前端已经能只读展示 MotionSpec，但编辑/保存需要后端提供：

- validate-spec；
- compile；
- author（Agent 任务）；
- 条件提交/409。

当前后端只有 capabilities/style-packs/asset-packs，因此前端只能 mock。

## 2. 目标

- 服务端是 schema/能力/编译/权威 hash 的唯一真相；
- 前端不执行 `mg_html`、不自行生成权威 hash；
- 编辑流：

```text
draft
  → validate-spec
  → compile
  → ready_to_commit
  → 条件 commit（If-Match / base_hash）
  → 409 或 committed
```

- author 只在 Agent/任务阶段调用 LLM，不在 render；
- 失败显式，不模板回退；
- 兼容旧 clip。

## 3. 通用契约

### 3.1 版本

```json
{
  "schema_version": "motion_spec/1",
  "compiler_version": "1",
  "capability_version": "1"
}
```

### 3.2 权威 hash

服务端对规范化 `MotionSpec` 计算 SHA-256：

- 键排序；
- 数字统一格式；
- UTF-8；
- 不含客户端计算值。

### 3.3 错误

```json
{
  "ok": false,
  "errors": [
    {"layer_id": "t", "field": "keyframes.time", "time": 2.5, "message": "time out of range"}
  ],
  "warnings": []
}
```

错误码：

- `MOTION_SPEC_INVALID`
- `MOTION_SPEC_UNSUPPORTED`
- `MOTION_SPEC_MISSING`
- `MOTION_COMPILE_FAILED`
- `MOTION_AUTHOR_UNAVAILABLE`
- `MOTION_COMMIT_CONFLICT`（409）
- `MOTION_ASSET_UNAVAILABLE`

## 4. validate-spec

```http
POST /api/text-animation/validate-spec
```

请求：

```json
{
  "clip_id": "tc_1",
  "base_hash": "",
  "schema_version": "motion_spec/1",
  "capability_version": "1",
  "draft_spec": {}
}
```

- 校验 Pydantic schema；
- capability；
- 时间/路径/安全区；
- 不写盘；
- 返回 errors/warnings、can_commit。

## 5. compile

```http
POST /api/text-animation/compile
```

请求同 validate + `draft_spec`。

响应：

```json
{
  "ok": true,
  "spec_hash": "...",
  "compiler_version": "1",
  "mg_def": {},
  "preview_ops": [],
  "fidelity": "approx",
  "errors": []
}
```

- 服务端验证后编译；
- 不调用 LLM；
- 不写 timeline；
- 缺能力返回 unsupported，不伪造。

## 6. author

```http
POST /api/text-animation/author
```

请求：

```json
{
  "project_id": "p1",
  "clip_id": "tc_1",
  "text": "技术中立",
  "duration_sec": 3,
  "style": {},
  "idempotency_key": "..."
}
```

响应：

```json
{"task_id": "ma_...", "status": "authoring", "clip_id": "tc_1"}
```

状态：

```http
GET /api/text-animation/author/{task_id}
POST /api/text-animation/author/{task_id}/cancel
```

- 调用 `text-motion-llm` author；
- 不写 timeline，返回 spec 供 commit；
- 任务状态含 seq/progress/error/cancelable；
- 任务只存在 Agent 阶段，不在 render。

## 7. commit

```http
POST /api/text-animation/commit
```

请求：

```json
{
  "project_id": "p1",
  "clip_id": "tc_1",
  "base_hash": "old",
  "spec": {},
  "spec_hash": "...",
  "compiler_version": "1",
  "mg_def": {},
  "preview_ops": [],
  "fidelity": "approx"
}
```

- 服务端重新 validate + compile；
- 不信任客户端 mg_def/hash；
- 校验 `base_hash` 与当前 clip hash；
- 不匹配返回 **409**；
- 原子写 metadata：
  - motion_spec
  - motion_spec_hash
  - motion_spec_version
  - motion_compiler_version
  - preview_ops
  - fidelity
  - mg_def/mg_html
  - provenance
- 保存项目；
- 返回新 revision/hash。

## 8. 与前端

- `textMotionApi.validateSpec/compile/startAuthor/getAuthorTask/cancelAuthor` 已定义；
- 编辑 UI 只有 `ready_to_commit` 才启用 commit；
- 409 保留 draft；
- 前端不执行 `mg_html`；
- 不修改 `clip.keyframes`。

## 9. 验收

- validate/compile 不写盘；
- commit 409 不写；
- author 不写 timeline；
- render 不调 LLM；
- 错误可定位 layer/field/time；
- 旧 clip 兼容；
- 前端 E2E 可完成编辑→compile→commit→刷新。
