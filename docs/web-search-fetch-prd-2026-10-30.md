# Web Search / Fetch 工具化升级 PRD

- **项目**：Clipwright / 帧艺
- **分支**：`feat/master-agent`
- **日期**：2026-10-30
- **文档目的**：核查动画 Agent 当前联网能力，并定义可供主 Agent、阶段 Agent 与动画管线复用的新 `web_search` / `web_fetch` 工具。

## 1. 结论摘要

动画 Agent 当前具备“受门控的搜索摘要注入”能力，不具备“把网页正文或搜索结果直接变成动画素材”的完整能力。

| 能力 | 当前状态 | 代码证据 | 结论 |
|---|---|---|---|
| 动画 Agent 搜索网络 | 半 | `clipwright/agents/animation_agent.py:1137-1149`、`:752-760`：命中 `_DATA_FACT_RE` 后直接实例化 `WebSearchService`，最多取 3 条结果 | 只在数据/事实关键词命中时搜索；不是任意动画请求都搜索 |
| 搜索结果进入 MG 生成 | 有（摘要参考） | `animation_agent.py:1146-1149`、`clipwright/animation/mg/generator.py:1357-1363` | 以标题、摘要、来源 URL 拼成 `web_context`，注入 LLM prompt；不是结构化事实对象 |
| 抓取网页正文 | 无（动画调用链） | `animation_agent.py:752-784`、`:1137-1180` 只有 `WebSearchService.search()`，没有 `WebFetchService.fetch()` | 服务和工具本身存在，但 AnimationAgent 当前未调用正文抓取 |
| Agent 通过 ToolRegistry 调用搜索 | 半 | `clipwright/tool/__init__.py:52,71-144` 注册 `WebSearchTool` / `WebFetchTool`；`web_search_tool.py:25-49`、`:100-116` 标记 `agent_callable=True`；动画 Agent却直接调 Service | 主 Agent surface 可以看到工具；动画阶段的业务路径仍是硬编码 Service 调用 |
| 搜索结果作为图片素材 | 无（网页搜索链路）/ 有（独立素材库链路） | `animation_agent.py:1242-1281` 只从 `MaterialRegistry` 查图片并落地/缓存；`:1287-1294` 下载远程图片 | 搜索结果 URL 不会自动进入 `materials/`，也不会作为网页候选素材 |
| 搜索结果作为视频素材 | 无（网页搜索链路） | `animation_agent.py:1137-1180` 无下载或 `video_download`；素材搜索由 `MaterialRegistry` 负责 | 需独立素材源插件和下载/许可登记 |
| 搜索结果作为引用图表 | 半 | `animation_agent.py:1141-1149` + `generator.py:1357-1363` 只提供事实参考文本 | LLM 可据摘要设计数值动画，但系统没有数据集、图表资产、引用绑定或可复核快照 |
| 未配置 key 的可见行为 | 半 | `web_search.py:56-63,72-73` 返回 False/空列表；`animation_agent.py:1138-1153` 空 `web_context`；`web_search_tool.py:63-70` 返回 `DEPENDENCY_MISSING` | 动画直调路径静默跳过且未调用 `_note`；主 Agent 工具调用可看到结构化缺依赖结果 |
| 百度依赖 | 半 | `web_search.py:75-101` 主 provider + 另一 provider fallback；`:146-179` Baidu 端点与 Bearer `bce-v3/ALTAK-` 鉴权；`config.py:357-383` provider 仅 `bocha|baidu` | 默认主 provider 是 Bocha，失败回退 Baidu；不是“依赖百度”，但当前唯一 fallback 是百度 |

## 2. 本次核查证据

### 2.1 `WebSearchService`：现有基础设施

`clipwright/services/web_search.py:42-50` 声明服务支持 Bocha 主 / Baidu 备，主 provider 失败后回退另一 provider，全部失败返回空列表且不抛异常。

`web_search.py:56-63` 的 `is_configured()` 同时要求 `enable_web_search=True` 和 `web_search_api_key` 非空。`web_search.py:65-96` 将结果归一化为 `{title, url, snippet, score}`，最多 50 条，失败记录 warning 后返回空列表。

Bocha 调用见 `web_search.py:105-120`：POST JSON `{query,count,freshness}`，`Authorization: Bearer <api_key>`。百度调用见 `web_search.py:148-179`：POST 千帆 AI Search 端点，Authorization 为 `Bearer bce-v3/ALTAK-<ak>`，并发送 UUID `X-Request-Id`。

当前 provider 配置只允许 `bocha` 或 `baidu`，且只有一组 `web_search_api_key` 与一组 `web_search_base_url`，见 `config.py:357-383`。因此：

1. 默认 `web_search_provider` 是 `bocha`；
2. 设置为 `baidu` 时 Baidu 为主、Bocha 为 fallback；
3. 主 provider 失败才尝试另一家；
4. 两家当前共用一个 API key；
5. 自定义 `WEB_SEARCH_BASE_URL` 时两家也共用该 endpoint，这可能导致 fallback 指向错误的 provider，应在新设计中拆成 provider-specific endpoint/key。

`WebFetchService.fetch()` 见 `web_search.py:252-289`：使用 `httpx.AsyncClient` GET，跟随重定向；只接受 `text/html` / `text/plain`；剥离 `script/style`、HTML 标签、实体并压缩空白，默认截断到 4000 字符。它不使用 readability/trafilatura，不提取图片/视频候选，也不落盘。

### 2.2 AnimationAgent：实际调用点

逻辑动画类型化生成路径在 `animation_agent.py:743-790`。数据/事实门控为 `_DATA_FACT_RE`（定义于 `:38-43`）。命中后 `:753-760` 调用 `WebSearchService.search(text_content[:120])`，取前 3 条，仅拼接标题和摘要。

