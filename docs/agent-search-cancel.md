# Agent Search & Cancel 能力审计与交付文档

> 关联计划：`clipwright-web-search-and-cancel`（双仓库：后端 `J:\Clipwright` + 前端 `J:\Clipweight-Client`）
> 创建：2026-08-11。本文记录缺口、设计决策、修复清单（含提交 hash 回填），并作为实现期端点/请求头的权威记录。

---

## 一、缺口清单

| 编号 | 缺口 | 现状（已核查行号） | 交付目标 |
|------|------|--------------------|----------|
| G2 | 前端取消接线 | 后端已交付：`pipeline_v2.py` 协作式取消（`POST /cancel/{pipeline_id}` + `_CANCELLED` set + SSE `cancelled` 事件）；前端缺口：`pipeline.ts` 无 cancel、AgentPanel BottomBar 无停止按钮、agentStore 无取消态、SSE switch 无 `cancelled` case | 前端补全取消能力 |
| A1 | AudioAgent BGM 不检索素材库 | `audio_agent.py` L47 `bgm_slots`、L121-171 BGM 分配只依赖 LLM 情绪匹配/规则槽位，从不查 `MaterialRegistry` | 素材库检索 + 回退 |
| A2 | 需求阶段不感知素材库 | `requirements_agent._generate_brief` L121-126 user_context、`requirements_service._generate_plan` L1137 rag_context 均无素材库概览 | 注入素材库概览 |
| W1 | requirements chat 无联网搜索 | `requirements_service.py` L971-1000 `_handle_gathering` 只走 `llm.ask/structured_output`，无 web 工具 | chat 全轮次接入 web_search/web_fetch |
| W2 | 规划书生成无联网搜索 | `_generate_plan` L1066-1154 rag_context 注入点 L1137；`requirements_agent.translate_scenes` L157-197 | 规划书注入搜索结果上下文 |
| W3 | structure 脚本生成无联网搜索 | `structure_agent.py` L300-330 工具构建 + with_tools 调用；可用工具仅 list_animations/describe_llm_mg 等 | 追加 web_search/web_fetch 工具 |
| W5 | animation mg_dynamic 数据/事实无来源 | `animation_agent._handle_llm_mg` L593-660（description 提取 + generate 调用）；`generator.py` L109-171 `generate()` 签名无事实上下文 → LLM 编造数值 | 数据/事实类门控搜索 + `web_context` 注入 |
| C1 | QualityAgent 无 LLM 语义质检 | `quality_agent.py` L39-313（7 项检查，L181-215 视觉 LLM 门控先例）；`pipeline_v2.py` L878-881 quality input 已透传 `constraints` | 新增第 8 项 LLM 语义质检（门控） |

全局核查：后端全仓库 grep `web_search`/`bocha`/`baidu` **0 命中** — 无任何联网搜索能力。

---

## 二、设计决策

1. **web_search 实现形态**：OpenAI 兼容 function-call 语义（`with_tools` 循环 + `web_search`/`web_fetch` 工具 schema），但**不引入 OpenAI SDK**——搜索本体是自建 REST 调用（Bocha/百度都是标准 HTTP JSON API），LLM 只决定"何时搜/搜什么"，执行由 `WebSearchService` 完成。
2. **provider 可插拔**：`settings.web_search_provider` 选择 Bocha（主）/百度（备）；Bocha 失败自动尝试百度；两 provider 端点/鉴权差异封装在 `web_search.py` 内部，对外只有统一 `search(query) -> list[dict]`。
3. **门控与降级（硬验收标准）**：所有接入点（W1-W5、A1、C1、G2）在"未配置/失败/无结果"时**必须与现状逐字节一致**——每个接入点都有测试断言零变化路径。
4. **HTTP 客户端**：后端已核查用 `httpx`（`pyproject.toml` L19 `httpx>=0.27.0`，7 个文件在用）。`WebSearchService`/`WebFetchService` 一律用 `httpx.AsyncClient`，**不新增依赖**。
5. **C1 门控**：`enable_semantic_qa` 放 persona constraints（后端默认 False），前端暂不暴露开关（本期后端能力 + 测试即可）。

### .env 变量表

| 变量 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `ENABLE_WEB_SEARCH` | bool | `false` | 总开关；false 时搜索功能完全关闭（= 现状） |
| `WEB_SEARCH_PROVIDER` | Literal[bocha,baidu] | `bocha` | 主 provider |
| `WEB_SEARCH_API_KEY` | str | `""` | provider key（Bocha Bearer / 百度 bce-v3/ALTAK）；空时未配置 |
| `WEB_SEARCH_BASE_URL` | Optional[str] | `None` | 覆盖 provider 默认端点 |
| `WEB_SEARCH_TIMEOUT` | int | `15` | 请求超时（秒） |
| `WEB_SEARCH_MAX_RESULTS` | int | `5` | 默认返回条数 |

### 门控信号汇总

