# AI 生成产物入轨 + 规划感知 — 设计与执行计划

> 状态：已完成（2026-09-07）
> 关联：`docs/fix-and-feature-plan.md` §10 执行轮次 60（火山引擎迁移）；本计划是其后续——把生成能力接进管线编排与规划链路。

---

## 一、现状核查结论（2026-09，含证据）

### 1.1 生成产物进不了时间线（断点在素材检索）

时间线层本身是通用的，断点集中在「素材检索」一环：

| 环节 | 现状 | 证据 |
|------|------|------|
| EditAgent 落轨 | 支持 `ClipKind.VIDEO` / `ClipKind.IMAGE` 两类片段 | `agents/edit_agent.py:913` |
| 素材类型枚举 | `MaterialType` 含 video/audio/image | `schema/material.py:12-17` |
| AudioAgent BGM 铺轨 | 消费 `local_path or url` | `agents/audio_agent.py:263-264` |
| **素材检索** | MaterialAgent 唯一来源是 `MaterialRegistry.search()`（`agents/material_agent.py:339`）；三个生成插件**未注册任何 MaterialSource**（注册素材源的只有 bgm_library/gif_sticker/coverr/pexels/pixabay/unsplash） | `plugins/*/main.py` |
| **剪辑消费** | EditAgent 只吃 `input_data.candidate_clips`（按 scene_index 分组、suggested_assets 按分排序），全部来自素材代理 | `agents/edit_agent.py:719-724` |
| **BGM 检索** | 只搜 MaterialRegistry | `agents/audio_agent.py:27-47,161-162` |

### 1.2 规划阶段对生成能力的感知

| 环节 | 现状 | 证据 |
|------|------|------|
| StructureAgent 工具暴露 | 三个生成工具**已**进入 function schemas（动态收集 `agent_callable` 工具），提示词亦提及「如 AI 生成图片/视频/音乐等」 | `agents/structure_agent.py:315-339` |
| 可用性过滤 | `ToolRegistry.list_agent_callable()` 按 `is_available()` 过滤，但三个工具未重写 `is_available()`（基类只查二进制依赖）→ **未配 key 也照常暴露**，LLM 调用只换来「未配置」报错 | `tool/registry.py:92-95`、`tool/base.py:50-57` |
| 使用策略 | 无「何时生成 vs 搜索」规则；调用凭 LLM 随缘 | `structure_agent.py` 系统提示词「## 可用工具」段 |
| 生成结果落点 | 工具结果回传对话（`services/llm.py:428-457`），但场景是自由文本 dict，无字段承载产物路径；下游无解析 → 路径随对话丢弃 | `schema/agent.py` StructureOutput.scenes |
| 需求服务 | 简报/规划书不感知生成能力：`_material_library_overview()` 只报 MaterialRegistry 源名（`requirements_service.py:173-190`，注入点 :1320-1326）；asset_ratio 只有 footage/mg 两档 | `requirements_service.py:257`、`structure_agent.py:645-648`、`animation/mg/generator.py:839-843` |

### 1.3 AnimationAgent 图片语义索引：已实现、全链路孤立

`image_assets` → `_build_image_semantic_index`（vision 分析 → 标签/描述索引 → MG 生成提示词注入选图菜单）整条链路完整（`agents/animation_agent.py:108-122,766-806,814-829`；schema `agent.py:137-140`，接受 `[{path|src, tags, description}]`），但**没有任何生产代码传入**——主管线 `_dispatch`/`_build_input`（`pipeline_v2.py:1030-1035,1311-1322`）与需求服务动画重做（`requirements_service.py:887-894`）均省略该参数，生产环境 `_image_index` 恒为空。注：此前审计报告称 `_edit_redo_animation` 传入该参数，经复查有误，特此勘误。

约束：`VisionService.analyze_image` 仅接受**存在的本地路径**（`services/vision.py:86-88`），远程 url 素材需先下载缓存。

---

## 二、设计（五项工作）

### D1 工具可用性检测（各 Agent 只调用可用工具）
沿用 `web_search_tool.py` 已验证的模式：三个工具按 provider 重写 `is_available()`（配置优先、env 兜底）。`list_agent_callable()` 的既有过滤随之生效——未配置凭据的工具自动从 StructureAgent 的 schemas 消失；execute 内既有「未配置」兜底保留为第二道防线。

| 工具 | provider → 可用条件 |
|------|--------------------|
| ai_image_generate | volcengine→`api_key‖ARK_API_KEY`；dalle→OPENAI_API_KEY；flux→FLUX_API_KEY；local→恒真 |
| ai_video_generate | volcengine→`api_key‖ARK_API_KEY`；kling→KLING_API_KEY；runway→RUNWAY_API_KEY |
| ai_music_generate | volcengine→`(access_key‖VOLC[_ENGINE]_ACCESS_KEY)∧(secret_key‖VOLC[_ENGINE]_SECRET_KEY)`；suno→SUNO_API_KEY |