LLM MG 路径在 `animation_agent.py:1137-1180`。注释明确写出门控和“任何异常/空结果一律静默回退 `web_context=""`”。`1143-1149` 只调用搜索并拼接标题、摘要、来源 URL，未调用 `WebFetchService`，未下载图片/视频。

MG 生成器的 `web_context` 参数见 `animation/mg/generator.py:212-245`，非空时在 `:274-285` 传给 LLM。`_build_context_section()` 位于 `generator.py:1217-1228`，实际消费位于 `:1357-1365`：添加“事实参考（来自联网搜索，动画中的数据/数值须以此为据）”文本段落。这里没有事实字段、来源对象、引用 ID、抓取快照或素材引用。

### 2.3 工具注册、surface 与权限

`clipwright/tool/__init__.py:52` 导入两个工具，`:71-144` 在 `register_builtin_tools()` 中注册 `WebSearchTool()` 与 `WebFetchTool()`。

`tool/web_search_tool.py:25-49` 与 `:100-116` 显示两者均为 `agent_callable=True`，且本体 `is_available()` 恒为 True。未配置时 `WebSearchTool.execute()` 在 `:63-70` 返回 `ToolStatus.DEPENDENCY_MISSING` 和空结果；成功时 `:71-89` 返回归一化结果。`WebFetchTool.execute()` 在 `:118-142` 返回 `{content}`，失败或空内容同样为 `DEPENDENCY_MISSING`。

`ToolRegistry.list_agent_callable()` 见 `tool/registry.py:141-166`：如果工具有 `is_configured()` 且为 False，则从阶段 Agent 工具列表排除；注释 `:147-150` 明确说明主 Agent Kernel surface 仍可见该工具，调用时如实返回未配置结果。

`services/agent_run.py:22-30` 的 `READONLY_TOOLS` 已包含 `web_search`、`web_fetch`，说明只读研究子 Agent 的运行时白名单已预留这两个名字。`services/capability_matrix.py:99-108` 的 `TIER_MAP` 已将两者归为 `costly`，原因是外网配额/成本。`tool/surface.py:239-291` 按 capability matrix 构建 Kernel surface；dangerous 不进入直接工具面。

但 `clipwright/tool/capabilities.py:26-62` 的 `CAPABILITY_ALIASES` 当前没有 `web_search` / `web_fetch` 语义能力别名，因此业务 Agent 仍不能按稳定能力名解析这两个工具；它们现在主要是工具名直连和 surface 条目。

### 2.4 降级与可见性

`services/degradation.py:30-47` 提供 `note_degradation()`；没有活跃 scope 时会 warning，并明确提示归因会丢失。动画 Agent虽然导入了 `_note`（`animation_agent.py:33`），但联网搜索的 `:751-762` 与 `:1140-1153` 分支没有调用 `_note`。因此未配置 key、空结果、异常都会使动画继续生成空 `web_context`，该原因不会进入动画阶段的结构化 degradation 列表。

MG 生成器自身对 LLM JSON 空结果、校验失败、fallback 等会调用 `_note`，见 `animation/mg/generator.py:26-39`、`:1367-1372`；这与 web 搜索跳过是两条不同的链路，不能把 MG fallback 记录误认为 web 降级已记录。

### 2.5 素材源与动画图片链路

`material_agent.py:13` 引入 `MaterialRegistry`。其搜索结果字段包含 `local_path`、`license`、`license_url`、`author`、`source_url`，见 `material_agent.py:152-172` 以及 `:1149-1173`。动画图片索引路径见 `animation_agent.py:1242-1281`：只检索 `MaterialRegistry` 的 `image` 类型；已有 `local_path` 直接使用，只有 URL-only 素材才通过 `:1287-1294` 的 `_cache_remote_image()` 下载到 `_cache/`，并经 `assert_public_url` 做 SSRF 校验。

这证明项目已有独立的“素材源→本地路径→语义索引→MG image 元素”链路，但网页搜索结果没有接入该链路，也没有按用户工作区 `materials/` 记录来源与许可。

## 3. 与 DeepSeek harness search/fetch 的差距 Top 5

1. **工具化边界不足**：DeepSeek harness 可在 Agent 回合中按需调用 search/fetch；Clipwright 的动画主路径绕过 ToolRegistry 直接调用 Service，且只针对数据关键词门控。新设计应让主 Agent、research、structure/material/edit/animation/audio/quality 通过统一 capability 解析调用。
2. **provider 与鉴权抽象不足**：当前只有 Bocha/Baidu 两个 provider，共用一套 key/endpoint；没有 Tavily、Serper、Bing、本地 Playwright，也没有 provider-specific 配置、健康状态和成本路由。
3. **正文抽取能力不足**：现有 `WebFetchService` 只是标签清洗和字符截断，动画未调用；没有 readability/trafilatura、主文识别、正文/标题/作者/发布日期/引用段落结构化，也没有抓取快照。
4. **结果不能结构化复用**：现状只把最多三条标题/摘要文本拼进 prompt；没有 evidence/citation ID、事实声明、可信度、来源类型、抓取时间、内容 hash、数据表格和图表生成输入。
5. **素材与可观测闭环不足**：搜索 URL 不会自动落盘到 `materials/`，没有图片/视频候选提取、许可元数据、下载完整性校验、来源回链；web 未配置/失败也没有 `note_degradation`、`subagent_tool`、provider、耗时、费用归因。

## 4. 背景与问题

用户需要判断“动画 Agent 能否自行联网搜索/抓取网页并把结果用到动画里”。当前系统实际分成三块：

