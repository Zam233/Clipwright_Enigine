"""Web 搜索 provider 抽象（PRD `docs/web-search-fetch-prd-2026-10-30.md` M1）。

背景（2026-10-30 实测）：动画 Agent 只在 `_DATA_FACT_RE` 命中时调 `WebSearchService.search()`，
取 3 条摘要注入 prompt；**不抓正文、不落素材**；`web_search_api_key` 为空时静默跳过；
provider 只有 bocha/baidu 两家，且都依赖付费 key。

本模块把「搜索」拆成可插拔 provider：
- 付费 API：`bocha` / `baidu` / `tavily` / `serper` / `bing`（各自 key，缺省回落到 `web_search_api_key`）
- **本地抓取**：`local`（DuckDuckGo HTML → Bing HTML），**不需要任何 key**
链路由 `settings.web_search_provider` 决定主 provider，其余已配置的按固定顺序兜底，
`local` 恒在最后（受 `settings.web_search_allow_local` 控制）。

约定：任何 provider 失败都不上抛；全部失败返回 `[]`，由调用方登记降级
（`note_degradation("web_search_unavailable")`），而不是静默当作"搜过但没结果"。
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Iterable

import httpx

from clipwright.config import logger, settings

DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; ClipWrightBot/1.0)"
_DEFAULT_HEADERS = {"User-Agent": DEFAULT_USER_AGENT}

#: 付费 provider 的端点（可用 `web_search_base_url` 覆盖主 provider）
ENDPOINTS = {
    # DeepSeek 自研搜索引擎（2026-10-31 用户指定；与平台 API 同一 Bearer 鉴权）
    "deepseek": "https://api.deepseek.com/v1/tools/search",
    "bocha": "https://api.bochaai.com/v1/web-search",
    "baidu": "https://qianfan.baidubce.com/v2/ai_search/web_search",
    "tavily": "https://api.tavily.com/search",
    "serper": "https://google.serper.dev/search",
    "bing": "https://api.bing.microsoft.com/v7.0/search",
}
#: DeepSeek 网页抓取（返回 Markdown 正文）——比本地正则抽正文干净得多
DEEPSEEK_FETCH_ENDPOINT = "https://api.deepseek.com/v1/tools/fetch"
#: 本地抓取入口（按顺序尝试）
_LOCAL_ENDPOINTS = (
    "https://html.duckduckgo.com/html/?q={q}",
    "https://www.bing.com/search?q={q}",
)
_BAIDU_AUTH_PREFIX = "bce-v3/ALTAK-"

#: provider 兜底顺序（不含 primary；local 恒最后，单独判断）
_FALLBACK_ORDER = ("deepseek", "bocha", "baidu", "tavily", "serper", "bing", "local")

#: 结果链接/标题的解析（DuckDuckGo HTML / Bing HTML 通用形态）
_HTML_ITEM_RES = (
    re.compile(
        r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>',
        re.S | re.I,
    ),
    re.compile(r'<h2[^>]*>\s*<a[^>]+href="(?P<url>https?://[^"]+)"[^>]*>(?P<title>.*?)</a>',
               re.S | re.I),
)
_TAG_RE = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    label: str
    requires_key: bool
    #: settings 上该 provider 专属 key 的属性名；空 = 用全局 `web_search_api_key`
    key_attr: str = ""


PROVIDERS: dict[str, ProviderSpec] = {
    "deepseek": ProviderSpec("deepseek", "DeepSeek 搜索", True, "web_search_deepseek_api_key"),
    "bocha": ProviderSpec("bocha", "Bocha", True),
    "baidu": ProviderSpec("baidu", "百度千帆", True),
    "tavily": ProviderSpec("tavily", "Tavily", True, "web_search_tavily_api_key"),
    "serper": ProviderSpec("serper", "Serper (Google)", True, "web_search_serper_api_key"),
    "bing": ProviderSpec("bing", "Bing Web Search", True, "web_search_bing_api_key"),
    "local": ProviderSpec("local", "本地抓取（DuckDuckGo/Bing）", False),
}


#: 允许回落到全局 `web_search_api_key` 的历史 provider。
#: 一个 bocha key 不该让 tavily/serper/bing 也算"已配置"——那会凭空多出三条必失败的链路
#: （实测 `test_both_providers_fail_returns_empty` 里 post 被调 4 次）。
_LEGACY_GLOBAL_KEY_PROVIDERS = ("bocha", "baidu")


def provider_key(spec: ProviderSpec) -> str:
    """该 provider 的 API key（专属优先；只有 bocha/baidu 回落到全局 key）。"""
    if not spec.requires_key:
        return ""
    if spec.id == "deepseek":
        own = str(getattr(settings, spec.key_attr, "") or "").strip()
        if own:
            return own
        # DeepSeek 搜索与平台 API 同一账号：主 LLM 就是 DeepSeek 时直接复用它的 key
        # （否则用户得为"搜索"再配一遍同一个 key，配置体验说不通）。
        base = f"{getattr(settings, 'llm_base_url', '') or ''} " \
               f"{getattr(settings, 'llm_provider', '') or ''}".lower()
        if "deepseek" in base:
            return str(getattr(settings, "llm_api_key", "") or "").strip()
        return ""
    if spec.key_attr:
        own = str(getattr(settings, spec.key_attr, "") or "").strip()
        if own:
            return own
        if spec.id not in _LEGACY_GLOBAL_KEY_PROVIDERS:
            return ""
    return str(getattr(settings, "web_search_api_key", "") or "").strip()


def primary_provider() -> str:
    raw = str(getattr(settings, "web_search_provider", "bocha") or "bocha").strip().lower()
    return raw if raw in PROVIDERS else "bocha"


def allow_local() -> bool:
    return bool(getattr(settings, "web_search_allow_local", True))


def is_enabled() -> bool:
    """总开关（`enable_web_search`）。关着就一个 provider 都不许用。"""
    return bool(getattr(settings, "enable_web_search", False))


def configured_providers() -> list[str]:
    """当前真正可用的 provider（不含总开关判断）。"""
    out: list[str] = []
    for pid, spec in PROVIDERS.items():
        if not spec.requires_key:
            if allow_local():
                out.append(pid)
            continue
        if provider_key(spec):
            out.append(pid)
    return out


def provider_chain() -> list[str]:
    """实际尝试顺序：主 provider → 其余付费 → local。总开关关闭时返回空。"""
    if not is_enabled():
        return []
    available = set(configured_providers())
    primary = primary_provider()
    ordered: list[str] = []
    if primary in available:
        ordered.append(primary)
    for pid in _FALLBACK_ORDER:
        if pid == "local":
            continue
        if pid in available and pid not in ordered:
            ordered.append(pid)
    if "local" in available:
        ordered.append("local")
    return ordered


def _timeout() -> float:
    try:
        return max(float(getattr(settings, "web_search_timeout", 15) or 15), 1.0)
    except (TypeError, ValueError):
        return 15.0


def _endpoint(provider: str) -> str:
    base = getattr(settings, "web_search_base_url", None)
    # 只对"当前主 provider"允许整体覆盖端点（避免一份 base_url 污染所有 provider）
    if base and provider == primary_provider():
        return str(base)
    return ENDPOINTS.get(provider, "")


def _normalize(item: dict[str, Any]) -> dict[str, Any]:
    def first(*keys: str) -> Any:
        for key in keys:
            value = item.get(key)
            if value not in (None, ""):
                return value
        return None

    try:
        score = float(item["score"]) if item.get("score") is not None else 0.0
    except (TypeError, ValueError):
        score = 0.0
    return {
        "title": str(first("title", "name") or ""),
        "url": str(first("url", "link", "href") or ""),
        "snippet": str(first("snippet", "summary", "abstract", "description", "content")
                       or "")[:2000],
        "score": score,
    }


def _find_list(node: Any) -> list[Any] | None:
    if isinstance(node, list):
        return node
    if isinstance(node, dict):
        for key in ("items", "list", "results", "value", "webPages", "organic"):
            value = node.get(key)
            if isinstance(value, list):
                return value
        for value in node.values():
            if isinstance(value, list):
                return value
    return None


def _clean_html_text(raw: str) -> str:
    return re.sub(r"\s+", " ", _TAG_RE.sub("", raw or "")).strip()


async def _search_deepseek(query: str, count: int,
                           client_factory: Callable[..., Any]) -> list[dict]:
    """DeepSeek 自研搜索（`POST /v1/tools/search`，2026-10-31 用户指定）。

    响应字段比别家丰富（`rerank_score`/`summary`/`content`/`authority`/`date`），
    这里**保留**它们：`content` 是 `need_content=true` 时返回的正文，能给 agent 直接用；
    `summary`（600–800 字）比 `snippet` 更适合喂模型。
    """
    key = provider_key(PROVIDERS["deepseek"])
    if not key:
        raise RuntimeError("DeepSeek 搜索缺少 API key（DEEPSEEK_API_KEY 或 web_search_deepseek_api_key）")
    payload = {
        "query": str(query)[:128],
        "count": max(1, min(int(count or 5), 50)),
        "need_content": True,
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    async with client_factory(timeout=_timeout()) as client:
        resp = await client.post(_endpoint("deepseek"), json=payload, headers=headers)
        if resp.status_code >= 400:
            raise RuntimeError(_deepseek_error(resp))
        data = resp.json()
    items = data.get("results") if isinstance(data, dict) else None
    out: list[dict] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        url = str(it.get("url") or "").strip()
        if not url:
            continue
        try:
            score = float(it.get("rerank_score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        out.append({
            "title": str(it.get("title") or ""),
            "url": url,
            "snippet": str(it.get("snippet") or "")[:2000],
            "summary": str(it.get("summary") or "")[:2000],
            "content": str(it.get("content") or "")[:20000],
            "score": score,
            "authority": it.get("authority"),
            "date": it.get("date"),
            "site_name": it.get("site_name"),
        })
    return out


def _deepseek_error(resp: Any) -> str:
    """把 DeepSeek 的 OpenAI 风格错误翻成人话（401/402/429 是运维最常撞的三种）。"""
    detail = ""
    try:
        body = resp.json()
        detail = str(((body or {}).get("error") or {}).get("message") or "")[:200]
    except Exception:  # noqa: BLE001
        detail = str(getattr(resp, "text", "") or "")[:200]
    hints = {
        400: "请求不合法",
        401: "API Key 缺失或无效",
        402: "账号余额不足",
        403: "安全策略拒绝",
        404: "没有可抓取的内容",
        429: "超过 QPS 限制",
        500: "服务端错误",
        504: "下游超时",
    }
    hint = hints.get(int(getattr(resp, "status_code", 0) or 0), "调用失败")
    return f"HTTP {getattr(resp, 'status_code', '?')} {hint}：{detail}"


async def _search_bocha(query: str, count: int, client_factory: Callable[..., Any]) -> list[dict]:
    payload = {"query": query, "count": count, "freshness": "oneMonth"}
    headers = {"Authorization": f"Bearer {provider_key(PROVIDERS['bocha'])}",
               "Content-Type": "application/json"}
    async with client_factory(timeout=_timeout()) as client:
        resp = await client.post(_endpoint("bocha"), json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    items: list[Any] = []
    for path in (("data", "data", "webPages", "value"), ("data", "webPages", "value"),
                 ("webPages", "value")):
        node: Any = data
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
        if isinstance(node, list):
            items = node
            break
    return [_normalize(it) for it in items if isinstance(it, dict)]


async def _search_baidu(query: str, count: int, client_factory: Callable[..., Any]) -> list[dict]:
    key = provider_key(PROVIDERS["baidu"])
    if key and not key.startswith(_BAIDU_AUTH_PREFIX):
        key = f"{_BAIDU_AUTH_PREFIX}{key}"
    payload = {
        "messages": [{"role": "user", "content": query}],
        "search_source": "baidu_search_v2",
        "resource_type_filter": [{"type": "web", "top_k": count}],
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
               "X-Request-Id": str(uuid.uuid4())}
    async with client_factory(timeout=_timeout()) as client:
        resp = await client.post(_endpoint("baidu"), json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    items: list[Any] = []
    if isinstance(data, dict):
        for key_name in ("references", "result", "results", "webSearchResult", "searchResult", "data"):
            found = _find_list(data.get(key_name))
            if found:
                items = found
                break
    return [_normalize(it) for it in items if isinstance(it, dict)]


async def _search_tavily(query: str, count: int, client_factory: Callable[..., Any]) -> list[dict]:
    payload = {"api_key": provider_key(PROVIDERS["tavily"]), "query": query,
               "max_results": count, "search_depth": "basic"}
    async with client_factory(timeout=_timeout()) as client:
        resp = await client.post(_endpoint("tavily"), json=payload)
        resp.raise_for_status()
        data = resp.json()
    items = data.get("results") if isinstance(data, dict) else None
    return [_normalize(it) for it in (items or []) if isinstance(it, dict)]


async def _search_serper(query: str, count: int, client_factory: Callable[..., Any]) -> list[dict]:
    headers = {"X-API-KEY": provider_key(PROVIDERS["serper"]), "Content-Type": "application/json"}
    async with client_factory(timeout=_timeout()) as client:
        resp = await client.post(_endpoint("serper"), json={"q": query, "num": count},
                                 headers=headers)
        resp.raise_for_status()
        data = resp.json()
    items = data.get("organic") if isinstance(data, dict) else None
    return [_normalize(it) for it in (items or []) if isinstance(it, dict)]


async def _search_bing(query: str, count: int, client_factory: Callable[..., Any]) -> list[dict]:
    headers = {"Ocp-Apim-Subscription-Key": provider_key(PROVIDERS["bing"])}
    async with client_factory(timeout=_timeout()) as client:
        resp = await client.get(_endpoint("bing"), params={"q": query, "count": count},
                                headers=headers)
        resp.raise_for_status()
        data = resp.json()
    pages = (data.get("webPages") or {}).get("value") if isinstance(data, dict) else None
    return [_normalize(it) for it in (pages or []) if isinstance(it, dict)]


def _parse_local_html(html: str, count: int) -> list[dict]:
    out: list[dict] = []
    for pattern in _HTML_ITEM_RES:
        for match in pattern.finditer(html or ""):
            url = (match.group("url") or "").strip()
            if not url.startswith("http"):
                continue
            out.append({
                "title": _clean_html_text(match.group("title"))[:200],
                "url": url,
                "snippet": "",
                "score": 0.0,
            })
            if len(out) >= count:
                return out
        if out:
            return out
    return out


async def _search_local(query: str, count: int, client_factory: Callable[..., Any]) -> list[dict]:
    """无密钥搜索：抓 DuckDuckGo / Bing 的 HTML 结果页（失败即空）。"""
    from urllib.parse import quote_plus

    last_error = ""
    for template in _LOCAL_ENDPOINTS:
        url = template.format(q=quote_plus(query))
        try:
            async with client_factory(timeout=min(_timeout(), 10.0),
                                      follow_redirects=True) as client:
                resp = await client.get(url, headers=_DEFAULT_HEADERS)
                resp.raise_for_status()
                html = resp.text
            results = _parse_local_html(html, count)
            if results:
                return results
            last_error = f"{url} 未解析出结果"
        except Exception as exc:  # noqa: BLE001 — 本地抓取失败继续下一个入口
            last_error = f"{url}: {exc}"
            logger.debug("本地搜索入口失败: %s", last_error)
    if last_error:
        logger.debug("本地搜索全部失败: %s", last_error)
    return []


_SEARCHERS: dict[str, Callable[..., Any]] = {
    "deepseek": _search_deepseek,
    "bocha": _search_bocha,
    "baidu": _search_baidu,
    "tavily": _search_tavily,
    "serper": _search_serper,
    "bing": _search_bing,
    "local": _search_local,
}


@dataclass
class SearchOutcome:
    results: list[dict[str, Any]]
    provider: str = ""
    attempts: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.attempts is None:
            self.attempts = []


async def search(
    query: str,
    count: int = 5,
    *,
    client_factory: Callable[..., Any] | None = None,
) -> SearchOutcome:
    """按 provider 链搜索；返回结果与实际命中的 provider（全失败时 provider 为空）。"""
    factory = client_factory or httpx.AsyncClient
    chain = provider_chain()
    outcome = SearchOutcome(results=[])
    if not chain:
        return outcome
    for pid in chain:
        searcher = _SEARCHERS.get(pid)
        if searcher is None:
            continue
        outcome.attempts.append(pid)
        try:
            results = await searcher(query, count, factory)
        except Exception as exc:  # noqa: BLE001 — 单个 provider 失败不影响链
            logger.warning("web search provider %s 失败: %s", pid, exc)
            continue
        if results:
            outcome.results = results
            outcome.provider = pid
            return outcome
    logger.warning("web search 全部 provider 无结果（attempts=%s）: query=%.80s",
                   ",".join(outcome.attempts), query)
    return outcome


def resolve_providers(ids: Iterable[str] | None = None) -> list[str]:
    """把一组 id 过滤成当前可用（供测试与诊断）。"""
    available = set(configured_providers())
    return [pid for pid in (ids or PROVIDERS) if pid in available]


__all__ = [
    "ENDPOINTS", "PROVIDERS", "ProviderSpec", "SearchOutcome",
    "allow_local", "configured_providers", "is_enabled", "primary_provider",
    "provider_chain", "provider_key", "resolve_providers", "search",
]