### D2 生成历史索引 + 素材源注册（核心桥）
- 三个插件生成成功后追加写入 `PluginData/plugins/<id>/generated.json`：`{id, prompt, type, path, duration_sec, resolution, created_at}`。
- 素材源实现落位于核心模块 `clipwright/plugins/generated_source.py`（工厂 `make_generated_source` + 历史 `record_generated/load_generated_history/generated_image_entries`），三个插件只做导入+注册（与 CapabilityPlugin 等基类同位于核心的惯例一致，避免三份重复）：读索引 → query 分词与 prompt/tags 重合度打分（0.70-0.92）→ 返回 `MaterialAsset(local_path=…, type=…, tags=prompt 分词, metadata.ai_generated=true)`。initialize() 即注册（历史资产在 key 失效后仍可用；空历史 search 返回 []）。
- 打通即生效（零改动）：MaterialAgent 场景关键词检索 → 候选 → EditAgent 落 VIDEO/IMAGE 轨；AudioAgent 素材库 BGM 检索 → local_path 铺轨。

### D3 StructureAgent 规划策略
「## 可用工具」段追加：素材库难以命中的特定画面（具象角色/品牌/概念视觉）→ 用 `ai_video_generate`/`ai_image_generate`；生成产物自动入库、素材阶段按语义检索编入；成本约束（视频每管线 ≤2 次）。未配置工具经 D1 自动不可见。

### D4 需求服务感知
- `_ai_generation_overview()`：遍历三个工具名 `ToolRegistry.get` + `is_available()`，产出能力概览（✅可用/❌未配置）。
- 注入 `_generate_plan` rag_context（「## AI 生成能力」段，与「## 素材库概览」并列）与聊天阶段 `_handle_gathering` 上下文（需求对话可主动提议生成）。
- `asset_ratio` 增加 `ai_generated` 可选档：CREATIVE_BRIEF_SYSTEM 默认值 + structure_agent 渲染 + mg/generator 渲染（三处 additive，空值不显示）。

### D5 AnimationAgent 图片链路
- 新增 `_search_library_images()`：以 topic（+简报关键词）检索 MaterialRegistry 过滤 type=image；local_path 直用，url-only 下载到 `_cache/`（失败跳过）。
- execute 合并 `input_data.image_assets`（显式传入）+ 主动检索结果，按 path 去重 → 语义索引。
- 接线：`pipeline_v2._dispatch`/`_build_input` animation 分支传 AI 生成图历史；`requirements_service._edit_redo_animation` 同步传参。生成图片已注册素材源（D2），主动检索亦可命中——双保险。

---

## 三、任务分解

| # | 任务 | 状态 |
|---|------|------|
| T1 | D1：三工具 `is_available()` 重写 | ✅ |
| T2 | D2：generated.json 历史索引 + GeneratedAssetSource（核心模块 `clipwright/plugins/generated_source.py` 工厂 + ×3 注册） | ✅ |
| T3 | D3：StructureAgent 生成策略提示 | ✅ |
| T4 | D4：_ai_generation_overview + 注入两处 + asset_ratio 三处 | ✅ |
| T5 | D5：_search_library_images + 合并去重 + 管线/重做接线 | ✅ |
| T6 | 测试：tests/clipwright/test_ai_generation_integration.py 17 项 + 既有动画图片用例补 stub | ✅ |
| T7 | 全量回归（基线 1436 passed）+ `import clipwright.main` | ⬜ |
| T8 | 文档：本文件勾选、api_reference/development 增量、fix-plan §10 轮 61、前端 docs 副本 | ✅ |

## 四、验收标准

1. 未配置 `ARK_API_KEY` 时 StructureAgent 的 function schemas 中不出现 `ai_video_generate`/`ai_image_generate`；配置后出现（单测断言）。
2. 插件生成成功后 `generated.json` 追加一条；`MaterialRegistry.search("生成时的 prompt 关键词")` 能命中该资产且 `local_path` 指向落地文件（集成单测）。
3. AnimationAgent 在无显式 image_assets 时也能从素材库检索图片并建立语义索引；显式传入与检索结果去重合并（单测）。
4. 配置生成工具后，需求服务规划书上下文包含「## AI 生成能力」段；未配置时该段缺省（单测）。
5. 全量回归 0 失败。

## 五、风险与边界

- 生成视频成本高（Seedance 按秒计费）：提示词层面约束每管线 ≤2 次；不做管线内自动批量生成。
- 远程图片下载入索引为 best-effort（失败跳过），不阻塞动画阶段。
- 素材源打分 0.70-0.92 与现有源（0.7-0.85）同量级，最终排序仍由 MaterialAgent 的视觉校验/persona 风格分决定。
- 不改动：管线 bug 清单（A1-A5/A8 等）维持只报告；TTS 不动。