- `WebSearchService` / `WebFetchService` 提供底层网络能力；
- `WebSearchTool` / `WebFetchTool` 已注册为 Agent 工具，主 Agent surface 可见；
- AnimationAgent 仍直接调用搜索 Service，仅把搜索摘要作为 MG prompt 的事实参考。

这造成能力认知与实际效果不一致：工具看起来存在，但动画阶段没有正文深读、没有网页素材下载，也没有引用图表资产；未配置 key 时动画继续执行，主 Agent 只在真正走工具调用时才收到结构化缺依赖状态。

## 5. 目标

1. 建立统一、可观测、可配置 provider 的 `web_search` 与 `web_fetch` 工具契约。
2. 让任意具备权限的 Agent 通过 ToolRegistry / `surface` / capability 名调用，而非硬编码 Service。
3. 提供结构化搜索结果、正文证据、引用 ID、抓取时间和内容 hash。
4. 支持正文抽取去噪，保留可追溯的原始 URL 与来源元数据。
5. 支持从网页中识别图片/视频候选，按权限下载到工作区 `materials/`，写入来源和许可记录。
6. 让动画 Agent可将真实数据转成可复核的图表/数据动画输入，并能引用本地素材资产。
7. 记录 provider、耗时、字数、缓存命中、成本、失败与降级原因，供主 Agent和 pipeline trace 观察。

## 6. 非目标

- 不在本 PRD 内实现通用搜索引擎或自建网页索引。
- 不默认绕过登录、验证码、付费墙或 robots 规则。
- 不把任意网页文本直接当作可信事实；事实仍需保留来源和证据片段。
- 不让网页抓取工具直接执行网页中的脚本或命令。
- 不把所有网页图片/视频自动加入成片；下载、许可和语义匹配必须是显式策略。
- 不替代现有 `MaterialRegistry`；新工具通过适配器向其提供候选资产。

## 7. 工具契约

### 7.1 `web_search`

**调用形式**：`ToolRegistry.execute("web_search", ...)`，Agent 通过 capability `web_search` 解析到具体实现。

**Schema**：

```json
{
  "query": "string, required, 1-1000 chars",
  "provider": "string|null, optional",
  "max_results": "integer, optional, 1-50",
  "freshness": "string|null, optional",
  "domains": ["string"],
  "safe_search": "boolean, default true",
  "include_snippet": "boolean, default true",
  "include_images": "boolean, default false",
  "include_videos": "boolean, default false",
  "idempotency_key": "string|null, optional",
  "timeout_sec": "number|null, optional"
}
```

**成功返回**：

```json
{
  "status": "success|degraded|dependency_missing|rate_limited|timeout|invalid_request",
  "query": "...",
  "provider": "bocha",
  "results": [
    {
      "result_id": "srch_<stable hash>",
      "rank": 1,
      "title": "...",
      "url": "https://...",
      "canonical_url": "https://...",
      "snippet": "...",
      "published_at": "ISO-8601|null",
      "source_name": "...",
      "content_type": "html|image|video|pdf|unknown",
      "score": 0.0,
      "retrieved_at": "ISO-8601",
      "provider_raw_id": "...|null"
    }
  ],
  "trace": {
    "request_id": "...",
    "cache_hit": false,
    "attempts": ["bocha", "baidu"],
    "duration_ms": 0,
    "estimated_cost_usd": 0.0
  }
}
```

### 7.2 `web_fetch`

**Schema**：

```json
{
  "url": "string, required, http/https",
  "extract_mode": "article|text|metadata|links|media_candidates, default article",
  "max_chars": "integer, optional, 1000-50000",
  "include_images": "boolean, default false",
  "include_videos": "boolean, default false",
  "download_media": "boolean, default false",
  "workspace_id": "string|null, required when download_media=true",
  "allowed_domains": ["string"],
  "idempotency_key": "string|null, optional",
  "timeout_sec": "number|null, optional"
}
```

**成功返回**：

```json
{
  "status": "success|degraded|dependency_missing|blocked|timeout|too_large|invalid_request",
  "url": "...",
  "canonical_url": "...",
  "title": "...",
  "author": "...|null",
  "published_at": "ISO-8601|null",
  "content": "clean article text",
  "content_hash": "sha256:...",
  "evidence": [
    {"evidence_id": "ev_...", "text": "...", "start": 0, "end": 1200}
  ],
  "links": [{"url": "...", "text": "...", "rel": "..."}],
  "media_candidates": [
    {
      "media_id": "media_...",
      "kind": "image|video",
      "url": "...",
      "poster_url": "...|null",
      "mime": "image/jpeg|video/mp4|null",
      "width": 0,
      "height": 0,
      "license": "...|unknown",
      "license_url": "...|null",
      "attribution": "...|null"
    }
  ],
  "materials": [
    {
      "asset_id": "asset_...",
      "local_path": "materials/web/<sha256>.<ext>",
      "source_url": "...",
      "license": "...",
      "license_url": "...|null",
      "author": "...|null",
      "content_hash": "sha256:..."
    }
  ],
  "trace": {
    "request_id": "...",
    "provider": "tavily|playwright|...",
    "cache_hit": false,
    "duration_ms": 0,
    "bytes_read": 0,
    "estimated_cost_usd": 0.0
  }
}
```

### 7.3 错误码与状态