| 接入点 | 门控 | 零变化条件 |
|--------|------|-----------|
| W1 chat | `WebSearchService.is_configured()` | False → 走原 `llm.ask/structured_output` |
| W2 plan | `is_configured()` + 搜索有结果 | 空/失败 → 无 web_context 段落 |
| W3 structure | `is_configured()` | False → tools 列表不含 web 工具（与现状一致） |
| W5 animation | 正则命中数据/事实类 + `is_configured()` | 未命中/未配置 → `web_context=""` |
| A1 BGM | `MaterialRegistry.list()` 非空 + 有 AUDIO 结果 | 无素材源/无结果 → 回退 bgm_slots |
| A2 overview | `MaterialRegistry.list()` 非空 | 空 → 无额外段落 |
| C1 semantic | `constraints["enable_semantic_qa"]` | False → 完全现状 |
| G2 cancel | 后端 `POST /cancel` + 前端停止按钮 | 失败 → 不关 SSE，仅日志 |

### 端点与请求头（实现期核对记录）

- **Bocha**：默认端点 `https://api.bochaai.com/v1/web-search`（POST JSON）。Header：`Authorization: Bearer {key}`、`Content-Type: application/json`。Body：`{query, count, freshness}`（freshness 默认 `oneMonth`）。返回 `data.webPages.value[]` → 归一化为 `{title, url, snippet, score}`。⚠️ 实现时以官方文档为准；端点不可考时以 `settings.web_search_base_url` 覆盖。
- **百度（千帆 v3 AI Search Web Search，已实测 200）**：端点 `POST https://qianfan.baidubce.com/v2/ai_search/web_search`。认证 `Authorization: Bearer bce-v3/ALTAK-{ak}`（`settings.web_search_api_key` 值本身已含 `bce-v3/ALTAK-` 前缀，缺失时代码自动补）。配套 Header：`Content-Type: application/json`、`X-Request-Id: <uuid>`。Body：`{messages: [{role: "user", content: query}], search_source: "baidu_search_v2", resource_type_filter: [{type: "web", top_k: N}]}`。返回结果数组在顶层 `references[]`（每项含 `title` / `url` / `content`）→ 归一化为 `{title, url, snippet, score}`。⚠️ 旧端点 `/v3/websearch` 与旧 Header `X-Bce-Request-Id` 已废弃（404）。**代码不硬编码 key**（测试凭证仅进 `.env`，F4 用 grep 校验 `bce-v3/ALTAK` 不出现在代码/提交）。

---

## 三、修复清单（提交 hash 回填）

### 后端 `J:\Clipwright`

| # | 文件 | 变更 | 提交 |
|---|------|------|------|
| B1 | `clipwright/config.py` | 新增 web search 配置组（`enable_web_search` 等 6 字段 + `.env` 变量） | `39d25af` |
| B2 | `clipwright/services/web_search.py`（新） | `WebSearchService`（Bocha 主 + 百度备，可插拔）+ `WebFetchService` + 模块级单例 | `f49af24` |
| B3 | `clipwright/tool/web_search_tool.py`（新）+ `tool/__init__.py` | `WebSearchTool`/`WebFetchTool` 注册到 ToolRegistry | `465a6ab` |
| B4 | `clipwright/services/requirements_service.py` | W1 chat 全轮次 `with_tools` 接入 | `5bf2de1` |
| B5 | `clipwright/services/requirements_service.py` + `agents/requirements_agent.py` | W2 规划书生成注入 web_context | `9bba15c` |
| B6 | `clipwright/agents/structure_agent.py` | W3 脚本生成追加 web 工具（动态收集已自动包含，测试锁定） | `549acb0` |
| B7 | `clipwright/agents/animation_agent.py` + `animation/mg/generator.py` | W5 数据/事实类门控搜索 + `web_context` 参数 | `7b38bee` |
| B8 | `clipwright/agents/audio_agent.py` | A1 BGM 素材库检索 + 回退 | `855c691` |
| B9 | `clipwright/agents/requirements_agent.py` + `services/requirements_service.py` | A2 素材库概览注入 | `bb64b11` |
| B10 | `clipwright/agents/quality_agent.py` | C1 LLM 语义质检（`enable_semantic_qa` 门控） | `7f4ca0f` |

### 前端 `J:\Clipweight-Client`

| # | 文件 | 变更 | 提交 |
|---|------|------|------|
| F1 | `src/services/api/pipeline.ts` | 新增 `cancel(pipelineId)` | `eeaa9f1` |
| F2 | `src/stores/agentStore.ts` | `cancelling` 状态 + `setCancelling` + resetPipeline 复位 | `858c4dc` |
| F3 | `src/features/agent/AgentPanel.tsx` + `src/types/pipeline.ts` | 停止按钮 + SSE `cancelled` 处理 + `PipelineSSEEventType` 增 `cancelled` | `22052256` |
| F4 | `docs/frontend-backend-parity.md` | parity 表同步 cancel 端点、移除 regenerate-scene | `4ecf4d2` |

> 审计文档本身：`dfede83`。全部 10 后端 + 4 前端提交已核实存在（F1 验证通过）。
