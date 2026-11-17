# 文字 MotionSpec API Coding Plan

> 对应 PRD：`docs/text-motion-api-prd.md`  
> 实现分支：后端 `feat/zam_text_motion_llm_spec`，前端 `feat/zam_text_motion_frontend`

## 1. 范围

实现：

- `POST /api/text-animation/validate-spec`
- `POST /api/text-animation/compile`
- `POST /api/text-animation/author`
- `GET /api/text-animation/author/{task_id}`
- `POST /api/text-animation/author/{task_id}/cancel`
- `POST /api/text-animation/commit`

不实现：

- render 调 LLM；
- 前端直接执行 `mg_html`；
- 自动模板回退。

## 2. CP-0：契约与工具（0.5 天）

任务：

- Pydantic 请求/响应模型；
- `canonical_motion_spec_hash(spec)`；
- 错误映射；
- capability 快照。

文件：

```text
clipwright/api/text_animation.py
clipwright/animation/motion_spec.py
tests/clipwright/test_text_motion_api.py
```

验收：

- hash 稳定；
- invalid spec 返回结构化错误；
- 不写盘。

## 3. CP-1：validate-spec / compile（1 天）

任务：

- validate 调 `MotionSpec.model_validate` + `validate_motion_spec`；
- compile 调 `compile_motion_spec`，再 `validate_mg_json`；
- `fidelity` 不默认 exact；
- 返回 errors/warnings/spec_hash/compiler_version。

测试：

- 合法/非法；
- 时间越界；
- 无能力；
- compile 不调用 LLM；
- 缺 MGDef 不返回成功。

## 4. CP-2：author 任务（1 天）

任务：

- 内存任务表 `task_id -> status/spec/error/seq`；
- `POST author` 创建 task，后台 `asyncio.create_task`；
- `GET status`；
- `POST cancel`；
- provider 从 `TextMotionRegistry.get("llm_motion")`；
- 失败 `MOTION_AUTHOR_UNAVAILABLE`/`MOTION_SPEC_INVALID`；
- author 不写 project/timeline。

限制：

- 当前任务表重启丢失；PRD 目标为持久任务，需后续 Mongo/队列。

## 5. CP-3：commit / 409（1 天）

任务：

- 加载 project；
- 找 clip；
- 校验 base_hash 与当前 metadata hash；
- 服务端重新 validate+compile；
- 不信任客户端 mg_def/spec_hash；
- 写 metadata；
- 原子保存；
- 返回 revision/hash；
- 不匹配 409。

测试：

- 成功；
- 409；
- 非 text/caption；
- 不半写；
- 客户端伪造 hash 无效。

## 6. CP-4：前端接线（1 天）

- `validated/compiled` 结果只在一致 draft 上可提交；
- commit 带 base_hash；
- 409 保留 draft；
- author 状态机接 API；
- 只读 UI 按 flag 打开。

## 7. CP-5：E2E

- draft → validate → compile → commit → refresh；
- 409/作者取消/失败；
- 真实 LLM author（provider 可用时）；
- 不执行 mg_html；
- tsc/build、pytest。

## 8. 验收

- [ ] validate/compile 不写盘；
- [ ] author 不写 timeline；
- [ ] commit 原子、409 无半写；
- [ ] render 无 LLM；
- [ ] 前端状态机与 API 对齐；
- [ ] 测试通过。


## 9. 实施记录（2026-11-18）

- validate-spec / compile / author/status/cancel / commit 已在 `clipwright/api/text_animation.py` 落地；
- compile 服务端重新编译并调用 MG validator；
- commit 校验 base_hash、非 text 拒绝、409 conflict、原子写 metadata；
- author 任务为内存状态机，真实 provider 可用时调用；不写 timeline；
- `tests/clipwright/test_text_motion_api.py` 4 passed；
- 前端 PropertiesPanel 已接 validate/compile/commit，支持 keyframe time/opacity 编辑；
- 仍缺：持久任务队列、真实前端 API 联调/视觉抽帧、author 取消/超时完整 E2E。