| 状态/错误 | 触发条件 | Agent 行为 |
|---|---|---|
| `INVALID_REQUEST` | URL、query、数量或 workspace 参数不合法 | 不重试，提示修正参数 |
| `DEPENDENCY_MISSING` | key、provider 或本地 Playwright 不可用 | 可转本地缓存/其他 provider，并登记降级 |
| `PROVIDER_AUTH_FAILED` | provider 401/403 | 标记 provider unhealthy，按策略回退 |
| `PROVIDER_BAD_RESPONSE` | 非法 JSON/缺少字段 | 回退并保留响应摘要 |
| `RATE_LIMITED` | 429、配额耗尽 | 遵循 Retry-After，超过预算则降级 |
| `TIMEOUT` | 超过单请求 timeout | 最多一次 retry，随后回退 |
| `BLOCKED_ROBOTS` | robots 或域名策略禁止 | 不绕过，返回 blocked |
| `SSRF_BLOCKED` | 非公网地址、DNS rebinding、危险端口 | 直接阻断并记审计事件 |
| `CONTENT_TOO_LARGE` | 响应体/正文超上限 | 截断并返回 `degraded`，记录原始大小 |
| `EXTRACT_EMPTY` | 页面可达但正文为空 | 尝试 `text`/Playwright fallback，最终返回空证据 |
| `MEDIA_LICENSE_UNKNOWN` | 候选没有可验证许可 | 可展示候选但默认不落盘进成片候选 |

### 7.4 超时、限流、幂等

- 默认搜索 15 秒、抓取 20 秒、单页面读取上限 5 MB；均可按 provider 和调用覆盖，但不得超过全局硬上限。
- 每个 provider 维护并发 semaphore、令牌桶和 Retry-After 处理；系统级 `rate_limit_*` 仍是总闸。
- provider 失败只做一次指数退避 retry；429 只遵循服务端退避窗口，不忙等。
- `idempotency_key` 与规范化请求体 hash 组成缓存键；同 workspace、同 URL、同抽取策略在 TTL 内重放，不重复下载。
- 搜索结果缓存默认 TTL 10 分钟；正文缓存 1 小时；素材文件按 URL+ETag/Last-Modified+内容 hash 去重。
- 幂等重放返回原 `request_id` 与 `cache_hit=true`，不重复产生 `subagent_tool` 成本事件。

## 8. Provider 抽象与回退

### 8.1 统一 Provider 接口

```python
class WebProvider(Protocol):
    name: str
    async def search(self, request: SearchRequest) -> ProviderSearchResponse: ...
    async def fetch(self, request: FetchRequest) -> ProviderFetchResponse: ...
    def health(self) -> ProviderHealth: ...
```

Provider adapter 负责鉴权、请求格式、原始响应解析和 provider-specific 限流；上层负责规范化、缓存、证据、来源与降级。

### 8.2 支持列表

- `bocha`：现有默认中文/通用搜索适配器。
- `baidu`：现有千帆 AI Search 适配器。
- `tavily`：优先提供带摘要/正文的研究搜索。
- `serper`：Google 结果兼容适配器。
- `bing`：Bing Web Search 适配器。
- `playwright_local`：本地 Playwright 抓取，主要用于 JS 渲染页面；不作为通用搜索引擎。

配置改为 provider-specific：`web_search_providers.<name>.api_key`、`base_url`、`timeout_sec`、`enabled`、`priority`、`rate_limit`。保留旧 `WEB_SEARCH_PROVIDER` 作为主 provider 兼容配置。

### 8.3 失败回退顺序

默认顺序：

```text
主 provider → 配置中的备用 provider 列表 → playwright_local（仅 fetch/JS 页面）→ 缓存 → 空结果 + degradation
```

搜索和抓取使用独立链路：搜索失败不能自动把 query 当 URL；fetch 失败可以按页面策略切换静态 HTTP → readability/trafilatura → Playwright。回退顺序可配置为 `provider_fallback_order`，并按错误类型配置是否回退：认证错误默认暂时熔断该 provider，参数错误不回退。

## 9. 正文抽取、媒体候选与落盘

1. HTTP 层先做 URL 规范化、响应头判断和最大字节限制。
2. `article` 模式优先使用 `trafilatura` 或 `readability-lxml` 提取主文；保留 title、author、date、canonical、引用链接。
3. `text` 模式允许纯文本/HTML 清洗；保留现有正则清洗作为无依赖 fallback。
4. 删除导航、页脚、广告、脚本、样式、重复段落；正文过长按段落边界截断，不能只做字符硬切而丢失 evidence 起止位置。
5. `media_candidates` 从 `img/src/srcset/picture`、`video/source/og:video`、JSON-LD 和 OpenGraph 提取；只返回候选 URL，不等于自动使用。
6. `download_media=true` 且调用者有 `write_local`/素材权限时，下载到 `<workspace>/materials/web/<sha256>.<ext>`，不得写 `_cache/` 代替用户素材目录。
7. 下载前执行 MIME、扩展名、大小、媒体解码探测和 `assert_public_url`；拒绝私网、loopback、link-local、非标准危险端口和 DNS rebinding。
8. 每个落盘资产必须写 sidecar 或资产索引：`asset_id`、`source_url`、`canonical_url`、`retrieved_at`、`content_hash`、`license`、`license_url`、`author`、`attribution`、`provider`、`download_status`。
9. 许可未知时仍可保留研究候选，但默认 `license_ok=false`，AnimationAgent 不得自动将其放进成片；用户确认后才进入可用素材候选。
10. 通过 `MaterialRegistry` 注册 web material source，使现有 `local_path`、`source_url`、`license` 字段和图片语义索引能够复用。

## 10. Agent 接入面

| Agent/层 | 用法 |
|---|---|
| 主 Agent | 通过 `surface` 使用 `web_search`/`web_fetch`；先 search，再按结果选择 fetch；看到 provider、成本、降级和 evidence；需要下载时显式请求 `download_media` |
| research | 通过 `READONLY_TOOLS` 仅搜索/抓取/读取，禁止下载；输出带 evidence ID 的研究报告 |
| structure | 搜索主题背景、定义、时间线和可引用来源；只写结构化事实卡，不直接生成素材 |
| material | 搜索图片/视频候选或调用 `MaterialRegistry` web source；在获得写入权限和许可可接受时下载到 `materials/`，输出候选排序与来源 |
| edit | 读取已落盘 `asset_id`、许可和来源，不直接抓网页；将素材放入时间线时保留来源引用 |
| animation | 对“统计/年份/百分比/GDP/报告”等需求调用 search；对高价值来源调用 fetch；从 evidence 生成 `data_series`/`citation_refs`，再交给 MG 图表模板；需要图片时只选择 `license_ok=true` 的本地 `materials/` 资产 |
| audio | 默认不联网；需要播报事实时读取 research evidence，不自行抓取；音频引用应回链到 evidence |
| quality | 检查图表数值是否来自 evidence、引用是否存在、媒体 asset 是否有来源/许可、是否使用了未授权候选；失败时发出 `note_degradation` |

### 10.1 “动画引用真实数据/图表”场景

1. AnimationAgent 以结构化 query 调 `web_search`，得到 `result_id`。
2. 对候选来源调用 `web_fetch(extract_mode="article")`，得到 `content_hash` 与 evidence 段落。
3. 从 evidence 中提取或由 research Agent 产出经确认的 `data_series`：`[{label,value,unit,period,source_evidence_id}]`。
4. MGGenerator 接收 `web_evidence` 与 `data_series`，生成 `data_bars`/`counter_up`/`timeline_progress` 等模板输入。
5. 每个图表 clip 的 metadata 写入 `citation_refs`，成片导出时可生成来源列表；数值缺证据时不得伪造，改走无数值动画或登记 degradation。

## 11. 可观测性与降级

### 11.1 `subagent_tool` 事件

每次 tool 调用产生：

```json
{
  "event": "subagent_tool",
  "tool": "web_search",
  "capability": "web_search",
  "agent": "animation_agent",
  "pipeline_id": "...",
  "request_id": "...",
  "provider": "bocha",
  "status": "success|degraded|failed",
  "duration_ms": 1234,
  "input_chars": 120,
  "output_chars": 3200,
  "result_count": 3,
  "cache_hit": false,
  "estimated_cost_usd": 0.0,
  "fallback_from": null
}
```

不得记录 API key、Authorization header、完整 cookie 或未经清洗的敏感页面内容。trace 只保留 URL、域名、hash 和截断摘要。

### 11.2 成本/耗时归因

- provider、Agent、stage、pipeline、workspace、request_id 五维归因；
- `duration_ms` 分解为 DNS/连接/TTFB/读取/抽取/下载；
- `estimated_cost_usd` 来自 provider 单价与调用量，免费 provider 记 0 但仍记配额消耗；
- cache hit 记 0 新增 provider 成本；
- `web_fetch` 下载媒体的字节数和磁盘占用单独记录。

### 11.3 `note_degradation`

以下情况必须登记：`web_not_configured`、`provider_auth_failed`、`provider_fallback`、`web_timeout`、`robots_blocked`、`extract_empty`、`media_license_unknown`、`media_download_failed`、`evidence_missing`、`chart_data_unverified`。登记内容至少包含 `kind`、`detail`、`scope`、`impact`，并通过阶段 `degradations` 进入 AgentResult，主 Agent 可见。

## 12. capability matrix / ToolRegistry / surface 接线

1. 在 `clipwright/tool/capabilities.py` 增加稳定能力名：`web_search`、`web_fetch`、可选 `web_media_candidates`、`web_material_download`。
2. `WebSearchTool` 和 `WebFetchTool` 设置 `capability`，provider adapter 不直接暴露给 Agent。
3. 在 `capability_matrix.TIER_MAP` 中：
   - `web_search`: `costly`；
   - `web_fetch`: `costly`；
   - `web_media_candidates`: `costly`；
   - `web_material_download`: `write_local` 或 `dangerous`（按是否可访问外部资源和部署策略裁定，默认需显式确认）。
4. `ToolRegistry` 注册时做 schema、capability 和 provider health 检查；`list_agent_callable()` 仍可按阶段配置过滤，但主 Agent surface 必须保留工具并返回结构化 `DEPENDENCY_MISSING`。
5. `surface` 仅纳入 `read`、`write_local`、`costly`；危险下载通过 ExecutionGateway/显式授权路径，不直接进入默认 Kernel 工具面。
6. `agent_run.READONLY_TOOLS` 保留 `web_search`、`web_fetch`；research 禁止 `web_material_download`。
7. AnimationAgent 删除直接 `WebSearchService()` 调用，改为 `resolve_tool("web_search")`/`call_tool`；调用记录统一进入 `subagent_tool`，且保留当前 `_DATA_FACT_RE` 作为优化门控，而不是能力边界。
8. MGGenerator 接受结构化 `web_evidence`、`data_series`、`citation_refs`；`web_context` 字符串保留兼容期，逐步废弃。

## 13. 安全与治理

- **域名白名单/黑名单**：全局 deny 优先于 allow；workspace 可追加 allow；默认只允许 `http`/`https`。
- **robots**：抓取前读取 robots.txt 并缓存；禁止路径返回 `BLOCKED_ROBOTS`，不通过 Playwright 绕过。
- **内容上限**：单响应 5 MB、正文 50,000 字符、单次最多 20 个媒体候选、单任务下载总量和并发均受 workspace 配置限制。
- **SSRF**：解析域名并校验 DNS 结果，阻断 RFC1918、loopback、link-local、metadata 地址、危险端口、重定向跨策略域；每跳重新校验。
- **缓存/TTL**：搜索 10 分钟、正文 1 小时、robots 24 小时、素材永久按 hash 去重但可按来源策略清理。
- **内容安全**：网页内容进入 LLM 前标注为不可信外部数据；过滤控制字符并限制 prompt 注入影响；网页不能改变工具权限或系统提示。
- **许可治理**：未知许可不得标记为 `license_ok`；素材清单必须保留原始来源、作者、许可 URL 和下载时间。
- **审计**：保留 request_id、provider、域名、状态、hash、降级和人工确认记录；敏感 header 永不落盘。

## 14. 里程碑与验收

### M1：统一工具契约与可观测搜索/抓取

**范围**：provider adapter 接口；Bocha/Baidu 迁移；Tavily/Serper/Bing 配置骨架；结构化 search/fetch 返回；capability、TIER_MAP、surface、ToolRegistry 接线；`subagent_tool` 与 degradation。

**可测断言**：

- `ToolRegistry.get("web_search")` 与 `get("web_fetch")` 存在且 schema 可编译；
- 主 Agent surface 包含两工具，阶段 Agent 未配置时不暴露但直接执行返回 `DEPENDENCY_MISSING`；
- provider 主备顺序可配置，主 provider 401/timeout 时 trace 含 fallback；
- 相同 `idempotency_key` 不产生第二次 provider 请求；
- 未配置 key 时产生 `web_not_configured` degradation，主 Agent 能在 AgentResult 看到；
- 每次调用生成一条 `subagent_tool`，不包含 secret；
- 旧 `WebSearchService.search()` 兼容测试继续通过。

### M2：正文抽取、证据和网页媒体候选

**范围**：trafilatura/readability；静态 HTML + Playwright fallback；evidence/canonical/content hash；图片/视频候选识别；robots、SSRF、大小限制、缓存。

**可测断言**：

- 对包含导航、脚本、广告和正文的 fixture，输出只含正文主要段落；
- evidence 的文本、字符偏移和 `content_hash` 可复核；
- 非 HTML、超大响应、超时、robots 禁止分别返回规定状态；
- 私网、loopback、metadata、DNS rebinding URL 被 `SSRF_BLOCKED`；
- JS fixture 在静态 HTTP 无正文时能按配置切到 Playwright；
- `include_images/videos=true` 能从 HTML/OpenGraph/JSON-LD 提取候选，且不执行页面脚本。

### M3：素材落盘与动画真实数据/引用图表闭环

**范围**：`materials/web/` 下载适配器、MaterialRegistry source、许可 sidecar、asset_id 引用、AnimationAgent capability 调用、结构化 `data_series`/`citation_refs`、QualityAgent 校验。

**可测断言**：

- 通过 `download_media=true` 下载的文件位于 workspace `materials/`，重复 URL/hash 不重复落盘；
- 每个资产均有 source URL、license、license URL/unknown、author、content hash；
- `license_ok=false` 资产不会自动进入 AnimationAgent 的 image 元素；
- 事实动画的每个数值都能追溯到 evidence ID；缺证据时生成无数值降级动画并记录 `chart_data_unverified`；
- MG clip metadata 含 `citation_refs`，QualityAgent 能发现缺失引用；
- 端到端 fixture 验证 search → fetch → data_series → MG chart → 来源记录。

## 15. 风险与回滚

| 风险 | 影响 | 缓解/回滚 |
|---|---|---|
| provider API 语义差异 | 结果解析、成本和质量漂移 | adapter contract fixture；按 provider feature flag 回退旧 Service |
| 抓取依赖增加 | 部署体积和启动失败 | trafilatura/readability 可选依赖；缺失时使用现有清洗器；Playwright 独立开关 |
| 外网调用变慢或费用上升 | pipeline 超时/预算超支 | 并发、超时、预算闸、缓存、fallback；设置 `web_tools_enabled=false` 即回退无 web_context |
| 网页提示注入 | 影响 LLM 生成 | 外部数据隔离标注、长度上限、字段化 evidence；不得执行网页指令 |
| 许可不完整 | 成片合规和归属风险 | `license_ok=false` 默认；QualityAgent 阻断自动入片；保留下载回滚和删除索引 |
| 旧测试依赖空字符串语义 | 回归失败 | M1 保留 Service 与 `web_context` 兼容层；灰度开关按 Agent/stage 生效 |
| 自定义 endpoint 造成 fallback 错配 | 搜索全部失败或误用 provider | 拆 provider-specific base URL/key；配置校验拒绝把一个 endpoint 同时用于不同 provider，兼容期发 warning |

**回滚开关**：`web_tools_enabled=false`、`web_media_download_enabled=false`、`animation_web_evidence=false`。关闭后保留本地缓存和已有素材，不删除用户资产；AnimationAgent回退到当前空 `web_context`/本地素材路径，并在关闭期间仍记录一次 `web_feature_disabled`。

## 16. 结论

当前动画 Agent 可以在数据/事实描述命中时联网搜索，并把最多三条标题/摘要（以及一条来源 URL）作为 MG 的事实参考；它当前不会抓取网页正文，也不会把网页搜索结果自动作为图片、视频或引用图表素材使用。`web_search` / `web_fetch` 已经是 ToolRegistry 工具并在 capability matrix 中定为 `costly`，但动画实际调用仍绕过工具面，且 provider 只有 Bocha/Baidu、共享 key/endpoint、缺少正文证据、素材落盘、许可和完整可观测链路。


## 执行记录（2026-10-30 · M3 收口）

本轮把三份 PRD 的 M3 全部落地，并跑通后端全量回归（4840 passed / 4 skipped / 1 xfailed）
与前端全量回归（128 files / 1107 tests，`tsc` 干净）。要点：

- **治理落地**：域名白名单/黑名单（`web_assets_allowed_domains` /
  `web_assets_denied_domains`，子域按后缀匹配、黑名单优先）、robots.txt 判定
  （`web_assets_respect_robots`，缓存**文本**按路径判定、最长匹配 Allow 胜出）、
  缓存 TTL（`web_assets_cache_ttl_hours`，同 URL 命中旁车直接复用并标 `cached=True`）。
- **工具面接线**：动画阶段的联网检索改走 `self.call_tool("web_search")`（进轨迹与
  `subagent_tool` 事件）；`web_fetch` 恒可用（不依赖搜索 key）。
- **接线测试**：7 个 stage agent 在联网开启时全部能解析 `web_search`/`web_fetch`；
  未配置时 `web_search` 按设计不出现在阶段 Agent 的工具面里。

## 执行记录（2026-10-30 · 第 9 轮 · M4 前端可见性 + M5c 需求会话问答 + 导出重试快照）

- **思考流与工具全部可见（§13 / M4 前端）**
  - `AgentPanel` 现在真的消费 SSE `reasoning` 帧（旧实现声明了 `liveReasoning` 却从不赋值，
    折叠条永远是空的）：逐块累积、每轮提问清空、正文已在流式时仍然可见（上限 20 万字）。
  - `ThinkingDisclosure` 改增量累积（旧实现每帧把全部片段重新 join 一遍，长任务 O(n²) 拖住主线程），
    首帧指纹变化或 seq 后退时整体重建（不会把上一轮思考挂到新任务上），到 40 万字上限如实说明停止追加。
  - `ToolCallRows` 三路合并（`tool_call`/`tool_result` 按 causation 配对 + `subagent_tool` + Skill），
    并**删除 `slice(-8)`**：主 agent / 子 agent / 技能各有来源筛选与计数；`source=kernel`（运行时补派）、
    `deduped`（复用上次结果）、`remaining_turns` 逐行标注；窗口化 60 行 + 「加载更早的」逐段翻到底，
    既不截断也不把上千行一次塞进 DOM。`ModelTurnRows` 同样取消 `slice(-6)`。
  - 后端 `subagent_tool` 补 `kind` / `run_id` / `seq_in_run`，前端 schema 同步（`ToolDetail.seq_in_run`）。
- **需求会话 ask_user（§15.5 / M5c）**——需求对话没有工具轮，因此复用同一个 `ask_user` 服务与同一套
  `question`/`answer` 事件语义，只把调用点放在 service：
  - LLM 结构化输出带 `ask_user` 指令 → `RequirementsService._ask_from_llm` 建问题
    （owner=`requirements`，事件流键 `requirements:<session_id>`，与任务事件按前缀隔离）→ 会话进
    `waiting_user`，本轮不再推进（不猜、不替用户选）；
  - `POST /api/requirements/{sid}/answer` 回答问题 → 答案还原成一句人话喂回对话 → agent 继续；
    `GET /{sid}/questions?status=pending|all`、`GET /{sid}/events?since_seq=` 供刷新/重连重建；
  - `waiting_user` 下用户直接打字 = 自由文本回答（自动应答后回到 gathering），不留死胡同；
  - 错误码对齐任务侧：404（跨会话答别人的问题）/ 409（已答、已取消）/ 410（超时或服务重启）/ 400；
  - 前端 `AgentPanel` 挂 `TaskQuestionCard`（`askerLabel="需求 agent"`），SSE `question` 帧到达即出卡，
    回答后回显 agent 续跑结果并刷新待答列表。需求会话**不访问任务事件 API**。
- **导出重试拆分（§20 M3 尾巴）**：提交时冻结时间线快照 + FNV-1a 指纹；失败卡片只给「重试本次提交」
  （跑快照），时间线改过才额外给「用当前时间线重新提交」，并把两侧指纹显示出来。旧实现永远跑
  "当前时间线"却叫重试，改完时间线再点重试会让人以为在重试、其实跑的是新内容。
- **回归**：后端 `pytest` **4924 passed / 4 skipped / 1 xfailed**（新增
  `test_requirements_ask_user.py` 9 例、`test_requirements_ask_user_api.py` 7 例）；
  前端 `tsc` 干净、`vitest` **136 文件 / 1171 例**（新增 `CopilotToolTransparency` 8 例、
  `exportSnapshot` 7 例、`AgentPanel` +5 例）；`stage_tool_failures` 失败率门禁仍绿。
- **顺手修的环境脆弱点**：`test_dead_extra_keys.py::test_plumbing_audit_passes_strict` 用 `text=True`
  按 locale（Windows 中文机 cp936）解码子进程的 UTF-8 输出，报告里一有中文就 `UnicodeDecodeError`
  → `stdout=None` → `json.loads(None)`；改为父进程显式按 UTF-8 解码。
- **未完成**：M6（动画提示词 v2：九段式模板、S1–S12 风格模块、lint、逐类改写）尚未开始；
  `TaskToolTrace` 已有后端 `limit/offset` 分页，本轮未再加虚拟滚动。

## 执行记录（2026-10-30 · 第 10 轮 · M6 动画提示词 v2）

- **九段式模板**：`prompts/text/_base.md`、`prompts/types/_base.md`（角色与交付物 / 硬约束 /
  常量表 / 分段关键帧公式与 easing 词典 / 风格模块引用 / 不要清单 / 自检清单 / 输出格式 /
  失败回退）。类型提示词保留七段旧标题（`text_specs.load_text_prompt` 与
  `validate_text_registry` 按这七段切分），另补 Constants / Output / Self-check / Fallback 四段。
- **风格模块 S1–S12**：`prompts/style_catalog.json`（id/name/series/keywords_zh/keywords_en/
  palette_hex/motion_tags/composition_tags/suitable_types/avoid_types/forbidden/
  renderer_paths/reference_source/prompt_version/status）+ `prompts/styles/S1–S12.md` 正文
  （视觉语言 / 关键参数倾向 / 适用与回避 / 禁止项 / 使用纪律）。
  `scripts/styles.py` 提供 `search` / `show` / `match`：默认只给 ≤3 条摘要、排序确定
  （分数→编号）、`avoid_types` 过滤、`show` 才读正文——不把 12 份模块正文一次拼进 prompt。
- **逐类改写（10 份）**：六类文字（emphasis_keyword / reveal_line / quote_block /
  label_callout / number_punch / title_stack）+ 四类 MG（bar_chart / line_chart /
  comparison / flow_chart）。要点：
  - drawtext 提示词只写 `keyframes_expr.EASING_EXPR` 的 21 个名字；`back-out`/`bounce`/
    `elastic-out` 明确标注为「MG 名字，drawtext 不认」，且只出现在反例行里；
  - 粒子 / 模糊 / 遮罩 / 扫光 / 独立卡片层一律标注「MG HTML 路径」或「本期不落」，
    并在 `docs/animation_system.md` 新增「渲染侧需求登记」表逐条列出所需能力；
  - Example 换成**可渲染 JSON**：扁平关键帧（不再用 `properties` 包裹，`repair_mg_json`
    会把它当未知 props 删掉）、每帧声明完整动画属性、带 `time=0` 起始帧；
  - 每份提示词声明 `style_modules: [S…]`，并给了"收束"阶段让动画覆盖率 ≥45% 且末段静止 ≥0.6s。
- **lint 进 CI**：`python -m clipwright.animation.prompts.lint_prompts --strict`
  （`.github/workflows/ci.yml` 新增一步）。检查段落齐全、反例行豁免（"不要写 X"不算违规）、
  占位符残留、duration/元素/字数上限、按渲染路径的 easing 白名单、drawtext 不支持能力词
  必须标注、Example 必须过 `validate_mg_json`、风格模块引用必须存在。
  现状 **0 error / 136 warning**：未迁移的老 MG 类型只报 warning，迁移后自动升级为 error
  （`MIGRATED_TYPES`）。
- **指标回归** `tests/clipwright/test_prompt_metrics.py`：把每份提示词自己的 Example 喂真实
  校验链路——motion coverage ≥45%、末段静止 ≥0.6s、`position` 不落字幕带（底边 ≤920px）、
  单行 ≤14 字、元素 ≤3、关键帧升序且在片段内、同输入两次结果一致、
  `repair_mg_json` 不得改动结构（修复率 = 0）。
- **顺手修不稳定测试**：`test_render_parallel::test_failed_mov_does_not_abort_others` 的
  2.5s 绝对阈值在 Windows 上连跑三次就随机红（实测 2.76s），改成相对串行基线的 80%
  （4×1.0×0.8=3.2s），仍能抓"串行化 / gather 悬挂"。
- **回归**：后端 `pytest` **4966 passed / 4 skipped / 1 xfailed**（+41：lint 29、指标 12）；
  提示词 lint strict 0 error；前端 `tsc` 干净、`vitest` 136 文件 / 1171 例。

## 执行记录（2026-10-30 · 第 11 轮 · 收口：progress 落 INDEX + 全链路 E2E）

- **`progress` 补上「进度时间线」落 INDEX.md（§14）**：旧实现只在 `progress.md` 里写时间线，
  而 `stage_report.update_index()` 是**整文件重写**——用户刚在 INDEX.md 看到的进度会被下一次
  报告写入抹掉（"看得见但会消失"的假功能）。现在：
  - `progress.timeline_lines()` 从 `progress.md` 读回最近 N 条；
  - `update_index()` 重写时合并出 `## 进度时间线` 小节（两份文件各自维护，谁也不覆盖谁）；
  - `list_reports()` 不再把 `progress.md` 当成阶段报告（否则报告数虚高、还能被当输入读）。
  新增 `tests/clipwright/test_progress_index.py`（4 例）钉住"反复重写也不丢时间线"。
- **完整前端 E2E（通过）**：`npx playwright test e2e/final-flow.spec.ts`，19.8 分钟一次跑完：
  首页新建制作 → 创意简报 → 「确认简报」→ 规划书 → 「确认实施」→ `/api/requirements/proceed`
  返回 `task_28fd9249b4` → 事件流 31 条（`subagent_tool` 2 条）→ `/tool-calls` 61 条轨迹
  → 任务 `done`（模型自己核了 audio 是否真落盘）→ 时间线回写项目（5 轨 / 43 片段 / 60s）
  → 导出「加入渲染队列」→ 渲染完成 → 「下载成片」4.2 MB →
  `ffprobe`：h264 1920x1080 30fps **58.0s**，页面零未捕获异常。
  （第一次跑在 18:34 后端 socket 不再 accept 而挂死：进程活着、日志停在 `vision_llm`，
  重启后端后同一脚本一次通过；挂死现象与代码改动无对应关系，已在日志里留痕。）
- **回归**：后端 `pytest` 全绿（含新增 progress/INDEX 4 例、prompt lint 29 例、指标 12 例）；
  前端 `tsc` 干净、`vitest` 136 文件 / 1171 例；`stage_tool_failures` 门禁绿；
  事件契约 29 种前后端同序（跨仓测试 `tests/test_event_contract.py` ↔ `eventSchema.test.ts`）。
